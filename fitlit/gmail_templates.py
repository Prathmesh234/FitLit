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
