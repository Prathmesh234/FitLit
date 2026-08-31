"""Durable system of record for the personal section.

Three tables in one private SQLite file:

* ``personal_task_runs``  — one row per task per Pacific day. Reserving a row is
  how a scheduled task stays idempotent when a timer catches up after a reboot.
* ``coffee_recommendations`` — every coffee shop already sent, so the next run
  can exclude it.
* ``coffee_feedback`` — the owner's verdict on a specific shop. ``blocked``
  removes a shop permanently; the other verdicts become taste guidance.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fitlit import config as fitlit_config
from fitlit.journal import PACIFIC
from personal import config

SENTIMENTS = ("loved", "liked", "neutral", "disliked", "blocked")
RUN_STATUSES = ("reserved", "sent", "failed", "skipped")

_PUNCTUATION = re.compile(r"[^a-z0-9]+")
# Trailing business words carry no identity: "Victrola Coffee Roasters" and
# "Victrola Coffee" must collapse to the same shop.
_NOISE_WORDS = (
    "coffee", "coffeehouse", "coffeeshop", "cafe", "caffe", "roasters",
    "roasting", "roastery", "espresso", "bar", "co", "company", "the", "and",
    "seattle", "wa",
)


class PersonalStoreError(RuntimeError):
    """The ledger rejected an operation."""


def shop_key(name: str) -> str:
    """Collapse a shop name to a stable identity for duplicate detection."""
    folded = unicodedata.normalize("NFKD", str(name or "")).encode(
        "ascii", "ignore"
    ).decode("ascii").lower()
    words = [word for word in _PUNCTUATION.split(folded) if word]
    trimmed = [word for word in words if word not in _NOISE_WORDS]
    key = "-".join(trimmed or words)
    if not key:
        raise PersonalStoreError("shop name did not contain any usable characters")
    return key[:120]


def _now() -> str:
    return datetime.now(PACIFIC).isoformat(timespec="seconds")


def today(now: datetime | None = None) -> date:
    return (now or datetime.now(PACIFIC)).astimezone(PACIFIC).date()


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = Path(path or config.PERSONAL_DB)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute(
        f"PRAGMA journal_mode={fitlit_config.SQLITE_JOURNAL_MODE}"
    )
    connection.execute("PRAGMA foreign_keys=ON")
    _migrate(connection)
    return connection


def _migrate(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS personal_task_runs (
            id         INTEGER PRIMARY KEY,
            task       TEXT NOT NULL,
            day        TEXT NOT NULL,
            status     TEXT NOT NULL,
            detail     TEXT,
            message_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (task, day)
        );
        CREATE TABLE IF NOT EXISTS coffee_recommendations (
            id            INTEGER PRIMARY KEY,
            day           TEXT NOT NULL UNIQUE,
            shop_key      TEXT NOT NULL,
            name          TEXT NOT NULL,
            neighborhood  TEXT NOT NULL,
            address       TEXT NOT NULL,
            maps_url      TEXT NOT NULL,
            drive_minutes INTEGER,
            noise_level   TEXT,
            hours_today   TEXT,
            repeat_of_day TEXT,
            payload_json  TEXT NOT NULL,
            created_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS coffee_recommendations_key
            ON coffee_recommendations (shop_key, day DESC);
        CREATE TABLE IF NOT EXISTS coffee_feedback (
            id         INTEGER PRIMARY KEY,
            shop_key   TEXT NOT NULL,
            shop_name  TEXT NOT NULL,
            sentiment  TEXT NOT NULL,
            note       TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS coffee_feedback_key
            ON coffee_feedback (shop_key, created_at DESC);
    """)
    connection.commit()


# --------------------------------------------------------------------------- #
# Task run bookkeeping
# --------------------------------------------------------------------------- #
def reserve_run(
    connection: sqlite3.Connection,
    task: str,
    day: date,
    *,
    force: bool = False,
) -> bool:
    """Claim today's slot for a task. False means it already ran."""
    stamp = _now()
    if force:
        connection.execute(
            """
            INSERT INTO personal_task_runs
                (task, day, status, created_at, updated_at)
            VALUES (?,?,'reserved',?,?)
            ON CONFLICT (task, day) DO UPDATE
                SET status='reserved', detail=NULL, message_id=NULL,
                    updated_at=excluded.updated_at
            """,
            (task, day.isoformat(), stamp, stamp),
        )
        connection.commit()
        return True
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO personal_task_runs
            (task, day, status, created_at, updated_at)
        VALUES (?,?,'reserved',?,?)
        """,
        (task, day.isoformat(), stamp, stamp),
    )
    connection.commit()
    if cursor.rowcount:
        return True
    # A previous attempt that failed may be retried; a delivered day may not.
    row = connection.execute(
        "SELECT status FROM personal_task_runs WHERE task=? AND day=?",
        (task, day.isoformat()),
    ).fetchone()
    if row is not None and row["status"] == "failed":
        connection.execute(
            """
            UPDATE personal_task_runs
               SET status='reserved', detail=NULL, updated_at=?
             WHERE task=? AND day=?
            """,
            (stamp, task, day.isoformat()),
        )
        connection.commit()
        return True
    return False


def finish_run(
    connection: sqlite3.Connection,
    task: str,
    day: date,
    status: str,
    *,
    detail: str | None = None,
    message_id: str | None = None,
) -> None:
    if status not in RUN_STATUSES:
        raise PersonalStoreError(f"unknown run status: {status}")
    connection.execute(
        """
        UPDATE personal_task_runs
           SET status=?, detail=?, message_id=?, updated_at=?
         WHERE task=? AND day=?
        """,
        (status, detail, message_id, _now(), task, day.isoformat()),
    )
    connection.commit()


def run_history(
    connection: sqlite3.Connection,
    task: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM personal_task_runs"
    params: tuple = ()
    if task:
        sql += " WHERE task=?"
        params = (task,)
    sql += " ORDER BY day DESC, id DESC LIMIT ?"
    rows = connection.execute(sql, (*params, max(1, limit))).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# Coffee recommendations
# --------------------------------------------------------------------------- #
def recent_recommendations(
    connection: sqlite3.Connection,
    *,
    window_days: int | None = None,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Shops already sent inside the repeat window, newest first."""
    window = config.COFFEE_REPEAT_WINDOW_DAYS if window_days is None else window_days
    cutoff = today(now).toordinal() - max(0, window)
    rows = connection.execute(
        """
        SELECT day, shop_key, name, neighborhood, noise_level
          FROM coffee_recommendations
         ORDER BY day DESC, id DESC
         LIMIT ?
        """,
        (config.COFFEE_HISTORY_LIMIT if limit is None else max(1, limit),),
    ).fetchall()
    return [
        dict(row)
        for row in rows
        if date.fromisoformat(row["day"]).toordinal() >= cutoff
    ]


