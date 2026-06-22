"""User-owned journal database — the writable side of FitLit.

FitLit's Google Health pull is **read-only**: the eight fetcher databases under
``data/db/<fetcher>.db`` mirror the wearable and must never be written by hand.
Anything the *user* tells the coach that the wearable can't capture — a fasted
weigh-in, what they ate, a waist measurement, a workout, a supplement — belongs
in a separate, user-owned journal: ``data/db/journal.db``.

This module owns that file. It is the single, typed place to log + read journal
data, so the rest of the app (chat coach, HTTP API, weekly report) never hand-rolls
SQL again. The journal never collides with the fetchers — it's its own file —
so it is always safe to open read-write.

Design notes
------------
* **Idempotent schema.** Every public call runs ``CREATE TABLE IF NOT EXISTS``
  first, so importing the module on a fresh machine just works; on an existing
  ``journal.db`` it's a no-op. The ``weight_log`` / ``meals`` shapes match what
  earlier coaching sessions created, so this is backward compatible.
* **Pacific-day keys.** The user is in Pacific time; days are stored as local
  ``YYYY-MM-DD`` strings so a "day" lines up with their calendar, not UTC.
* **Wide meals table.** One row per day, one column per meal (breakfast / lunch /
  dinner / snacks) — the layout the user asked for — plus estimated daily totals.

Standard library only (sqlite3); no external dependencies.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fitlit import config

# Pacific Standard/Daylight — the user's timezone. FitLit stores wearable data in
# UTC, but *journal* days are keyed to the user's local calendar day.
PACIFIC = timezone(timedelta(hours=-7))

LB_PER_KG = 2.2046226218


def db_path():
    """Path to the user-owned journal database."""
    return config.DB_DIR / "journal.db"


def today() -> str:
    """Today's date as a Pacific-local ``YYYY-MM-DD`` string."""
    return datetime.now(PACIFIC).strftime("%Y-%m-%d")


def _now_pacific_time() -> str:
    return datetime.now(PACIFIC).strftime("%H:%M")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
  date            TEXT PRIMARY KEY,   -- Pacific day, 'YYYY-MM-DD'
  breakfast       TEXT,
  lunch           TEXT,
  dinner          TEXT,
  snacks          TEXT,
  total_kcal      INTEGER,
  total_protein_g REAL,
  notes           TEXT,
  updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS weight_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  date        TEXT NOT NULL,          -- Pacific day, 'YYYY-MM-DD'
  weighed_at  TEXT,                   -- Pacific 'HH:MM'
  weight_lb   REAL,
  weight_kg   REAL,
  conditions  TEXT,                   -- e.g. 'AM fasted', 'post-meal'
  source      TEXT DEFAULT 'manual',
  note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_weight_date ON weight_log(date);

CREATE TABLE IF NOT EXISTS waist_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  date          TEXT NOT NULL,        -- Pacific day
  navel_in      REAL,                 -- waist at navel (inches)
  suprailiac_in REAL,                 -- suprailiac / iliac-crest (inches)
  note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_waist_date ON waist_log(date);

CREATE TABLE IF NOT EXISTS sleep_log (
  date         TEXT PRIMARY KEY,      -- Pacific night
  bedtime      TEXT,                  -- Pacific 'HH:MM'
  wake_time    TEXT,
  hours_asleep REAL,
  efficiency   REAL,                  -- percent
  rem_min      INTEGER,
  deep_min     INTEGER,
  feel         TEXT,                  -- subjective: 'rested' / 'groggy' / ...
  note         TEXT
);

