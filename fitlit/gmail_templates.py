"""Compact, inline-styled HTML reports that render reliably in Gmail."""
from __future__ import annotations

import html
from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    label: str
    value: str
    unit: str = ""
    accent: str = "#5f8579"


@dataclass(frozen=True)
class Report:
    subject: str
    text: str
    html: str


def _fmt(value, suffix: str = "", digits: int = 0, missing: str = "—") -> str:
    if value is None:
        return missing
    if isinstance(value, (int, float)):
        rendered = f"{value:,.{digits}f}"
    else:
        rendered = str(value)
    return f"{rendered}{suffix}"


def _change(value, suffix: str = "", digits: int = 1) -> str:
    if value is None:
        return "No prior baseline"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f}{suffix} vs prior week"


def weekly_report(catalog: dict) -> Report:
    """Render the in-depth Sunday training and recovery catalog."""
    week = catalog["week"]
    training = catalog["training"]
    activity = catalog["activity"]
    sleep = catalog["sleep"]
    recovery = catalog["recovery"]
    coverage = catalog["coverage"]
    subject = (
        f"FitLit Weekly | {week['label']} | {training['training_sessions']} workouts · "
        f"{training['exercise_calories']:,} exercise kcal"
    )
    palette = {
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

    metrics = [
        ("Training sessions", training["training_sessions"], "", palette["rust"]),
        ("Training time", training["training_duration_min"], "min", palette["clay"]),
        ("Exercise energy", training["exercise_calories"], "kcal", palette["honey"]),
        ("Active-zone load", training["active_zone_minutes"], "min", palette["rust"]),
        ("Average steps", activity["avg_steps"], "/ day", palette["olive"]),
        ("Total energy out", activity["total_calories_out"], "kcal", palette["honey"]),
        ("Average sleep", sleep["avg_hours"], "hours", palette["sage"]),
        ("Average HRV", recovery["avg_hrv_ms"], "ms", palette["sage"]),
    ]
    cells = [
        '<td style="width:50%;padding:6px;vertical-align:top">'
        f'<div style="border:1px solid {palette["line"]};border-radius:12px;'
        f'padding:12px;background:{palette["card"]}">'
        f'<div style="font:600 10px Arial,sans-serif;color:{palette["muted"]};'
        f'text-transform:uppercase;letter-spacing:.7px">{html.escape(label)}</div>'
        f'<div style="margin-top:5px;font:italic 27px Georgia,serif;color:{color}">'
        f'{html.escape(_fmt(value, digits=2 if label == "Average sleep" else 1 if label == "Average HRV" else 0))} '
        f'<span style="font:500 10px Arial,sans-serif;color:{palette["muted"]}">'
        f'{html.escape(unit)}</span></div></div></td>'
        for label, value, unit, color in metrics
    ]
    metric_rows = "".join(
        f"<tr>{''.join(cells[index:index + 2])}</tr>"
        for index in range(0, len(cells), 2)
    )

    max_steps = max((day.get("steps") or 0 for day in catalog["daily"]), default=1) or 1
    daily_rows = "".join(
        '<tr>'
        f'<td style="padding:8px 5px;border-top:1px solid {palette["line"]};'
        f'font:600 11px Arial,sans-serif;color:{palette["ink"]}">{html.escape(day["label"])}</td>'
        f'<td style="padding:8px 5px;border-top:1px solid {palette["line"]};width:42%">'
        f'<div style="height:7px;border-radius:5px;background:#e4dece">'
        f'<div style="height:7px;border-radius:5px;background:{palette["olive"]};'
        f'width:{round((day.get("steps") or 0) / max_steps * 100)}%"></div></div></td>'
        f'<td style="padding:8px 5px;border-top:1px solid {palette["line"]};'
        f'text-align:right;font:600 11px Arial,sans-serif;color:{palette["ink"]}">'
        f'{html.escape(_fmt(day.get("steps")))} steps</td>'
        f'<td style="padding:8px 5px;border-top:1px solid {palette["line"]};'
        f'text-align:right;font:11px Arial,sans-serif;color:{palette["muted"]}">'
        f'{html.escape(_fmt(day.get("sleep_hours"), "h", 2))}</td>'
        '</tr>'
        for day in catalog["daily"]
    )

    session_rows = []
    for session in catalog["sessions"]:
        flags = (
            f'<div style="margin-top:3px;color:{palette["rust"]};font-size:10px">'
            f'Quality note: {html.escape("; ".join(session["quality_flags"]))}</div>'
            if session["quality_flags"] else ""
        )
        details = [
            f'{session["duration_min"]} min',
            f'{session["calories"]:,} kcal',
        ]
        if session["avg_hr"] is not None:
            details.append(f'{session["avg_hr"]} avg bpm')
        if session["distance_km"]:
            details.append(f'{session["distance_km"]:.2f} km')
        if session["active_zone_minutes"]:
            details.append(f'{session["active_zone_minutes"]} zone min')
        session_rows.append(
            '<tr>'
            f'<td style="padding:10px 5px;border-top:1px solid {palette["line"]};'
            f'vertical-align:top;font:600 11px Arial,sans-serif;color:{palette["ink"]}">'
            f'{html.escape(session["day"][5:])}<br>'
            f'<span style="font-weight:400;color:{palette["muted"]}">'
            f'{html.escape(session["start"])}</span></td>'
            f'<td style="padding:10px 5px;border-top:1px solid {palette["line"]};'
            f'vertical-align:top;font:600 12px Arial,sans-serif;color:{palette["ink"]}">'
            f'{html.escape(session["name"])}'
            f'<div style="margin-top:3px;font:10px Arial,sans-serif;color:{palette["muted"]}">'
            f'{html.escape(session["type"])}</div>{flags}</td>'
            f'<td style="padding:10px 5px;border-top:1px solid {palette["line"]};'
            f'vertical-align:top;text-align:right;font:11px Arial,sans-serif;'
            f'line-height:1.45;color:{palette["ink"]}">'
            f'{html.escape(" · ".join(details))}</td>'
            '</tr>'
        )
    sessions_html = "".join(session_rows) or (
        f'<tr><td style="padding:12px;color:{palette["muted"]};font:12px Arial,sans-serif">'
        "No formal exercise sessions were recorded.</td></tr>"
    )

    recovery_rows = [
        ("Sleep efficiency", _fmt(sleep["avg_efficiency_pct"], "%", 1),
         _change(sleep["efficiency_change_points"], " points", 1)),
        ("Sleep debt", _fmt(sleep["sleep_debt_hours"], " h", 1),
         f'{sleep["nights"]}/7 nights captured'),
        ("Resting heart rate", _fmt(recovery["avg_resting_hr_bpm"], " bpm", 1),
         _change(recovery["resting_hr_change_bpm"], " bpm", 1)),
        ("Blood oxygen", _fmt(recovery["avg_spo2_pct"], "%", 1),
         (
             f'lowest nightly bound {_fmt(recovery["lowest_spo2_bound_pct"], "%", 1)}'
             if recovery["lowest_spo2_bound_pct"] is not None else "No lower-bound data"
         )),
        ("Respiratory rate", _fmt(recovery["avg_respiratory_rate"], " br/min", 1),
         _change(recovery["respiratory_change"], " br/min", 1)),
        ("Recovery-strain proxy", len(recovery["strain_flag_days"]), "days with both lower HRV and elevated RHR"),
    ]
    recovery_html = "".join(
        '<tr>'
        f'<td style="padding:9px 4px;border-top:1px solid {palette["line"]};'
        f'font:11px Arial,sans-serif;color:{palette["muted"]}">{html.escape(label)}</td>'
        f'<td style="padding:9px 4px;border-top:1px solid {palette["line"]};'
        f'text-align:right;font:600 12px Arial,sans-serif;color:{palette["ink"]}">'
        f'{html.escape(str(value))}</td>'
        f'<td style="padding:9px 4px;border-top:1px solid {palette["line"]};'
        f'text-align:right;font:10px Arial,sans-serif;color:{palette["muted"]}">'
        f'{html.escape(str(context))}</td>'
        '</tr>'
        for label, value, context in recovery_rows
    )

    insights_html = "".join(
        f'<li style="margin:7px 0">{html.escape(item)}</li>'
        for item in catalog["insights"]
    )
    priorities_html = "".join(
        f'<li style="margin:7px 0">{html.escape(item)}</li>'
        for item in catalog["priorities"]
    )
    quality_count = len(training["quality_flags"])
    coverage_text = (
        f'activity {coverage["activity_days"]}/7 · sleep {coverage["sleep_nights"]}/7 · '
        f'HRV {coverage["hrv_days"]}/7 · RHR {coverage["resting_hr_days"]}/7 · '
        f'oxygen {coverage["oxygen_days"]}/7 · respiration {coverage["respiratory_days"]}/7'
    )
    body = f"""<!doctype html>
<html><body style="margin:0;background:#ede7d8;color:#221e16">
<div style="max-width:700px;margin:0 auto;padding:28px 16px;font-family:Arial,sans-serif">
  <div style="font-size:10px;letter-spacing:2px;text-transform:uppercase;color:#9a9385">Weekly performance catalog</div>
  <h1 style="margin:6px 0 3px;font:normal 36px Georgia,serif">The week in motion.</h1>
  <div style="font-size:12px;color:{palette["muted"]}">{html.escape(week["label"])} · Pacific time</div>
  <table role="presentation" style="width:100%;border-spacing:0;margin:14px -6px 0">{metric_rows}</table>

  <div style="margin-top:22px;font:italic 23px Georgia,serif;color:{palette["ink"]}">Seven-day rhythm</div>
  <div style="font:10px Arial,sans-serif;color:{palette["muted"]};margin-top:2px">Movement bar · steps · sleep</div>
  <table role="presentation" style="width:100%;border-collapse:collapse;margin-top:8px">{daily_rows}</table>

  <div style="margin-top:22px;font:italic 23px Georgia,serif;color:{palette["ink"]}">Workout catalog</div>
  <div style="font:10px Arial,sans-serif;color:{palette["muted"]};margin-top:2px">
    {training["sessions"]} formal records · {training["walking_sessions"]} walking sessions · {training["distance_km"]:.2f} km
  </div>
  <table role="presentation" style="width:100%;border-collapse:collapse;margin-top:8px">{sessions_html}</table>

  <div style="margin-top:22px;font:italic 23px Georgia,serif;color:{palette["ink"]}">Recovery and physiology</div>
  <div style="font:10px Arial,sans-serif;color:{palette["muted"]};margin-top:2px">
    Stress is not directly measured; the strain proxy requires both HRV and resting-HR movement against the prior week.
  </div>
  <table role="presentation" style="width:100%;border-collapse:collapse;margin-top:8px">{recovery_html}</table>

  <table role="presentation" style="width:100%;border-spacing:0;margin:18px -6px 0"><tr>
    <td style="width:50%;padding:6px;vertical-align:top">
      <div style="border:1px solid {palette["line"]};border-radius:12px;padding:13px;background:{palette["paper"]}">
        <div style="font:600 10px Arial,sans-serif;letter-spacing:1px;text-transform:uppercase;color:{palette["muted"]}">What stood out</div>
        <ul style="margin:8px 0 0;padding-left:18px;font:12px Arial,sans-serif;line-height:1.45;color:#4d473d">{insights_html}</ul>
      </div>
    </td>
    <td style="width:50%;padding:6px;vertical-align:top">
      <div style="border:1px solid {palette["line"]};border-radius:12px;padding:13px;background:#edf0e6">
        <div style="font:600 10px Arial,sans-serif;letter-spacing:1px;text-transform:uppercase;color:{palette["muted"]}">Next-week focus</div>
        <ol style="margin:8px 0 0;padding-left:18px;font:12px Arial,sans-serif;line-height:1.45;color:#4d473d">{priorities_html}</ol>
      </div>
    </td>
  </tr></table>

  <div style="margin-top:16px;border-left:3px solid {palette["honey"]};padding:9px 12px;background:#f4efe4;font:11px Arial,sans-serif;color:#5f604c">
    Coverage: {html.escape(coverage_text)}. {quality_count} workout record(s) carry a data-quality note and remain visible rather than silently corrected.
  </div>
  <div style="margin-top:20px;padding-top:12px;border-top:1px solid #d8d0c0;font-size:10px;color:#9a9385">
    FitLit · Pacific time · automated personal health summary
  </div>
</div></body></html>"""

    plain = [
        "FITLIT WEEKLY PERFORMANCE CATALOG",
        week["label"],
        "",
        "AT A GLANCE",
        f"Training sessions: {training['training_sessions']}",
        f"Training time: {training['training_duration_min']} min",
        f"Exercise energy: {training['exercise_calories']:,} kcal",
        f"Active-zone load: {training['active_zone_minutes']} min",
        f"Average steps: {_fmt(activity['avg_steps'])}/day",
        f"Total energy out: {activity['total_calories_out']:,} kcal",
        f"Average sleep: {_fmt(sleep['avg_hours'], 'h', 2)}",
        f"Average HRV: {_fmt(recovery['avg_hrv_ms'], ' ms', 1)}",
        "",
        "DAILY RHYTHM",
        *[
            f"{day['label']}: {_fmt(day.get('steps'))} steps · "
            f"{_fmt(day.get('calories_out'))} kcal out · "
            f"{_fmt(day.get('sleep_hours'), 'h sleep', 2)}"
            for day in catalog["daily"]
        ],
        "",
        "WORKOUT CATALOG",
        *[
            f"{session['day']} {session['start']} — {session['name']}: "
            f"{session['duration_min']} min · {session['calories']} kcal · "
            f"{session['active_zone_minutes']} zone min"
            + (
                f" · quality note: {'; '.join(session['quality_flags'])}"
                if session["quality_flags"] else ""
            )
            for session in catalog["sessions"]
        ],
        "",
        "RECOVERY AND PHYSIOLOGY",
        *[f"{label}: {value} · {context}" for label, value, context in recovery_rows],
        "",
        "WHAT STOOD OUT",
        *[f"- {item}" for item in catalog["insights"]],
        "",
        "NEXT-WEEK FOCUS",
        *[f"{index}. {item}" for index, item in enumerate(catalog["priorities"], 1)],
        "",
        f"Coverage: {coverage_text}",
        "Stress is not directly measured; the strain proxy combines HRV and resting heart rate.",
    ]
    return Report(subject=subject, text="\n".join(plain), html=body)


def append_ai_insight(
    rendered: Report,
    *,
    headline: str,
    observations: tuple[str, ...],
    confidence: float,
    provider: str,
) -> Report:
    """Append already-validated AI text while escaping every rendered value."""
    items = "".join(
        f'<li style="margin:7px 0">{html.escape(item)}</li>' for item in observations
    )
    block = (
        '<div style="margin-top:16px;border:1px solid #d8d0c0;border-radius:12px;'
        'padding:13px;background:#f8f4eb">'
        '<div style="font:600 10px Arial,sans-serif;letter-spacing:1.5px;'
        'text-transform:uppercase;color:#817a6c">AI observations</div>'
        f'<div style="margin-top:6px;font:italic 18px Georgia,serif;color:#28231b">'
        f'{html.escape(headline)}</div>'
        f'<ul style="margin:8px 0 0;padding-left:18px;font:13px Arial,sans-serif;'
        f'line-height:1.45;color:#4d473d">{items}</ul>'
        f'<div style="margin-top:8px;font:10px Arial,sans-serif;color:#9a9385">'
        f'{html.escape(provider.title())} · confidence {confidence:.0%}</div></div>'
    )
    marker = '<div style="margin-top:20px;padding-top:12px;'
    enriched_html = rendered.html.replace(marker, block + marker, 1)
    enriched_text = "\n".join([
        rendered.text,
        "",
        f"AI observations — {headline}",
        *(f"- {item}" for item in observations),
        f"{provider.title()} · confidence {confidence:.0%}",
    ])
    return Report(subject=rendered.subject, text=enriched_text, html=enriched_html)


def report(
    *,
    subject: str,
    kicker: str,
    title: str,
    subtitle: str,
    metrics: list[Metric],
    details: list[tuple[str, str]],
    note: str | None = None,
) -> Report:
    metric_cells = []
    for metric in metrics:
        metric_cells.append(
            '<td style="width:50%;padding:7px;vertical-align:top">'
            '<div style="border:1px solid #ded7c7;border-radius:12px;padding:13px;background:#f4efe4">'
            f'<div style="font:600 11px Arial,sans-serif;color:#756f62">{html.escape(metric.label)}</div>'
            f'<div style="margin-top:5px;font:italic 30px Georgia,serif;color:{metric.accent}">'
            f'{html.escape(metric.value)} <span style="font:500 11px Arial,sans-serif;color:#8b8578">'
            f'{html.escape(metric.unit)}</span></div></div></td>'
        )
    metric_rows = "".join(
        f"<tr>{''.join(metric_cells[index:index + 2])}</tr>"
        for index in range(0, len(metric_cells), 2)
    )
    detail_rows = "".join(
        '<tr>'
        f'<td style="padding:9px 0;border-top:1px solid #e2dbcc;font:12px Arial,sans-serif;color:#817a6c">{html.escape(label)}</td>'
        f'<td style="padding:9px 0;border-top:1px solid #e2dbcc;text-align:right;font:600 12px Arial,sans-serif;color:#28231b">{html.escape(value)}</td>'
        '</tr>'
        for label, value in details
    )
    note_html = (
        f'<div style="margin-top:16px;border-left:3px solid #7c8154;padding:9px 12px;'
        f'background:#edf0e6;font:italic 14px Georgia,serif;color:#5f604c">{html.escape(note)}</div>'
        if note else ""
    )
    body = f"""<!doctype html>
<html><body style="margin:0;background:#ede7d8;color:#221e16">
<div style="max-width:620px;margin:0 auto;padding:26px 18px;font-family:Arial,sans-serif">
  <div style="font-size:10px;letter-spacing:2px;text-transform:uppercase;color:#9a9385">{html.escape(kicker)}</div>
  <h1 style="margin:5px 0 3px;font:normal 34px Georgia,serif">{html.escape(title)}</h1>
  <div style="font-size:12px;color:#817a6c">{html.escape(subtitle)}</div>
  <table role="presentation" style="width:100%;border-spacing:0;margin:14px -7px 0">{metric_rows}</table>
  <table role="presentation" style="width:100%;border-collapse:collapse;margin-top:12px">{detail_rows}</table>
  {note_html}
  <div style="margin-top:20px;padding-top:12px;border-top:1px solid #d8d0c0;font-size:10px;color:#9a9385">
    FitLit · Pacific time · automated personal health summary
  </div>
</div></body></html>"""
    plain = [title, subtitle, ""]
    plain.extend(f"{metric.label}: {metric.value} {metric.unit}".rstrip() for metric in metrics)
    plain.append("")
    plain.extend(f"{label}: {value}" for label, value in details)
    if note:
        plain.extend(["", note])
    return Report(subject=subject, text="\n".join(plain), html=body)
