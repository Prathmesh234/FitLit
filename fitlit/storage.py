"""SQLite persistence — several databases, one per fetcher.

Layout (all under ``data/db/``):

    live_activity.db   heart.db   daily_summaries.db   body.db
    sleep.db           nutrition.db   cardiac.db        location.db

* **One database per fetcher.** Each fetcher runs as its own process, so giving
  each its own file means the eight cron scripts never contend on SQLite's
  single-writer lock — they write to different files concurrently.
* **One table per data type** inside that database. The table's columns are
  generated from the Pydantic model in ``fitlit.models`` (single source of
  truth) — a typed envelope, any typed value columns, plus ``data_json`` /
  ``raw_json`` that preserve every field forever.
* **Upsert on ``name``** (the API's globally-unique data-point id), so polling
  the same window every 60s never creates duplicates; an edited point (new
  ``updateTime``) overwrites the prior row.

WAL mode + a busy timeout keep it durable and tolerant under a 24/7 cadence.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

from fitlit import config
from fitlit.models import DataPoint, model_for, sqlite_type_for

# Envelope columns present on every table, in order. (column, sqlite type)
_ENVELOPE_COLUMNS: list[tuple[str, str]] = [
    ("name", "TEXT PRIMARY KEY"),
    ("data_type", "TEXT"),
    ("start_time", "TEXT"),
    ("end_time", "TEXT"),
    ("start_utc_offset", "TEXT"),
    ("end_utc_offset", "TEXT"),
    ("recording_method", "TEXT"),
    ("platform", "TEXT"),
    ("device_name", "TEXT"),
    ("update_time", "TEXT"),
    ("fetched_at", "TEXT"),
    ("data_json", "TEXT"),
    ("raw_json", "TEXT"),
]
_JSON_COLUMNS = {"data_json", "raw_json"}


def db_path_for_fetcher(fetcher_name: str):
    return config.DB_DIR / f"{fetcher_name}.db"


def _table_name(data_type: str) -> str:
    """data_type is a safe camelCase identifier from our catalogue; quote anyway."""
    return data_type


def _columns_for(data_type: str) -> list[tuple[str, str]]:
    model = model_for(data_type)
    cols = list(_ENVELOPE_COLUMNS)
    for field in model.VALUE_FIELDS:
        cols.append((field, sqlite_type_for(model, field)))
    return cols


class Database:
    """A single per-fetcher SQLite database. Cheap to open; not thread-shared."""

    def __init__(self, fetcher_name: str) -> None:
        self.fetcher_name = fetcher_name
        config.DB_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path_for_fetcher(fetcher_name), timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._tables: set[str] = set()

    # ------------------------------------------------------------------ #
    def ensure_table(self, data_type: str) -> None:
        table = _table_name(data_type)
        if table in self._tables:
            return
        cols = _columns_for(data_type)
        coldefs = ", ".join(f'"{name}" {ctype}' for name, ctype in cols)
        self.conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({coldefs})')
        # Indexes that matter for a lifetime of time-series queries.
        self.conn.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{table}_start" ON "{table}" (start_time)')
        self.conn.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{table}_fetched" ON "{table}" (fetched_at)')
        self.conn.commit()
        self._tables.add(table)

    # ------------------------------------------------------------------ #
    def upsert_points(self, data_type: str, raw_points: list[dict], fetched_at: datetime) -> int:
        """Parse raw data points through the model and upsert them. Returns count."""
        if not raw_points:
            return 0
        self.ensure_table(data_type)
        model = model_for(data_type)
        cols = [c for c, _ in _columns_for(data_type)]
        table = _table_name(data_type)

        placeholders = ", ".join("?" for _ in cols)
        collist = ", ".join(f'"{c}"' for c in cols)
        updates = ", ".join(f'"{c}"=excluded."{c}"' for c in cols if c != "name")
        sql = (
            f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders}) '
            f'ON CONFLICT(name) DO UPDATE SET {updates}'
        )

        rows = []
        for point in raw_points:
            dp: DataPoint = model.from_raw(data_type, point, fetched_at)
            dump = dp.model_dump()
            row = []
            for c in cols:
                if c in _JSON_COLUMNS:
                    row.append(json.dumps(dump.get(c, {}), default=str))
                elif c == "fetched_at":
                    row.append(dp.fetched_at.isoformat())
                else:
                    row.append(dump.get(c))
            rows.append(row)

        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)

    def close(self) -> None:
        self.conn.close()


# --------------------------------------------------------------------------- #
# Module-level convenience: cache one Database per fetcher per process.
# --------------------------------------------------------------------------- #
_dbs: dict[str, Database] = {}
_lock = threading.Lock()


def _get_db(fetcher_name: str) -> Database:
    with _lock:
        db = _dbs.get(fetcher_name)
        if db is None:
            db = Database(fetcher_name)
            _dbs[fetcher_name] = db
        return db


def store(fetcher_name: str, data_type: str, raw_points: list[dict], fetched_at: datetime) -> int:
    """Persist one page of data points for a fetcher/data type. Returns count."""
    return _get_db(fetcher_name).upsert_points(data_type, raw_points, fetched_at)


def stats() -> dict:
    """Row counts per data type per fetcher database, for observability.

    Reads existing database files only (read-only); never creates them.
    """
    out: dict[str, dict] = {}
    for fetcher_name in config.FETCHERS:
        path = db_path_for_fetcher(fetcher_name)
        if not path.exists():
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        finally:
            conn.close()
        if counts:
            out[fetcher_name] = {"rows": counts, "total": sum(counts.values())}
    return out
