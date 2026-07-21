"""Editorial Gmail renderers for the morning and evening daily reports."""
from __future__ import annotations

import html

from fitlit.gmail_templates import Report

PALETTE = {
    "rust": "#a94e33",
    "clay": "#bd6a4a",
    "sage": "#5f8579",
    "olive": "#7c8154",
    "honey": "#c8973f",
    "ink": "#28231b",
    "muted": "#817a6c",
    "line": "#ded7c7",
    "paper": "#f8f4eb",
    "card": "#f4efe4",
}


def _fmt(value, suffix: str = "", digits: int = 0, missing: str = "—") -> str:
    if value is None:
        return missing
    if isinstance(value, (int, float)):
        rendered = f"{value:,.{digits}f}"
    else:
        rendered = str(value)
    return f"{rendered}{suffix}"


def _metric_grid(metrics: list[tuple[str, object, str, str, int]]) -> str:
    cells = []
    for label, value, unit, color, digits in metrics:
        cells.append(
            '<td style="width:50%;padding:6px;vertical-align:top">'
            f'<div style="border:1px solid {PALETTE["line"]};border-radius:13px;'
            f'padding:13px;background:{PALETTE["card"]}">'
            f'<div style="font:600 10px Arial,sans-serif;color:{PALETTE["muted"]};'
            f'text-transform:uppercase;letter-spacing:.7px">{html.escape(label)}</div>'
            f'<div style="margin-top:5px;font:italic 28px Georgia,serif;color:{color}">'
            f'{html.escape(_fmt(value, digits=digits))} '
            f'<span style="font:500 10px Arial,sans-serif;color:{PALETTE["muted"]}">'
            f'{html.escape(unit)}</span></div></div></td>'
        )
    rows = "".join(
        f"<tr>{''.join(cells[index:index + 2])}</tr>"
        for index in range(0, len(cells), 2)
    )
    return (
        '<table role="presentation" style="width:100%;border-spacing:0;'
        f'margin:14px -6px 0">{rows}</table>'
    )


def _section(title: str, eyebrow: str = "") -> str:
    eyebrow_html = (
        f'<div style="font:600 9px Arial,sans-serif;letter-spacing:1.4px;'
        f'text-transform:uppercase;color:{PALETTE["rust"]}">{html.escape(eyebrow)}</div>'
        if eyebrow else ""
    )
    return (
        f'<div style="margin-top:22px">{eyebrow_html}'
        f'<div style="margin-top:3px;font:italic 22px Georgia,serif;'
        f'color:{PALETTE["ink"]}">{html.escape(title)}</div></div>'
    )


def _detail_rows(details: list[tuple[str, str]]) -> str:
    rows = "".join(
        '<tr>'
        f'<td style="padding:9px 0;border-top:1px solid {PALETTE["line"]};'
        f'font:11px Arial,sans-serif;color:{PALETTE["muted"]}">{html.escape(label)}</td>'
        f'<td style="padding:9px 0;border-top:1px solid {PALETTE["line"]};'
        f'text-align:right;font:600 11px Arial,sans-serif;color:{PALETTE["ink"]}">'
        f'{html.escape(value)}</td></tr>'
        for label, value in details
    )
    return (
        '<table role="presentation" style="width:100%;border-collapse:collapse;'
        f'margin-top:9px">{rows}</table>'
    )


def _observation_list(items: list[str]) -> str:
    return (
        f'<div style="border:1px solid {PALETTE["line"]};border-radius:13px;'
        f'padding:13px 15px;background:{PALETTE["paper"]}">'
        f'<ul style="margin:0;padding-left:18px;font:13px Arial,sans-serif;'
        f'line-height:1.5;color:#4d473d">'
        + "".join(f'<li style="margin:6px 0">{html.escape(item)}</li>' for item in items)
        + "</ul></div>"
    )


def _date_note(context: dict) -> str:
    return (
        f'<div style="margin-top:18px;border-left:3px solid {PALETTE["honey"]};'
        'padding:10px 13px;background:#f7efd9">'
        f'<div style="font:600 10px Arial,sans-serif;text-transform:uppercase;'
        f'letter-spacing:1px;color:{PALETTE["honey"]}">Date detail</div>'
        f'<div style="margin-top:4px;font:italic 15px Georgia,serif;color:{PALETTE["ink"]}">'
        f'Day {context["day_of_year"]} of {context["days_in_year"]} · '
        f'ISO week {context["iso_week"]} · {context["days_remaining"]} days remain in '
        f'{context["full"][-4:]}</div></div>'
    )


