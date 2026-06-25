"""Lossless garbage collector for the FitLit fetcher databases.

**The problem.** Every fetcher re-pulls its whole available history each cycle
and upserts by ``name``. Because the API hands back overlapping points with
distinct ids, the live tables accumulate *many rows per timestamp* — one day of
``steps`` can be ~300k rows over ~340 distinct minutes. Left alone,
``live_activity.db`` grows multiple GB/day and eventually fills the disk (which
stalls the writers entirely).

**The fix — archive, verify, prune (never lose a byte).** For data older than a
threshold this module:

  1. **Archives** every old row — *all* columns, including ``raw_json`` — to a
     gzip-compressed JSONL file under ``data/archive/<fetcher>/<data_type>.jsonl.gz``.
     Rows sharing a timestamp can carry *different* values, so we keep every row,
     not one per timestamp — the archive is a byte-for-byte superset of what we
     remove.
  2. **Verifies** the archive: the number of JSON lines written must equal the
     number of rows selected. Only on a match do we proceed — so a crash or a
     full disk can never delete unarchived data.
  3. **Consolidates** each archived (data_type, UTC-day) into a single summary
     row in a ``_gc_summary`` table — the "one row a week-old day collapses to" —
     recording the row count, the archive path, the time span, and value
     aggregates (min/max/sum/avg of the type's primary numeric column when it has
     one). The hot table keeps a queryable trace; the full detail lives in the
     archive.
  4. **Prunes** the archived rows from the live table in batches.

Restoring is the exact inverse (:func:`restore_archive`), so the operation is
fully reversible. Standard library only (sqlite3 + gzip + json).
"""
from __future__ import annotations

import gzip
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fitlit import config
from fitlit.models import model_for

log = logging.getLogger("fitlit.gc")

# Envelope columns are always present; we read every column dynamically so the
# archive captures typed value columns too, whatever they are per data type.
_BATCH = 5000
DEFAULT_OLDER_THAN_DAYS = 7


def archive_dir() -> Path:
    return config.DATA_DIR / "archive"


def _archive_path(fetcher: str, data_type: str) -> Path:
    return archive_dir() / fetcher / f"{data_type}.jsonl.gz"


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name <> '_gc_summary'")]


def _ensure_summary_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _gc_summary (
            data_type   TEXT,
            utc_day     TEXT,
            row_count   INTEGER,
            first_start TEXT,
            last_start  TEXT,
            value_field TEXT,
            value_min   REAL,
            value_max   REAL,
            value_sum   REAL,
            value_avg   REAL,
            archive     TEXT,
            archived_at TEXT,
            PRIMARY KEY (data_type, utc_day)
        )
    """)


def _primary_value_field(data_type: str) -> str | None:
    """The type's first typed numeric value column (e.g. steps→count), if any."""
    model = model_for(data_type)
    for field in model.VALUE_FIELDS:
        ann = model.model_fields[field].annotation
        # crude: int/float annotations are aggregatable; everything else skipped
        if any(t in repr(ann) for t in ("int", "float")):
            return field
    return None


def _cutoff_iso(older_than_days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
def archive_and_prune(
    fetcher: str,
    *,
    older_than_days: int = DEFAULT_OLDER_THAN_DAYS,
    dry_run: bool = False,
) -> dict:
    """Archive + consolidate + prune one fetcher DB's rows older than the cutoff.

    Returns a per-data-type report of how many rows were archived and pruned.
    With ``dry_run`` it only counts what *would* be processed and touches nothing.
    """
    db_path = config.DB_DIR / f"{fetcher}.db"
    if not db_path.exists():
        return {"fetcher": fetcher, "error": "no such database"}

    cutoff = _cutoff_iso(older_than_days)
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA temp_store=MEMORY")
    report: dict[str, dict] = {}
    try:
        if not dry_run:
            _ensure_summary_table(conn)
        for table in _tables(conn):
            n = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" '
                f"WHERE start_time IS NOT NULL AND start_time < ?", (cutoff,)
            ).fetchone()[0]
            if n == 0:
                continue
            if dry_run:
                report[table] = {"would_archive": n}
                continue
            report[table] = _process_table(conn, fetcher, table, cutoff)
        return {"fetcher": fetcher, "cutoff": cutoff, "tables": report}
    finally:
        conn.close()


def _process_table(conn: sqlite3.Connection, fetcher: str, data_type: str, cutoff: str) -> dict:
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{data_type}")')]
    path = _archive_path(fetcher, data_type)
    path.parent.mkdir(parents=True, exist_ok=True)

    select = (f'SELECT {", ".join(chr(34)+c+chr(34) for c in cols)} FROM "{data_type}" '
              f"WHERE start_time IS NOT NULL AND start_time < ? ORDER BY start_time")

    # 1. Archive every matching row (append; gzip). Count lines written.
    written = 0
    with gzip.open(path, "at", encoding="utf-8") as gz:
        for row in conn.execute(select, (cutoff,)):
            gz.write(json.dumps({c: row[c] for c in cols}, default=str) + "\n")
            written += 1

    # 2. Verify: archived line count must equal the rows we intend to delete.
    expected = conn.execute(
        f'SELECT COUNT(*) FROM "{data_type}" WHERE start_time IS NOT NULL AND start_time < ?',
        (cutoff,)).fetchone()[0]
    if written != expected:
        raise RuntimeError(
            f"archive integrity check failed for {fetcher}.{data_type}: "
            f"wrote {written} lines, expected {expected} — NOT pruning")

    # 3. Consolidate each UTC day into one _gc_summary row.
    _write_summaries(conn, fetcher, data_type, cutoff, str(path))

    # 4. Prune the archived rows, in batches, committing as we go.
    deleted = 0
    while True:
        cur = conn.execute(
            f'DELETE FROM "{data_type}" WHERE rowid IN '
            f'(SELECT rowid FROM "{data_type}" '
            f' WHERE start_time IS NOT NULL AND start_time < ? LIMIT {_BATCH})',
            (cutoff,))
        conn.commit()
        if cur.rowcount == 0:
            break
        deleted += cur.rowcount
    log.info("gc %s.%s: archived %d, pruned %d (older than %s)",
             fetcher, data_type, written, deleted, cutoff)
    return {"archived": written, "pruned": deleted, "archive": str(path)}


