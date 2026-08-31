"""Email rendering for personal-section tasks.

The health reports in `fitlit/gmail_templates.py` use a warm paper palette; the
personal mail keeps that family so both come from one assistant, but shifts to
espresso and crema tones so the owner can tell at a glance which half of FitLit
is writing. Everything is inline-styled table markup, because that is what
Gmail renders reliably.
"""
from __future__ import annotations

import html
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from fitlit.gmail_templates import Report

PALETTE = {
    "paper": "#efe7d9",
    "card": "#faf6ee",
    "ink": "#241d16",
    "espresso": "#5b3a26",
    "crema": "#b3793f",
    "sage": "#6b7a5a",
    "muted": "#8a8172",
    "line": "#ddd1bd",
}

_NOISE_COPY = {
    "very quiet": "Very quiet",
    "quiet": "Quiet",
    "moderate": "Gentle hum",
}


def _e(value: Any) -> str:
    return html.escape(str(value or "").strip())


def _host(url: str) -> str:
    netloc = urlsplit(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _stat(label: str, value: str, unit: str, accent: str) -> str:
    return (
        '<td style="width:33.3%;padding:6px;vertical-align:top">'
        f'<div style="border:1px solid {PALETTE["line"]};border-radius:12px;'
        f'padding:12px;background:{PALETTE["card"]}">'
        f'<div style="font:600 10px Arial,sans-serif;color:{PALETTE["muted"]};'
        f'text-transform:uppercase;letter-spacing:.8px">{_e(label)}</div>'
        f'<div style="margin-top:6px;font:italic 24px Georgia,serif;'
        f'color:{accent};line-height:1.15">{_e(value)}'
        + (
            f' <span style="font:500 10px Arial,sans-serif;'
            f'color:{PALETTE["muted"]}">{_e(unit)}</span>'
            if unit
            else ""
        )
        + "</div></div></td>"
    )


def _rows(details: list[tuple[str, str]]) -> str:
    return "".join(
        "<tr>"
        f'<td style="padding:9px 0;border-top:1px solid {PALETTE["line"]};'
        f'font:12px Arial,sans-serif;color:{PALETTE["muted"]};width:38%;'
        f'vertical-align:top">{_e(label)}</td>'
        f'<td style="padding:9px 0;border-top:1px solid {PALETTE["line"]};'
        f'text-align:right;font:600 12px Arial,sans-serif;'
        f'color:{PALETTE["ink"]}">{_e(value)}</td>'
        "</tr>"
        for label, value in details
        if str(value or "").strip()
    )


def coffee_subject(shop: dict[str, Any], day: date) -> str:
    return (
        f"Coffee today | {shop['name']} · {shop['neighborhood']} | "
        f"{day.strftime('%a, %b %-d')}"
    )


def coffee_report(
    shop: dict[str, Any],
    day: date,
    *,
    repeat_of_day: str | None = None,
) -> Report:
    """Render one daily coffee recommendation as a Gmail-safe report."""
    noise = _NOISE_COPY.get(shop.get("noise_level", ""), shop.get("noise_level", "—"))
    subject = coffee_subject(shop, day)
    stats = "".join([
        _stat("Drive from SLU", str(shop["drive_minutes"]), "min", PALETTE["espresso"]),
        _stat("Open today", shop["hours_today"], "", PALETTE["crema"]),
        _stat("Room", noise, "", PALETTE["sage"]),
    ])

    details = [
        ("Address", shop.get("address", "")),
        ("Today's hours", shop.get("hours_today", "")),
        ("Hours confirmed via", shop.get("hours_source", "")),
        ("Best time to go", shop.get("best_time", "")),
        ("Seating", shop.get("seating", "")),
        ("Wi-Fi and outlets", shop.get("wifi_outlets", "")),
        ("Order this", shop.get("signature_order", "")),
        ("Food", shop.get("food_note", "")),
        ("Getting there", shop.get("drive_note", "")),
    ]

    notes = []
    if str(shop.get("hours_note") or "").strip():
        notes.append(("Hours note", shop["hours_note"]))
    if repeat_of_day:
        notes.append(
            (
                "Repeat",
                f"This one came around again — it was last sent on {repeat_of_day}.",
            )
        )
    note_html = "".join(
        f'<div style="margin-top:12px;border-left:3px solid {PALETTE["crema"]};'
        f'padding:9px 12px;background:#f6ece0;font:13px Arial,sans-serif;'
        f'color:{PALETTE["espresso"]}"><strong>{_e(label)}:</strong> '
        f"{_e(text)}</div>"
        for label, text in notes
    )

    sources = shop.get("sources") or []
    source_html = " · ".join(
        f'<a href="{_e(url)}" style="color:{PALETTE["espresso"]};'
        f'text-decoration:underline">{_e(_host(url))}</a>'
        for url in sources
    )

    body = f"""<!doctype html>
<html><body style="margin:0;background:{PALETTE["paper"]};color:{PALETTE["ink"]}">
<div style="max-width:620px;margin:0 auto;padding:26px 18px;font-family:Arial,sans-serif">
  <div style="font-size:10px;letter-spacing:2px;text-transform:uppercase;color:{PALETTE["muted"]}">
    Personal &middot; Coffee of the day
  </div>
  <h1 style="margin:6px 0 3px;font:normal 34px Georgia,serif;color:{PALETTE["ink"]}">{_e(shop["name"])}</h1>
  <div style="font-size:12px;color:{PALETTE["muted"]}">
    {_e(shop["neighborhood"])} &middot; {_e(day.strftime("%A, %B %-d, %Y"))} &middot; Pacific time
  </div>
  <div style="margin-top:10px;font:italic 16px Georgia,serif;color:{PALETTE["espresso"]}">
    {_e(shop.get("one_liner", ""))}
  </div>

  <table role="presentation" style="width:100%;border-spacing:0;margin:14px -6px 0">
    <tr>{stats}</tr>
  </table>

  <div style="margin-top:16px;font:14px/1.55 Arial,sans-serif;color:{PALETTE["ink"]}">
    {_e(shop.get("why_today", ""))}
  </div>

  <div style="margin-top:14px;border-left:3px solid {PALETTE["sage"]};padding:9px 12px;
              background:#eef0e7;font:italic 13px/1.5 Georgia,serif;color:#4d5541">
    {_e(shop.get("vibe", ""))}
    <div style="margin-top:6px;font:11px Arial,sans-serif;color:{PALETTE["muted"]}">
      Noise read: {_e(shop.get("noise_evidence", ""))}
    </div>
  </div>

  <table role="presentation" style="width:100%;border-collapse:collapse;margin-top:16px">
    {_rows(details)}
  </table>
  {note_html}

  <div style="margin-top:18px">
    <a href="{_e(shop["google_maps_url"])}"
       style="display:inline-block;padding:11px 18px;border-radius:9px;
              background:{PALETTE["espresso"]};color:#faf6ee;font:600 13px Arial,sans-serif;
              text-decoration:none">Open in Google Maps</a>
    {(
        f'<a href="{_e(shop["website"])}" style="display:inline-block;margin-left:10px;'
        f'padding:11px 18px;border-radius:9px;border:1px solid {PALETTE["line"]};'
        f'color:{PALETTE["espresso"]};font:600 13px Arial,sans-serif;'
        f'text-decoration:none">Shop website</a>'
        if str(shop.get("website") or "").strip() else ""
    )}
  </div>

  <div style="margin-top:20px;padding-top:12px;border-top:1px solid {PALETTE["line"]};
              font-size:10px;color:{PALETTE["muted"]};line-height:1.6">
    Hours and status checked live on {_e(shop.get("verified_date", day.isoformat()))} (Pacific).
    Sources: {source_html or "—"}<br>
    FitLit &middot; personal assistant &middot; reply with what you thought of it
  </div>
</div></body></html>"""

    plain = [
        f"{shop['name']} — {shop['neighborhood']}",
        day.strftime("%A, %B %-d, %Y") + " · Pacific time",
        "",
        str(shop.get("one_liner", "")),
        "",
        f"Drive from SLU: {shop['drive_minutes']} min",
        f"Open today: {shop['hours_today']}",
        f"Room: {noise}",
        "",
        str(shop.get("why_today", "")),
        "",
        str(shop.get("vibe", "")),
        f"Noise read: {shop.get('noise_evidence', '')}",
        "",
    ]
    plain.extend(
        f"{label}: {value}" for label, value in details if str(value or "").strip()
    )
    plain.extend(f"{label}: {text}" for label, text in notes)
    plain.extend([
        "",
        f"Google Maps: {shop['google_maps_url']}",
    ])
    if str(shop.get("website") or "").strip():
        plain.append(f"Website: {shop['website']}")
    plain.extend([
        "",
        f"Hours and status checked live on "
        f"{shop.get('verified_date', day.isoformat())} (Pacific).",
        "Sources: " + (", ".join(sources) if sources else "—"),
    ])
    return Report(subject=subject, text="\n".join(plain), html=body)