def _footer(coverage: str) -> str:
    return (
        '<div style="margin-top:20px;padding-top:12px;'
        f'border-top:1px solid {PALETTE["line"]};font:10px Arial,sans-serif;'
        f'line-height:1.5;color:#9a9385">{html.escape(coverage)}<br>'
        'Wearable signals are directional and are not medical advice.</div>'
    )


def _shell(kicker: str, title: str, subtitle: str, content: str) -> str:
    return f"""<!doctype html>
<html><body style="margin:0;background:#ede7d8;color:#221e16">
<div style="max-width:640px;margin:0 auto;padding:28px 18px;font-family:Arial,sans-serif">
  <div style="font-size:10px;letter-spacing:2px;text-transform:uppercase;color:#9a9385">{html.escape(kicker)}</div>
  <h1 style="margin:5px 0 3px;font:normal 35px Georgia,serif">{html.escape(title)}</h1>
  <div style="font-size:12px;color:#817a6c">{html.escape(subtitle)}</div>
  {content}
</div></body></html>"""


def sleep_report(digest: dict) -> Report:
    context = digest["date"]
    sleep = digest["sleep"]
    baseline = digest["baseline"]
    recovery = digest["recovery"]
    stages = sleep["stages"]
    available_stages = {
        name: minutes for name, minutes in stages.items() if minutes is not None
    }
    stage_total = sum(available_stages.values()) or 1
    stage_colors = {
        "deep": PALETTE["sage"],
        "rem": "#b07766",
        "light": PALETTE["olive"],
        "awake": PALETTE["honey"],
    }
    stage_bar = "".join(
        f'<td style="height:13px;background:{stage_colors[name]};'
        f'width:{minutes / stage_total * 100:.2f}%"></td>'
        for name, minutes in available_stages.items() if minutes
    )
    stage_labels = " · ".join(
        f"{name.title()} {_fmt(minutes, 'm')}" for name, minutes in stages.items()
    ) if available_stages else "Stage data unavailable"
    restorative = (
        stages["deep"] + stages["rem"]
        if stages["deep"] is not None and stages["rem"] is not None else None
    )
    metrics = _metric_grid([
        ("Sleep", sleep["hours_asleep"], "hours", PALETTE["sage"], 2),
        ("Efficiency", sleep["efficiency_pct"], "%", PALETTE["olive"], 1),
        ("Deep + REM", restorative, "min", "#b07766", 0),
        ("Time to sleep", sleep["latency_min"], "min", PALETTE["honey"], 0),
        ("HRV", recovery["hrv_ms"], "ms", PALETTE["sage"], 1),
        ("Resting HR", recovery["resting_hr_bpm"], "bpm", PALETTE["rust"], 1),
        ("SpO₂", recovery["spo2_pct"], "%", PALETTE["sage"], 1),
        ("Respiration", recovery["respiratory_rate"], "/ min", PALETTE["clay"], 1),
    ])
    content = "".join([
        metrics,
        _section("Sleep architecture", "The night"),
        (
            f'<div style="margin-top:10px;border-radius:8px;overflow:hidden;'
            f'background:#e5dece"><table role="presentation" style="width:100%;'
            f'border-spacing:0"><tr>{stage_bar}</tr></table></div>'
            if stage_bar else ""
        ),
        f'<div style="margin-top:7px;font:10px Arial,sans-serif;color:'
        f'{PALETTE["muted"]}">{html.escape(stage_labels)}</div>',
        _detail_rows([
            ("Sleep window", f"{sleep['start'].strftime('%-I:%M %p')} – {sleep['end'].strftime('%-I:%M %p')} PT"),
            ("In bed", _fmt(sleep["minutes_in_bed"], " min")),
            ("Awake", _fmt(sleep["awake_min"], " min")),
            ("Prior 7-night average", _fmt(baseline["avg_hours"], " h", 2, "No baseline")),
            ("Vs recent average", (
                f"{baseline['duration_delta_hours']:+.2f} h"
                if baseline["duration_delta_hours"] is not None else "No baseline"
            )),
            ("Bedtime consistency", _fmt(baseline["bedtime_consistency_min"], " min SD", 0, "Need 2+ nights")),
        ]),
        _section("Morning read", "Interpretation"),
        _observation_list(digest["observations"]),
        f'<div style="margin-top:12px;border-left:3px solid {PALETTE["sage"]};'
        f'padding:10px 13px;background:#edf0e6;font:italic 14px Georgia,serif;'
        f'line-height:1.45;color:#5f604c">{html.escape(digest["priority"])}</div>',
        _date_note(context),
        _footer(
            f"Coverage: {digest['coverage']['sleep_baseline_nights']} prior sleep nights · "
            f"{digest['coverage']['hrv_baseline_days']} HRV days · "
            f"{digest['coverage']['oxygen_baseline_days']} oxygen days."
        ),
    ])
    if sleep["hours_asleep"] is not None and sleep["efficiency_pct"] is not None:
        subject_summary = f"{sleep['hours_asleep']:.2f}h · {sleep['efficiency_pct']:.0f}%"
    elif sleep["hours_asleep"] is not None:
        subject_summary = f"{sleep['hours_asleep']:.2f}h · efficiency pending"
    else:
        subject_summary = "sleep summary pending"
    subject = f"FitLit Sleep | {context['short'].split(', ', 1)[1]} | {subject_summary}"
    text = "\n".join([
        subject,
        context["full"],
        "",
        f"Sleep: {_fmt(sleep['hours_asleep'], ' h', 2)} at "
        f"{_fmt(sleep['efficiency_pct'], '%', 1)} efficiency",
        f"Window: {sleep['start'].strftime('%-I:%M %p')}–{sleep['end'].strftime('%-I:%M %p')} PT",
        f"Stages: {stage_labels}",
        f"Time to sleep: {_fmt(sleep['latency_min'], ' min')}",
        f"Awake: {sleep['awake_min']} min",
        f"HRV: {_fmt(recovery['hrv_ms'], ' ms', 1)}",
        f"Resting HR: {_fmt(recovery['resting_hr_bpm'], ' bpm', 1)}",
        f"SpO2: {_fmt(recovery['spo2_pct'], '%', 1)}",
        f"Respiration: {_fmt(recovery['respiratory_rate'], '/min', 1)}",
        "",
        "Morning read:",
        *(f"- {item}" for item in digest["observations"]),
        f"- Focus: {digest['priority']}",
        "",
        f"Date detail: day {context['day_of_year']} · week {context['iso_week']} · "
        f"{context['days_remaining']} days remain in {context['full'][-4:]}",
    ])
    return Report(
        subject=subject,
        text=text,
        html=_shell("Morning recovery brief", "Sleep, decoded.", context["full"], content),
    )