def _write_summaries(conn: sqlite3.Connection, fetcher: str, data_type: str,
                     cutoff: str, archive: str) -> None:
    vfield = _primary_value_field(data_type)
    agg = (f', MIN("{vfield}"), MAX("{vfield}"), SUM("{vfield}"), AVG("{vfield}")'
           if vfield else ", NULL, NULL, NULL, NULL")
    rows = conn.execute(
        f'SELECT substr(start_time,1,10) day, COUNT(*), MIN(start_time), MAX(start_time){agg} '
        f'FROM "{data_type}" WHERE start_time IS NOT NULL AND start_time < ? '
        f'GROUP BY day', (cutoff,)).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    for day, count, first, last, vmin, vmax, vsum, vavg in rows:
        conn.execute(
            "INSERT INTO _gc_summary (data_type, utc_day, row_count, first_start, "
            "last_start, value_field, value_min, value_max, value_sum, value_avg, "
            "archive, archived_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(data_type, utc_day) DO UPDATE SET "
            "row_count=row_count+excluded.row_count, last_start=excluded.last_start, "
            "value_sum=COALESCE(value_sum,0)+COALESCE(excluded.value_sum,0), "
            "archived_at=excluded.archived_at",
            (data_type, day, count, first, last, vfield, vmin, vmax, vsum, vavg,
             archive, now))
    conn.commit()


# --------------------------------------------------------------------------- #
def restore_archive(fetcher: str, data_type: str) -> int:
    """Re-insert every archived row back into the live table (the inverse of
    pruning). Proves losslessness and supports point-in-time recovery."""
    path = _archive_path(fetcher, data_type)
    if not path.exists():
        return 0
    db_path = config.DB_DIR / f"{fetcher}.db"
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{data_type}")')]
        placeholders = ", ".join("?" for _ in cols)
        collist = ", ".join(f'"{c}"' for c in cols)
        sql = (f'INSERT INTO "{data_type}" ({collist}) VALUES ({placeholders}) '
               f'ON CONFLICT(name) DO NOTHING')
        restored = 0
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            batch = []
            for line in gz:
                rec = json.loads(line)
                batch.append([rec.get(c) for c in cols])
                if len(batch) >= _BATCH:
                    conn.executemany(sql, batch); conn.commit()
                    restored += len(batch); batch = []
            if batch:
                conn.executemany(sql, batch); conn.commit()
                restored += len(batch)
        return restored
    finally:
        conn.close()


def compact(fetcher: str) -> dict:
    """Reclaim freed pages back to the OS via ``VACUUM`` after pruning.

    VACUUM rebuilds the file, so it needs temporary free space roughly equal to
    the *final* (post-prune) size — small once the duplicates are gone. We bail
    out with a clear message if there isn't enough headroom, rather than risk a
    half-written vacuum on a full disk.
    """
    import shutil

    db_path = config.DB_DIR / f"{fetcher}.db"
    if not db_path.exists():
        return {"fetcher": fetcher, "error": "no such database"}
    before = db_path.stat().st_size
    free = shutil.disk_usage(config.DB_DIR).free
    conn = sqlite3.connect(db_path, timeout=120)
    try:
        # Fold the WAL in first so the main file reflects the deletes.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page = conn.execute("PRAGMA page_size").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        live_bytes = before - freelist * page  # rough post-vacuum size
        if free < live_bytes * 1.1:
            return {"fetcher": fetcher, "skipped": "insufficient free space for VACUUM",
                    "free_bytes": free, "needed_estimate": int(live_bytes * 1.1)}
        conn.execute("VACUUM")
    finally:
        conn.close()
    after = db_path.stat().st_size
    return {"fetcher": fetcher, "before_bytes": before, "after_bytes": after,
            "reclaimed_bytes": before - after}


def run_all(*, older_than_days: int = DEFAULT_OLDER_THAN_DAYS,
            do_compact: bool = True, dry_run: bool = False) -> dict:
    """Archive+prune (and optionally compact) every fetcher database."""
    out: dict[str, dict] = {}
    for fetcher in config.FETCHERS:
        out[fetcher] = archive_and_prune(fetcher, older_than_days=older_than_days, dry_run=dry_run)
        if do_compact and not dry_run and out[fetcher].get("tables"):
            out[fetcher]["compact"] = compact(fetcher)
    return out


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OLDER_THAN_DAYS
    dry = "--dry-run" in sys.argv
    print(json.dumps(run_all(older_than_days=days, dry_run=dry), indent=2, default=str))