def last_seen(
    connection: sqlite3.Connection, key: str
) -> str | None:
    row = connection.execute(
        """
        SELECT day FROM coffee_recommendations
         WHERE shop_key=? ORDER BY day DESC LIMIT 1
        """,
        (key,),
    ).fetchone()
    return row["day"] if row else None


def record_recommendation(
    connection: sqlite3.Connection,
    day: date,
    payload: dict[str, Any],
    *,
    repeat_of_day: str | None = None,
) -> str:
    key = shop_key(payload["name"])
    connection.execute(
        """
        INSERT INTO coffee_recommendations
            (day, shop_key, name, neighborhood, address, maps_url,
             drive_minutes, noise_level, hours_today, repeat_of_day,
             payload_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (day) DO UPDATE SET
            shop_key=excluded.shop_key, name=excluded.name,
            neighborhood=excluded.neighborhood, address=excluded.address,
            maps_url=excluded.maps_url, drive_minutes=excluded.drive_minutes,
            noise_level=excluded.noise_level, hours_today=excluded.hours_today,
            repeat_of_day=excluded.repeat_of_day,
            payload_json=excluded.payload_json
        """,
        (
            day.isoformat(),
            key,
            payload["name"],
            payload["neighborhood"],
            payload["address"],
            payload["google_maps_url"],
            int(payload["drive_minutes"]),
            payload["noise_level"],
            payload["hours_today"],
            repeat_of_day,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            _now(),
        ),
    )
    connection.commit()
    return key


# --------------------------------------------------------------------------- #
# Coffee feedback
# --------------------------------------------------------------------------- #
def record_feedback(
    connection: sqlite3.Connection,
    name: str,
    sentiment: str,
    note: str | None = None,
) -> str:
    if sentiment not in SENTIMENTS:
        raise PersonalStoreError(
            f"sentiment must be one of {', '.join(SENTIMENTS)}"
        )
    clean = str(name).strip()
    if not clean:
        raise PersonalStoreError("a shop name is required")
    key = shop_key(clean)
    connection.execute(
        """
        INSERT INTO coffee_feedback
            (shop_key, shop_name, sentiment, note, created_at)
        VALUES (?,?,?,?,?)
        """,
        (key, clean[:200], sentiment, (note or "").strip()[:500] or None, _now()),
    )
    connection.commit()
    return key


def _latest_feedback(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT f.shop_key, f.shop_name, f.sentiment, f.note, f.created_at
          FROM coffee_feedback f
          JOIN (
                SELECT shop_key, MAX(id) AS newest
                  FROM coffee_feedback GROUP BY shop_key
               ) latest
            ON latest.newest = f.id
         ORDER BY f.id DESC
        """
    ).fetchall()


def blocked_shops(connection: sqlite3.Connection) -> list[str]:
    """Shops whose most recent verdict removes them from rotation."""
    return [
        row["shop_name"]
        for row in _latest_feedback(connection)
        if row["sentiment"] == "blocked"
    ]


def blocked_keys(connection: sqlite3.Connection) -> set[str]:
    return {
        row["shop_key"]
        for row in _latest_feedback(connection)
        if row["sentiment"] == "blocked"
    }


def preferences(
    connection: sqlite3.Connection, limit: int | None = None
) -> list[dict[str, Any]]:
    """The owner's standing verdicts, newest first, blocked entries excluded.

    Blocked shops are handled as a hard exclusion instead, so this is purely
    the taste signal: what to lean toward and what to avoid repeating.
    """
    cap = config.COFFEE_FEEDBACK_LIMIT if limit is None else max(1, limit)
    return [
        {
            "shop": row["shop_name"],
            "sentiment": row["sentiment"],
            "note": row["note"],
        }
        for row in _latest_feedback(connection)
        if row["sentiment"] != "blocked"
    ][:cap]


def feedback_history(
    connection: sqlite3.Connection, limit: int = 50
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM coffee_feedback ORDER BY id DESC LIMIT ?",
        (max(1, limit),),
    ).fetchall()
    return [dict(row) for row in rows]