def _movement_chart(blocks: list[dict]) -> str:
    maximum = max((block["steps"] for block in blocks), default=1) or 1
    cells = []
    for block in blocks:
        height = max(4, round(block["steps"] / maximum * 58))
        cells.append(
            '<td style="width:12.5%;text-align:center;vertical-align:bottom;padding:0 3px">'
            f'<div style="height:62px;display:flex;align-items:flex-end;justify-content:center">'
            f'<div style="width:100%;height:{height}px;background:{PALETTE["olive"]};'
            f'border-radius:5px 5px 2px 2px"></div></div>'
            f'<div style="margin-top:5px;font:9px Arial,sans-serif;color:{PALETTE["muted"]}">'
            f'{html.escape(block["label"])}</div></td>'
        )
    return (
        '<table role="presentation" style="width:100%;border-spacing:0;'
        f'margin-top:10px"><tr>{"".join(cells)}</tr></table>'
    )


def _workout_rows(sessions: list[dict]) -> str:
    if not sessions:
        return (
            f'<div style="margin-top:9px;padding:13px;border:1px solid {PALETTE["line"]};'
            f'border-radius:12px;font:12px Arial,sans-serif;color:{PALETTE["muted"]}">'
            'No formal workout record was captured today.</div>'
        )
    rows = []
    for session in sessions:
        quality = (
            f" · excluded: {'; '.join(session['quality_flags'])}"
            if session["quality_flags"] else ""
        )
        rows.append(
            '<tr>'
            f'<td style="padding:10px 0;border-top:1px solid {PALETTE["line"]};'
            f'font:600 12px Arial,sans-serif;color:{PALETTE["ink"]}">'
            f'{html.escape(session["name"])}</td>'
            f'<td style="padding:10px 0;border-top:1px solid {PALETTE["line"]};'
            f'text-align:right;font:11px Arial,sans-serif;color:{PALETTE["muted"]}">'
            f'{session["duration_min"]} min · {session["calories"]:,} kcal'
            f'{html.escape(quality)}</td></tr>'
        )
    return (
        '<table role="presentation" style="width:100%;border-collapse:collapse;'
        f'margin-top:8px">{"".join(rows)}</table>'
    )


