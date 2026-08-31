"""The daily Seattle coffee-shop recommendation.

One email each morning with a shop the owner has not been sent recently, close
enough to South Lake Union to be a short drive, calm enough to sit in, and —
the part that actually matters — described from the live web rather than from
the model's memory. A recommendation is only accepted when the run really
searched, the hours it reports are today's published hours, and the shop is
open today.

Run it by hand with::

    uv run python -m personal.runner run coffee --dry-run
    uv run python -m personal.runner run coffee

and record a verdict on a shop with::

    uv run python -m personal.runner feedback "Victrola Coffee" disliked --note "too loud"
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from fitlit.journal import PACIFIC
from personal import agent, config, emails, store

log = logging.getLogger("fitlit.personal.coffee")

TASK = "coffee"
_MAPS_PREFIXES = (
    "https://www.google.com/maps",
    "https://maps.google.com/",
    "https://maps.app.goo.gl/",
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 2, "maxLength": 80},
        "neighborhood": {"type": "string", "minLength": 2, "maxLength": 60},
        "address": {"type": "string", "minLength": 5, "maxLength": 160},
        "google_maps_url": {"type": "string", "minLength": 10, "maxLength": 400},
        "website": {"type": "string", "maxLength": 300},
        "open_today": {"type": "boolean"},
        "hours_today": {"type": "string", "minLength": 3, "maxLength": 120},
        "hours_source": {"type": "string", "minLength": 3, "maxLength": 200},
        "hours_note": {"type": "string", "maxLength": 240},
        "drive_minutes": {"type": "integer", "minimum": 1, "maximum": 60},
        "drive_note": {"type": "string", "minLength": 5, "maxLength": 240},
        "noise_level": {
            "type": "string",
            "enum": list(config.COFFEE_NOISE_LEVELS),
        },
        "noise_evidence": {"type": "string", "minLength": 10, "maxLength": 400},
        "vibe": {"type": "string", "minLength": 20, "maxLength": 500},
        "seating": {"type": "string", "minLength": 5, "maxLength": 240},
        "wifi_outlets": {"type": "string", "minLength": 3, "maxLength": 200},
        "signature_order": {"type": "string", "minLength": 3, "maxLength": 200},
        "food_note": {"type": "string", "maxLength": 240},
        "best_time": {"type": "string", "minLength": 3, "maxLength": 200},
        "why_today": {"type": "string", "minLength": 40, "maxLength": 700},
        "one_liner": {"type": "string", "minLength": 8, "maxLength": 120},
        "verified_date": {"type": "string", "minLength": 10, "maxLength": 10},
        "search_queries": {
            "type": "array",
            "minItems": 2,
            "maxItems": 8,
            "items": {"type": "string", "minLength": 5, "maxLength": 160},
        },
        "sources": {
            "type": "array",
            "minItems": 2,
            "maxItems": 6,
            "items": {"type": "string", "minLength": 10, "maxLength": 400},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "name", "neighborhood", "address", "google_maps_url", "website",
        "open_today", "hours_today", "hours_source", "hours_note",
        "drive_minutes", "drive_note", "noise_level", "noise_evidence", "vibe",
        "seating", "wifi_outlets", "signature_order", "food_note", "best_time",
        "why_today", "one_liner", "verified_date", "search_queries", "sources",
        "confidence",
    ],
    "additionalProperties": False,
}


class CoffeeRejected(RuntimeError):
    """A candidate failed a hard rule and the run should try again."""


@dataclass(frozen=True)
class CoffeeResult:
    status: str
    day: str
    shop: dict[str, Any] | None = None
    subject: str | None = None
    message_id: str | None = None
    attempts: int = 0
    web_searches: int = 0
    repeat_of_day: str | None = None
    detail: str | None = None


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "You are the personal-tasks researcher for FitLit, a private assistant "
    "that serves exactly one owner. This run produces a real coffee-shop "
    "recommendation the owner will act on this morning, so every fact must "
    "come from a live web result you actually retrieved in this session. "
    "Never answer a factual question about a business from memory: opening "
    "hours change, cafes close, and a stale hour sends the owner to a locked "
    "door. If you cannot verify a shop, pick a different shop rather than "
    "guessing. Return only the requested JSON object."
)


def _history_block(recent: list[dict[str, Any]]) -> str:
    if not recent:
        return "No coffee shop has been recommended yet; anything eligible is new."
    lines = [
        f"- {row['name']} ({row['neighborhood']}) — sent {row['day']}"
        for row in recent
    ]
    return "\n".join(lines)


def _preference_block(preferences: list[dict[str, Any]]) -> str:
    if not preferences:
        return "The owner has not rated any shop yet."
    lines = []
    for row in preferences:
        note = f" — \"{row['note']}\"" if row.get("note") else ""
        lines.append(f"- {row['shop']}: {row['sentiment']}{note}")
    return "\n".join(lines)


def build_prompt(
    day: date,
    *,
    recent: list[dict[str, Any]],
    blocked: list[str],
    preferences: list[dict[str, Any]],
    rejections: list[str],
) -> str:
    weekday = day.strftime("%A")
    blocked_block = (
        "\n".join(f"- {name}" for name in blocked)
        if blocked
        else "None."
    )
    rejection_block = (
        "\n".join(f"- {reason}" for reason in rejections)
        if rejections
        else "None — this is the first attempt."
    )
    return f"""\
