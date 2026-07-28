"""Deterministic, read-only health answers for self-sent Gmail commands."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from fitlit import ai_insights, daily_digest, weekly_catalog
from fitlit.journal import PACIFIC


@dataclass(frozen=True)
class EmailAnswer:
    subject: str
    text: str
    html: str
    intent: str


def classify(question: str) -> str:
    words = set(re.findall(r"[a-z0-9]+", question.lower()))
    if words & {"help", "commands", "examples"}:
        return "help"
    if words & {"week", "weekly"}:
        return "weekly"
    if words & {"sleep", "slept", "recovery", "hrv", "spo2", "oxygen", "resting"}:
        return "sleep"
    if words & {"workout", "exercise", "training", "trained", "lift", "lifting", "run"}:
        return "workout"
    if words & {"steps", "activity", "active", "movement", "calories"}:
        return "activity"
    return "daily"


def _fmt(value: Any, suffix: str = "", digits: int = 0) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _sleep_answer(day: date) -> tuple[str, list[tuple[str, str]], list[str], dict]:
    digest = daily_digest.build_sleep(day)
    if not digest:
        return (
            "Sleep data has not synced for this Pacific date.",
            [],
            ["FitLit will keep polling the wearable source."],
            {"report_type": "inbox_sleep", "sleep_available": False},
        )
    sleep = digest["sleep"]
    recovery = digest["recovery"]
    baseline = digest["baseline"]
    lead = (
        f"You recorded {_fmt(sleep['hours_asleep'], ' hours', 2)} of sleep at "
        f"{_fmt(sleep['efficiency_pct'], '%', 1)} efficiency."
    )
    metrics = [
        ("Sleep window", f"{sleep['start'].strftime('%-I:%M %p')}–{sleep['end'].strftime('%-I:%M %p')} PT"),
        ("Time to sleep", _fmt(sleep["latency_min"], " min")),
        ("Deep sleep", _fmt(sleep["stages"]["deep"], " min")),
        ("REM sleep", _fmt(sleep["stages"]["rem"], " min")),
        ("Vs 7-night average", (
            f"{baseline['duration_delta_hours']:+.2f} hours"
            if baseline["duration_delta_hours"] is not None else "Unavailable"
        )),
        ("HRV", _fmt(recovery["hrv_ms"], " ms", 1)),
        ("Resting heart rate", _fmt(recovery["resting_hr_bpm"], " bpm", 1)),
        ("SpO₂", _fmt(recovery["spo2_pct"], "%", 1)),
        ("Respiratory rate", _fmt(recovery["respiratory_rate"], "/min", 1)),
    ]
    return lead, metrics, [*digest["observations"], digest["priority"]], {
        **daily_digest.sleep_ai_payload(digest),
        "report_type": "inbox_sleep",
    }


def _workout_answer(day: date) -> tuple[str, list[tuple[str, str]], list[str], dict]:
    sessions = weekly_catalog.session_records(day - timedelta(days=7), day)
    today = [row for row in sessions if row["day"] == day.isoformat()]
    trusted_today = [row for row in today if not row["quality_flags"]]
    recent = [row for row in sessions if not row["quality_flags"]]
    if trusted_today:
        minutes = sum(row["duration_min"] for row in trusted_today)
        calories = sum(row["calories"] for row in trusted_today)
        zone = sum(row["active_zone_minutes"] for row in trusted_today)
        lead = (
            f"FitLit found {len(trusted_today)} trusted workout record(s) today: "
            f"{minutes} minutes and {calories:,} exercise kcal."
        )
        metrics = [
            ("Active-zone load", f"{zone} min"),
            ("Workout types", ", ".join(row["type"] for row in trusted_today)),
            ("Records excluded", str(len(today) - len(trusted_today))),
        ]
        observations = [
            f"{row['name']}: {row['duration_min']} min · {row['calories']:,} kcal · "
            f"{row['active_zone_minutes']} zone min."
            for row in trusted_today[:4]
        ]
        payload = {
            "report_type": "inbox_workout",
            "workout_records": len(trusted_today),
            "workout_minutes": minutes,
            "exercise_calories": calories,
            "active_zone_minutes": zone,
        }
        return lead, metrics, observations, payload
    latest = recent[-1] if recent else None
    if latest:
        return (
            "No trusted formal workout is recorded today.",
            [
                ("Latest workout", f"{latest['day']} · {latest['name']}"),
                ("Duration", f"{latest['duration_min']} min"),
                ("Exercise energy", f"{latest['calories']:,} kcal"),
                ("Active-zone load", f"{latest['active_zone_minutes']} min"),
            ],
            ["Walking and hourly movement can still appear in the activity answer."],
            {
                "report_type": "inbox_workout",
                "workout_records": 0,
                "latest_workout_days_ago": (day - date.fromisoformat(latest["day"])).days,
                "latest_workout_minutes": latest["duration_min"],
                "latest_exercise_calories": latest["calories"],
            },
        )
    return (
        "No trusted formal workout was found in the last eight days.",
        [],
        ["Try “How active was I today?” for steps and movement."],
        {"report_type": "inbox_workout", "workout_records": 0},
    )


def _activity_answer(day: date) -> tuple[str, list[tuple[str, str]], list[str], dict]:
    digest = daily_digest.build_day(day)
    activity = digest["activity"]
    training = digest["training"]
    sleep = digest["sleep"]["sleep"] if digest["sleep"] else {}
    recovery = digest["recovery"]
    lead = (
        f"Today currently shows {_fmt(activity['steps'], ' steps')} and "
        f"{_fmt(activity['calories_out'], ' kcal')} of total energy output."
    )
    metrics = [
        ("10,000-step goal", _fmt(activity["step_goal_pct"], "%")),
        ("7-day step average", _fmt(activity["seven_day_avg_steps"], " steps")),
        ("Workout time", f"{training['workout_minutes']} min"),
        ("Exercise energy", f"{training['exercise_calories']:,} kcal"),
        ("Active-zone load", f"{training['active_zone_minutes']} min"),
        ("Sleep", _fmt(sleep.get("hours_asleep"), " hours", 2)),
        ("HRV", _fmt(recovery.get("hrv_ms"), " ms", 1)),
        ("Resting heart rate", _fmt(recovery.get("resting_hr_bpm"), " bpm", 1)),
    ]
    return lead, metrics, [*digest["facts"], *digest["observations"]][:6], {
        **daily_digest.day_ai_payload(digest),
        "report_type": "inbox_activity",
    }


def _weekly_answer(day: date) -> tuple[str, list[tuple[str, str]], list[str], dict]:
    start = day - timedelta(days=day.weekday())
    catalog = weekly_catalog.build(start, day)
    training = catalog["training"]
    activity = catalog["activity"]
    sleep = catalog["sleep"]
    recovery = catalog["recovery"]
    lead = (
        f"For {catalog['week']['label']}, FitLit captured "
        f"{training['training_sessions']} training session(s), "
        f"{activity['total_steps']:,} steps, and "
        f"{_fmt(sleep['avg_hours'], ' average sleep hours', 2)}."
    )
    metrics = [
        ("Exercise energy", f"{training['exercise_calories']:,} kcal"),
        ("Training time", f"{training['training_duration_min']} min"),
        ("Average daily steps", _fmt(activity["avg_steps"])),
        ("Sleep debt", _fmt(sleep["sleep_debt_hours"], " hours", 1)),
        ("Average HRV", _fmt(recovery["avg_hrv_ms"], " ms", 1)),
        ("Average resting HR", _fmt(recovery["avg_resting_hr_bpm"], " bpm", 1)),
    ]
    return lead, metrics, [*catalog["insights"], *catalog["priorities"]][:7], {
        **weekly_catalog.ai_payload(catalog),
        "report_type": "inbox_weekly",
    }


def _help_answer() -> tuple[str, list[tuple[str, str]], list[str], dict]:
    return (
        "Send a self-addressed email whose subject starts with “FitLit Ask:”.",
        [
            ("Sleep", "FitLit Ask: How did I sleep?"),
            ("Workout", "FitLit Ask: How was my workout today?"),
            ("Activity", "FitLit Ask: How active was I today?"),
            ("Weekly", "FitLit Ask: Give me this week's summary"),
            ("Daily", "FitLit Ask: How did today look?"),
        ],
        [
            "Questions are classified locally into read-only health views.",
            "Email commands cannot run shell commands or change FitLit data.",
        ],
        {"report_type": "inbox_help"},
    )


def _render(
    intent: str,
    day: date,
    lead: str,
    metrics: list[tuple[str, str]],
    observations: list[str],
    ai: ai_insights.AIInsight | None,
) -> EmailAnswer:
    label = intent.title()
    subject = f"Re: FitLit Ask | {label} | {day.strftime('%b %-d')}"
    metric_text = [f"{name}: {value}" for name, value in metrics]
    ai_text = list(ai.observations) if ai else []
    text = "\n".join([
        lead,
        "",
        *metric_text,
        "",
        *(f"- {item}" for item in observations),
        *(["", "Additional observations:", *(f"- {item}" for item in ai_text)] if ai_text else []),
        "",
        f"Interpreted as: {intent}",
        f"Data date: {day.strftime('%A, %B %-d, %Y')} (Pacific)",
        "FitLit email commands are read-only and are not medical advice.",
    ])
    metric_rows = "".join(
        "<tr>"
        '<td style="padding:8px 0;border-top:1px solid #ded7c7;'
        'font:11px Arial,sans-serif;color:#817a6c">'
        f"{html.escape(name)}</td>"
        '<td style="padding:8px 0;border-top:1px solid #ded7c7;text-align:right;'
        'font:600 11px Arial,sans-serif;color:#28231b">'
        f"{html.escape(value)}</td></tr>"
        for name, value in metrics
    )
    observation_items = "".join(
        f'<li style="margin:6px 0">{html.escape(item)}</li>'
        for item in observations
    )
    ai_block = (
        '<div style="margin-top:16px;padding:12px;border-left:3px solid #5f8579;'
        'background:#edf0e6">'
        '<div style="font:600 10px Arial,sans-serif;color:#5f8579;'
        'text-transform:uppercase;letter-spacing:1px">Additional observations</div>'
        '<ul style="margin:7px 0 0;padding-left:18px;font:12px Arial,sans-serif;'
        f'line-height:1.5;color:#4d473d">{"".join(f"<li>{html.escape(item)}</li>" for item in ai_text)}</ul>'
        '</div>'
        if ai_text else ""
    )
    body = f"""<!doctype html>