def day_report(digest: dict) -> Report:
    context = digest["date"]
    activity = digest["activity"]
    training = digest["training"]
    recovery = digest["recovery"]
    sleep = digest["sleep"]["sleep"] if digest["sleep"] else {}
    metrics = _metric_grid([
        ("Steps", activity["steps"], "", PALETTE["olive"], 0),
        ("Goal", activity["step_goal_pct"], "%", PALETTE["honey"], 0),
        ("Energy out", activity["calories_out"], "kcal", PALETTE["honey"], 0),
        ("Exercise", training["workout_minutes"], "min", PALETTE["rust"], 0),
        ("Zone load", training["active_zone_minutes"], "min", PALETTE["clay"], 0),
        ("Sleep", sleep.get("hours_asleep"), "hours", PALETTE["sage"], 2),
        ("HRV", recovery.get("hrv_ms"), "ms", PALETTE["sage"], 1),
        ("Resting HR", recovery.get("resting_hr_bpm"), "bpm", PALETTE["rust"], 1),
    ])
    facts = digest["facts"] or ["The available data did not produce a reliable standout fact."]
    content = "".join([
        metrics,
        _section("Movement rhythm", "Across the clock"),
        _movement_chart(digest["movement"]["blocks"]),
        _detail_rows([
            ("Seven-day step average", _fmt(activity["seven_day_avg_steps"], " steps")),
            ("Exercise energy", _fmt(training["exercise_calories"], " kcal")),
            ("Trusted workout records", f"{training['trusted_records']} of {training['formal_records']}"),
            ("Average weight", _fmt(digest["weight"]["avg7_lb"], " lb", 1, "No recent entries")),
            ("SpO₂", _fmt(recovery.get("spo2_pct"), "%", 1)),
            ("Respiratory rate", _fmt(recovery.get("respiratory_rate"), " / min", 1)),
        ]),
        _section("Workout ledger", "Training"),
        _workout_rows(training["sessions"]),
        _section("Details of the day", "What stood out"),
        _observation_list(facts),
        (
            _section("The practical read", "Direction")
            + _observation_list(digest["observations"])
            if digest["observations"] else ""
        ),
        _date_note(context),
        _footer(
            f"Coverage: {digest['coverage']['activity_days']} recent activity days · "
            f"{digest['coverage']['hourly_step_samples']} active hours · "
            f"{digest['coverage']['formal_workouts']} formal workout records · "
            f"sleep {'available' if digest['coverage']['sleep_available'] else 'pending'}."
        ),
    ])
    steps = activity["steps"]
    calories = activity["calories_out"]
    steps_summary = f"{steps:,} steps" if steps is not None else "steps unavailable"
    calories_summary = (
        f"{calories:,} kcal" if calories is not None else "energy unavailable"
    )
    subject = (
        f"FitLit Daily | {context['short'].split(', ', 1)[1]} | "
        f"{steps_summary} · {calories_summary}"
    )
    text = "\n".join([
        subject,
        context["full"],
        "",
        f"Steps: {_fmt(steps)} ({_fmt(activity['step_goal_pct'], '%')} of 10,000)",
        f"Energy out: {_fmt(calories, ' kcal')}",
        f"Exercise: {training['workout_minutes']} min · "
        f"{training['exercise_calories']:,} kcal · "
        f"{training['active_zone_minutes']} active-zone min",
        f"Sleep: {_fmt(sleep.get('hours_asleep'), ' h', 2)}",
        f"HRV: {_fmt(recovery.get('hrv_ms'), ' ms', 1)}",
        f"Resting HR: {_fmt(recovery.get('resting_hr_bpm'), ' bpm', 1)}",
        f"SpO2: {_fmt(recovery.get('spo2_pct'), '%', 1)}",
        "",
        "Details of the day:",
        *(f"- {item}" for item in facts),
        "",
        "Practical read:",
        *(f"- {item}" for item in digest["observations"]),
        "",
        f"Date detail: day {context['day_of_year']} · week {context['iso_week']} · "
        f"{context['days_remaining']} days remain in {context['full'][-4:]}",
    ])
    return Report(
        subject=subject,
        text=text,
        html=_shell("Evening day in review", "Your day, in review.", context["full"], content),
    )