CREATE TABLE IF NOT EXISTS supplement_log (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  date      TEXT NOT NULL,            -- Pacific day
  taken_at  TEXT,                     -- Pacific 'HH:MM'
  name      TEXT NOT NULL,            -- e.g. 'creatine', 'whey'
  amount    TEXT,                     -- e.g. '5g', '2 scoops'
  note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_supp_date ON supplement_log(date);

CREATE TABLE IF NOT EXISTS workout_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  date          TEXT NOT NULL,        -- Pacific day
  kind          TEXT,                 -- 'lift' / 'run' / 'cardio' / ...
  focus         TEXT,                 -- e.g. 'upper chest', 'legs'
  duration_min  INTEGER,
  detail        TEXT,                 -- freeform (lifts, sets, splits)
  note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_workout_date ON workout_log(date);
"""


def connect() -> sqlite3.Connection:
    """Open the journal read-write, ensuring the schema exists. Caller closes."""
    config.DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA journal_mode={config.SQLITE_JOURNAL_MODE}")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# --------------------------------------------------------------------------- #
# Logging helpers
# --------------------------------------------------------------------------- #
def log_weight(
    *,
    weight_lb: Optional[float] = None,
    weight_kg: Optional[float] = None,
    date: Optional[str] = None,
    weighed_at: Optional[str] = None,
    conditions: str = "unspecified",
    source: str = "manual",
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Record a weigh-in. Supply lb or kg; the other is derived."""
    if weight_lb is None and weight_kg is None:
        raise ValueError("provide weight_lb or weight_kg")
    if weight_kg is None:
        weight_kg = round(weight_lb / LB_PER_KG, 1)
    if weight_lb is None:
        weight_lb = round(weight_kg * LB_PER_KG, 1)
    row = {
        "date": date or today(),
        "weighed_at": weighed_at or _now_pacific_time(),
        "weight_lb": weight_lb,
        "weight_kg": weight_kg,
        "conditions": conditions,
        "source": source,
        "note": note,
    }
    with connect() as conn:
        conn.execute(
            "INSERT INTO weight_log(date,weighed_at,weight_lb,weight_kg,conditions,source,note) "
            "VALUES(:date,:weighed_at,:weight_lb,:weight_kg,:conditions,:source,:note)",
            row,
        )
        conn.commit()
    return row


def log_meal(
    date: Optional[str] = None,
    *,
    breakfast: Optional[str] = None,
    lunch: Optional[str] = None,
    dinner: Optional[str] = None,
    snacks: Optional[str] = None,
    total_kcal: Optional[int] = None,
    total_protein_g: Optional[float] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Upsert a day's meals. Only the fields you pass are written (others kept)."""
    date = date or today()
    fields = {
        "breakfast": breakfast, "lunch": lunch, "dinner": dinner, "snacks": snacks,
        "total_kcal": total_kcal, "total_protein_g": total_protein_g, "notes": notes,
    }
    given = {k: v for k, v in fields.items() if v is not None}
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO meals(date) VALUES(?)", (date,))
        if given:
            assignments = ", ".join(f'"{k}"=:{k}' for k in given)
            conn.execute(
                f"UPDATE meals SET {assignments}, updated_at=datetime('now') WHERE date=:date",
                {**given, "date": date},
            )
        conn.commit()
        cur = conn.execute("SELECT * FROM meals WHERE date=?", (date,))
        return dict(cur.fetchone())


def log_waist(
    *,
    navel_in: Optional[float] = None,
    suprailiac_in: Optional[float] = None,
    date: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Record a waist / suprailiac measurement (the body-comp handoff's #2 metric)."""
    row = {"date": date or today(), "navel_in": navel_in,
           "suprailiac_in": suprailiac_in, "note": note}
    with connect() as conn:
        conn.execute(
            "INSERT INTO waist_log(date,navel_in,suprailiac_in,note) "
            "VALUES(:date,:navel_in,:suprailiac_in,:note)", row)
        conn.commit()
    return row


def log_sleep(
    *,
    date: Optional[str] = None,
    bedtime: Optional[str] = None,
    wake_time: Optional[str] = None,
    hours_asleep: Optional[float] = None,
    efficiency: Optional[float] = None,
    rem_min: Optional[int] = None,
    deep_min: Optional[int] = None,
    feel: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Upsert a night's sleep summary (subjective + optional wearable-derived)."""
    date = date or today()
    fields = {
        "bedtime": bedtime, "wake_time": wake_time, "hours_asleep": hours_asleep,
        "efficiency": efficiency, "rem_min": rem_min, "deep_min": deep_min,
        "feel": feel, "note": note,
    }
    given = {k: v for k, v in fields.items() if v is not None}
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO sleep_log(date) VALUES(?)", (date,))
        if given:
            assignments = ", ".join(f'"{k}"=:{k}' for k in given)
            conn.execute(f"UPDATE sleep_log SET {assignments} WHERE date=:date",
                         {**given, "date": date})
        conn.commit()
        return dict(conn.execute("SELECT * FROM sleep_log WHERE date=?", (date,)).fetchone())


def log_supplement(
    name: str,
    amount: Optional[str] = None,
    *,
    date: Optional[str] = None,
    taken_at: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Record a supplement dose (creatine, whey, etc.)."""
    row = {"date": date or today(), "taken_at": taken_at or _now_pacific_time(),
           "name": name, "amount": amount, "note": note}
    with connect() as conn:
        conn.execute(
            "INSERT INTO supplement_log(date,taken_at,name,amount,note) "
            "VALUES(:date,:taken_at,:name,:amount,:note)", row)
        conn.commit()
    return row


def log_workout(
    kind: str,
    *,
    focus: Optional[str] = None,
    duration_min: Optional[int] = None,
    detail: Optional[str] = None,
    date: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Record a workout (lift / run / cardio …) with optional focus + detail."""
    row = {"date": date or today(), "kind": kind, "focus": focus,
           "duration_min": duration_min, "detail": detail, "note": note}
    with connect() as conn:
        conn.execute(
            "INSERT INTO workout_log(date,kind,focus,duration_min,detail,note) "
            "VALUES(:date,:kind,:focus,:duration_min,:detail,:note)", row)
        conn.commit()
    return row


# --------------------------------------------------------------------------- #
# Read helpers
# --------------------------------------------------------------------------- #
def recent_weights(limit: int = 30) -> list[dict]:
    with connect() as conn:
        cur = conn.execute(
            "SELECT date,weighed_at,weight_lb,weight_kg,conditions,source "
            "FROM weight_log ORDER BY date DESC, id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def recent_meals(limit: int = 14) -> list[dict]:
    with connect() as conn:
        cur = conn.execute(
            "SELECT date,breakfast,lunch,dinner,snacks,total_kcal,total_protein_g "
            "FROM meals ORDER BY date DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def recent_waist(limit: int = 12) -> list[dict]:
    with connect() as conn:
        cur = conn.execute(
            "SELECT date,navel_in,suprailiac_in,note FROM waist_log "
            "ORDER BY date DESC, id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def recent_workouts(limit: int = 20) -> list[dict]:
    with connect() as conn:
        cur = conn.execute(
            "SELECT date,kind,focus,duration_min,detail FROM workout_log "
            "ORDER BY date DESC, id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def summary() -> dict:
    """Row counts per journal table — quick health check / observability."""
    out: dict[str, int] = {}
    with connect() as conn:
        for table in ("meals", "weight_log", "waist_log", "sleep_log",
                      "supplement_log", "workout_log"):
            out[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    return out


if __name__ == "__main__":  # tiny smoke test / status
    import json
    print(f"journal: {db_path()}")
    print(json.dumps(summary(), indent=2))