<html><body style="margin:0;background:#ede7d8;color:#221e16">
<div style="max-width:620px;margin:0 auto;padding:26px 18px;font-family:Arial,sans-serif">
  <div style="font-size:10px;letter-spacing:2px;text-transform:uppercase;color:#9a9385">FitLit email assistant</div>
  <h1 style="margin:5px 0 3px;font:normal 32px Georgia,serif">{html.escape(label)} answer</h1>
  <div style="font-size:11px;color:#817a6c">{html.escape(day.strftime('%A, %B %-d, %Y'))} · Pacific</div>
  <div style="margin-top:16px;font:italic 17px Georgia,serif;line-height:1.45;color:#28231b">{html.escape(lead)}</div>
  <table role="presentation" style="width:100%;border-collapse:collapse;margin-top:14px">{metric_rows}</table>
  <ul style="margin:14px 0 0;padding-left:18px;font:12px Arial,sans-serif;line-height:1.5;color:#4d473d">{observation_items}</ul>
  {ai_block}
  <div style="margin-top:18px;padding-top:11px;border-top:1px solid #ded7c7;font:10px Arial,sans-serif;line-height:1.5;color:#9a9385">
    Interpreted as {html.escape(intent)} · read-only command · wearable data is not medical advice.
  </div>
</div></body></html>"""
    return EmailAnswer(subject=subject, text=text, html=body, intent=intent)


def answer(
    question: str,
    *,
    now: datetime | None = None,
    include_ai: bool = True,
) -> EmailAnswer:
    local = (now or datetime.now(PACIFIC)).astimezone(PACIFIC)
    cleaned = " ".join(question.split())
    intent = classify(cleaned)
    if intent == "sleep":
        lead, metrics, observations, payload = _sleep_answer(local.date())
    elif intent == "workout":
        lead, metrics, observations, payload = _workout_answer(local.date())
    elif intent == "activity":
        lead, metrics, observations, payload = _activity_answer(local.date())
    elif intent == "weekly":
        lead, metrics, observations, payload = _weekly_answer(local.date())
    elif intent == "help":
        lead, metrics, observations, payload = _help_answer()
    else:
        lead, metrics, observations, payload = _activity_answer(local.date())
        intent = "daily"
        payload["report_type"] = "inbox_daily"
    ai = (
        ai_insights.generate(payload)
        if include_ai and intent != "help"
        else None
    )
    return _render(intent, local.date(), lead, metrics, observations, ai)