Recommend exactly one coffee shop in {config.COFFEE_CITY} for the owner to \
visit today, {weekday}, {day.isoformat()} (Pacific time).

## Hard constraints
1. Drive time: no more than about {config.COFFEE_TARGET_DRIVE_MINUTES} minutes \
by car from {config.COFFEE_ORIGIN} in normal mid-morning traffic. \
{config.COFFEE_MAX_DRIVE_MINUTES} minutes is the absolute ceiling. \
Neighborhoods that typically qualify include South Lake Union, Belltown, \
Downtown, Denny Triangle, Capitol Hill, First Hill, Eastlake, Queen Anne, \
Fremont, Wallingford, Ballard, Interbay, Magnolia, Green Lake, Phinney Ridge, \
University District, Montlake, Madison Valley, the Central District, \
Pioneer Square, the International District, SoDo, and Georgetown. Confirm the \
drive rather than assuming it.
2. Atmosphere: calm and low-key — somewhere the owner can sit, think, read, or \
work. A little background noise and conversation is fine; a loud, packed, \
music-forward, or bar-like room is not. Classify it as one of \
{", ".join(config.COFFEE_NOISE_LEVELS)}.
3. It must be a real, currently operating coffee shop that is OPEN today. \
Reject anything marked permanently closed, temporarily closed, or "hours may \
differ" without confirmation.
4. It must not be a shop from the already-recommended list or the blocked list \
below.

## Already recommended (do not repeat)
{_history_block(recent)}

## Blocked by the owner (never recommend)
{blocked_block}

## The owner's standing feedback (honor this)
{_preference_block(preferences)}

## Rejected earlier in this same run
{rejection_block}

## Research method — this is the part that matters
Use WebSearch and WebFetch for real. Write your queries the way a person \
actually types them into Google, one plain phrase per search, for example:

- `quiet coffee shops near South Lake Union Seattle`
- `best coffee shop to work in Fremont Seattle`
- `<shop name> Seattle`
- `<shop name> Seattle hours`
- `<shop name> Seattle reviews quiet`

Then, before you answer:

- Open the shop's own website or its Google Maps / Google Business listing with \
WebFetch and read the posted hours. Report `hours_today` EXACTLY as that source \
publishes today's {weekday} hours, in Pacific time, in the form the listing \
uses (for example `7:00 AM – 4:00 PM`). Do not average, round, infer from \
another weekday, or reuse hours you remember.
- Name the source you took the hours from in `hours_source` (for example \
`Google Business listing` or `shop's own website, Hours page`).
- If today's hours differ from the shop's usual hours, or the listing warns \
that hours may differ (a holiday, a seasonal change), say so in `hours_note`. \
Otherwise leave `hours_note` empty.
- Confirm the shop is open today and set `open_today` accordingly. If it is \
closed today, choose a different shop.
- Read recent reviews for how the room actually sounds and feels, and quote or \
paraphrase that in `noise_evidence`.
- Estimate `drive_minutes` from {config.COFFEE_ORIGIN} and explain the route \
briefly in `drive_note`.
- `google_maps_url` must be a working Google Maps link for this exact location, \
for example \
`https://www.google.com/maps/search/?api=1&query=Shop+Name+Seattle+WA`.
- `sources` must list the real URLs you actually opened or that the search \
returned, from at least two different sites.
- `search_queries` must list the queries you actually ran, verbatim.
- `verified_date` must be `{day.isoformat()}`.
- `confidence` is your honest confidence that the hours and status are correct \
right now.

