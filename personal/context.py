"""The personal section's contribution to the conversational agent's grounding.

`fitlit.email_agent` builds one read-only snapshot per reply. Health comes from
the wearable databases; this module adds what the assistant knows about the
owner's personal side, so a question like "where am I getting coffee today?"
is answered from what was actually sent this morning rather than invented.

Everything here is best effort. A missing or unreadable ledger yields an empty
block instead of failing a health reply.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime
from typing import Any

log = logging.getLogger("fitlit.personal.context")

# Kept small on purpose: this rides along with every reply's evidence budget.
_RECENT_LIMIT = 6
_PREFERENCE_LIMIT = 8

_SHOP_FIELDS = (
    "name",
    "neighborhood",
    "address",
    "hours_today",
    "hours_source",
    "hours_note",
    "drive_minutes",
    "noise_level",
    "best_time",
    "one_liner",
    "verified_date",
    "google_maps_url",
    "website",
)


def _coffee_today(connection: sqlite3.Connection, day: date) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT payload_json, repeat_of_day FROM coffee_recommendations WHERE day=?",
        (day.isoformat(),),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None
    shop = {
        field: payload[field]
        for field in _SHOP_FIELDS
        if payload.get(field) not in (None, "")
    }
    if row["repeat_of_day"]:
        shop["previously_sent_on"] = row["repeat_of_day"]
    shop["sent_on"] = day.isoformat()
    return shop or None


def assistant_context(now: datetime, day: date) -> dict[str, Any]:
    """A compact, JSON-safe view of the owner's personal tasks."""
    # Imported here so the health path never pays for the personal package and
    # a personal-side import error can never break a health reply.
    from personal import config, store

    if not config.PERSONAL_DB.exists():
        return {}
    try:
        connection = store.connect()
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log.warning("personal ledger unavailable: %s", exc)
        return {}
    try:
        block: dict[str, Any] = {
            "coffee_task": {
                "delivers": "one Seattle coffee shop by email each morning",
                "send_hour_pacific": config.COFFEE_SEND_HOUR,
                "origin": config.COFFEE_ORIGIN,
                "max_drive_minutes": config.COFFEE_MAX_DRIVE_MINUTES,
            },
        }
        today_shop = _coffee_today(connection, day)
        if today_shop:
            block["coffee_today"] = today_shop
        recent = store.recent_recommendations(
            connection, window_days=365, limit=_RECENT_LIMIT, now=now
        )
        if recent:
            block["coffee_recent"] = [
                {
                    "day": row["day"],
                    "name": row["name"],
                    "neighborhood": row["neighborhood"],
                }
                for row in recent
                if row["day"] != day.isoformat()
            ][: _RECENT_LIMIT]
        preferences = store.preferences(connection, limit=_PREFERENCE_LIMIT)
        if preferences:
            block["coffee_feedback"] = preferences
        blocked = store.blocked_shops(connection)
        if blocked:
            block["coffee_blocked"] = blocked
        return block
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log.warning("personal context could not be read: %s", exc)
        return {}
    finally:
        connection.close()