## Voice
`why_today` is written to the owner directly — concrete and specific about this \
shop, not generic coffee praise. `one_liner` is a short hook for the email \
subject line. Keep everything factual and grounded in what you found.
"""


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise CoffeeRejected(f"{field} was missing or not a string")
    return value.strip()


def _valid_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def validate(
    payload: dict[str, Any],
    day: date,
    *,
    blocked: set[str],
    web_searches: int,
) -> dict[str, Any]:
    """Apply every rule the schema cannot express. Raises CoffeeRejected."""
    if web_searches < config.COFFEE_MIN_WEB_SEARCHES:
        raise CoffeeRejected(
            "the run reported no web search, so the hours could not be live"
        )

    name = _text(payload, "name")
    if not name:
        raise CoffeeRejected("name was empty")
    key = store.shop_key(name)
    if key in blocked:
        raise CoffeeRejected(f"{name} is blocked by the owner")

    if payload.get("open_today") is not True:
        raise CoffeeRejected(f"{name} is not open today")

    verified = _text(payload, "verified_date")
    if not _DATE.fullmatch(verified) or verified != day.isoformat():
        raise CoffeeRejected(
            f"verified_date was {verified!r}, not today ({day.isoformat()})"
        )

    minutes = payload.get("drive_minutes")
    if not isinstance(minutes, int) or isinstance(minutes, bool):
        raise CoffeeRejected("drive_minutes was not an integer")
    if minutes > config.COFFEE_MAX_DRIVE_MINUTES:
        raise CoffeeRejected(
            f"{name} is a {minutes}-minute drive, over the "
            f"{config.COFFEE_MAX_DRIVE_MINUTES}-minute ceiling"
        )

    noise = _text(payload, "noise_level")
    if noise not in config.COFFEE_NOISE_LEVELS:
        raise CoffeeRejected(f"noise_level {noise!r} is not an accepted level")

    maps_url = _text(payload, "google_maps_url")
    if not maps_url.startswith(_MAPS_PREFIXES):
        raise CoffeeRejected("google_maps_url was not a Google Maps link")

    website = _text(payload, "website")
    if website and not _valid_url(website):
        raise CoffeeRejected("website was not a usable URL")

    sources = payload.get("sources")
    if not isinstance(sources, list) or len(sources) < 2:
        raise CoffeeRejected("fewer than two sources were supplied")
    clean_sources = []
    for item in sources:
        if not isinstance(item, str) or not _valid_url(item.strip()):
            raise CoffeeRejected(f"source {item!r} was not a usable URL")
        clean_sources.append(item.strip())
    if len({urlsplit(url).netloc.lower() for url in clean_sources}) < 2:
        raise CoffeeRejected("every source came from the same site")

    queries = payload.get("search_queries")
    if not isinstance(queries, list) or len(queries) < 2:
        raise CoffeeRejected("fewer than two search queries were reported")

    hours = _text(payload, "hours_today")
    if not hours:
        raise CoffeeRejected("hours_today was empty")

    clean = dict(payload)
    clean.update({
        "name": name,
        "neighborhood": _text(payload, "neighborhood"),
        "address": _text(payload, "address"),
        "google_maps_url": maps_url,
        "website": website,
        "hours_today": hours,
        "noise_level": noise,
        "drive_minutes": minutes,
        "sources": clean_sources,
        "search_queries": [str(item).strip() for item in queries],
        "shop_key": key,
    })
    return clean


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def _recommend(
    day: date,
    connection,
    *,
    attempts: int,
) -> tuple[dict[str, Any], int, int, str | None]:
    """Ask the harness for a validated shop. Returns (shop, attempt, searches, repeat_of)."""
    recent = store.recent_recommendations(connection)
    blocked_names = store.blocked_shops(connection)
    blocked = store.blocked_keys(connection)
    preferences = store.preferences(connection)
    recent_keys = {row["shop_key"]: row["day"] for row in recent}
    rejections: list[str] = []
    duplicate: tuple[dict[str, Any], int, int, str] | None = None
    last_error: str | None = None

    for attempt in range(1, max(1, attempts) + 1):
        prompt = build_prompt(
            day,
            recent=recent,
            blocked=blocked_names,
            preferences=preferences,
            rejections=rejections,
        )
        try:
            run = agent.run(
                prompt,
                OUTPUT_SCHEMA,
                system_prompt=SYSTEM_PROMPT,
            )
        except agent.PersonalAgentError as exc:
            last_error = str(exc)
            log.warning("coffee attempt %d could not run: %s", attempt, exc)
            rejections.append(f"the previous attempt failed: {exc}")
            continue
        try:
            shop = validate(
                run.data,
                day,
                blocked=blocked,
                web_searches=run.web_searches,
            )
        except CoffeeRejected as exc:
            last_error = str(exc)
            log.warning("coffee attempt %d rejected: %s", attempt, exc)
            rejections.append(str(exc))
            continue

        seen_on = recent_keys.get(shop["shop_key"])
        if seen_on is None:
            return shop, attempt, run.web_searches, None
        # An occasional repeat is acceptable, but only after asking again.
        log.info(
            "coffee attempt %d repeated %s (last sent %s)",
            attempt,
            shop["name"],
            seen_on,
        )
        duplicate = duplicate or (shop, attempt, run.web_searches, seen_on)
        rejections.append(
            f"{shop['name']} was already recommended on {seen_on}; pick a "
            "different shop"
        )

    if duplicate is not None:
        return duplicate
    raise agent.PersonalAgentError(
        last_error or "no usable coffee recommendation was produced"
    )


def run(
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
    send: bool = True,
) -> CoffeeResult:
    """Produce, record, and mail today's recommendation."""
    moment = (now or datetime.now(PACIFIC)).astimezone(PACIFIC)
    day = moment.date()

    if not config.COFFEE_ENABLED and not force:
        return CoffeeResult(
            status="disabled",
            day=day.isoformat(),
            detail="FITLIT_PERSONAL_COFFEE_ENABLED is off",
        )

    connection = store.connect()
    try:
        if not dry_run and not store.reserve_run(
            connection, TASK, day, force=force
        ):
            return CoffeeResult(
                status="already-sent",
                day=day.isoformat(),
                detail="today's recommendation was already delivered",
            )
        try:
            shop, attempts, searches, repeat_of = _recommend(
                day, connection, attempts=config.COFFEE_ATTEMPTS
            )
        except agent.PersonalAgentError as exc:
            if not dry_run:
                store.finish_run(
                    connection, TASK, day, "failed", detail=str(exc)[:400]
                )
            return CoffeeResult(
                status="failed", day=day.isoformat(), detail=str(exc)
            )

        report = emails.coffee_report(shop, day, repeat_of_day=repeat_of)
        if dry_run:
            return CoffeeResult(
                status="dry-run",
                day=day.isoformat(),
                shop=shop,
                subject=report.subject,
                attempts=attempts,
                web_searches=searches,
                repeat_of_day=repeat_of,
            )

        store.record_recommendation(
            connection, day, shop, repeat_of_day=repeat_of
        )
        if not send:
            store.finish_run(
                connection, TASK, day, "skipped", detail="delivery skipped"
            )
            return CoffeeResult(
                status="recorded",
                day=day.isoformat(),
                shop=shop,
                subject=report.subject,
                attempts=attempts,
                web_searches=searches,
                repeat_of_day=repeat_of,
            )

        # Imported here so a dry run never needs Gmail credentials present.
        from fitlit import gmail_client

        try:
            message_id = gmail_client.send(
                report.subject,
                report.text,
                report.html,
                category="personal-coffee",
            )
        except Exception as exc:  # noqa: BLE001 - recorded, then reported
            store.finish_run(
                connection, TASK, day, "failed", detail=str(exc)[:400]
            )
            return CoffeeResult(
                status="send-failed",
                day=day.isoformat(),
                shop=shop,
                subject=report.subject,
                attempts=attempts,
                web_searches=searches,
                detail=str(exc),
            )
        store.finish_run(
            connection, TASK, day, "sent", message_id=message_id
        )
        return CoffeeResult(
            status="sent",
            day=day.isoformat(),
            shop=shop,
            subject=report.subject,
            message_id=message_id,
            attempts=attempts,
            web_searches=searches,
            repeat_of_day=repeat_of,
        )
    finally:
        connection.close()


def history(limit: int = 20) -> list[dict[str, Any]]:
    connection = store.connect()
    try:
        rows = connection.execute(
            """
            SELECT day, name, neighborhood, drive_minutes, noise_level,
                   hours_today, repeat_of_day
              FROM coffee_recommendations
             ORDER BY day DESC LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def status() -> dict[str, Any]:
    connection = store.connect()
    try:
        recent = store.recent_recommendations(connection)
        return {
            "task": TASK,
            "enabled": config.COFFEE_ENABLED,
            "origin": config.COFFEE_ORIGIN,
            "max_drive_minutes": config.COFFEE_MAX_DRIVE_MINUTES,
            "send_hour_pacific": config.COFFEE_SEND_HOUR,
            "repeat_window_days": config.COFFEE_REPEAT_WINDOW_DAYS,
            "shops_in_window": len(recent),
            "blocked": store.blocked_shops(connection),
            "preferences": store.preferences(connection),
            "recent_runs": store.run_history(connection, TASK, limit=7),
        }
    finally:
        connection.close()
