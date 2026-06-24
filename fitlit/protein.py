"""Vegetarian protein tracker — hit the muscle-sparing target while cutting.

In a deficit, protein is the lever that preserves lean mass (the body-comp
handoff: ``1.6–2.2 g/kg/day``, skew high while cutting). The user is vegetarian,
so this module computes their daily target from the latest weigh-in, compares it
to logged intake, and — crucially — suggests how to close any gap using *their*
vegetarian sources (eggs, whey, tofu, protein powder, paneer, lentils…).

It reads the latest weight + logged protein from the journal; nothing here is a
medical prescription, just the standard sports-nutrition range applied to real
numbers. Read-only + stdlib.
"""
from __future__ import annotations

from fitlit import journal

# Protein per common vegetarian serving (grams). Conservative, label-typical
# values; biased to the user's stated staples (eggs, whey, tofu, protein powder).
VEG_SOURCES: dict[str, dict] = {
    "whey/protein scoop": {"protein_g": 24, "serving": "1 scoop (~30g)"},
    "egg":                {"protein_g": 6,  "serving": "1 large"},
    "paneer":             {"protein_g": 18, "serving": "100 g"},
    "firm tofu":          {"protein_g": 12, "serving": "100 g"},
    "greek yogurt":       {"protein_g": 10, "serving": "100 g"},
    "lentils (cooked)":   {"protein_g": 9,  "serving": "100 g"},
    "milk":               {"protein_g": 8,  "serving": "250 ml"},
    "oats":               {"protein_g": 5,  "serving": "40 g dry"},
}

# g protein per kg bodyweight. Handoff range; recommend the upper end on a cut.
RATE_MIN, RATE_MAX = 1.6, 2.2
RATE_CUTTING = 2.0


def target_g(weight_kg: float | None = None, *, cutting: bool = True) -> dict:
    """Daily protein target band + a recommended number from the latest weight."""
    if weight_kg is None:
        weight_kg, _ = _latest_weight_kg()
    if weight_kg is None:
        return {"error": "no weigh-in logged — log a weight to set a protein target"}
    rate = RATE_CUTTING if cutting else 1.8
    return {
        "weight_kg": round(weight_kg, 1),
        "min_g": round(weight_kg * RATE_MIN),
        "max_g": round(weight_kg * RATE_MAX),
        "recommended_g": round(weight_kg * rate),
        "rate_g_per_kg": rate,
        "context": "cutting — skew high to spare lean mass" if cutting else "maintenance",
    }


def daily_status(date: str | None = None, *, cutting: bool = True) -> dict:
    """Logged protein vs target for a day, with a gap + how to close it."""
    date = date or journal.today()
    tgt = target_g(cutting=cutting)
    if "error" in tgt:
        return tgt

    logged = next((m.get("total_protein_g") for m in journal.recent_meals(60)
                   if m["date"] == date), None)
    out = {
        "date": date,
        "target_g": tgt["recommended_g"],
        "target_band_g": [tgt["min_g"], tgt["max_g"]],
        "logged_g": logged,
    }
    if logged is None:
        out["note"] = ("no protein logged for this day yet — set meals.total_protein_g "
                       "(e.g. via journal.log_meal) so the gap can be tracked.")
        out["suggestions"] = suggest_to_close(tgt["recommended_g"])
        return out

    gap = round(tgt["recommended_g"] - logged, 1)
    out["gap_g"] = gap
    out["status"] = "met" if gap <= 0 else "short"
    if gap > 0:
        out["suggestions"] = suggest_to_close(gap)
    return out


def suggest_to_close(gap_g: float) -> list[str]:
    """Greedy vegetarian combos to make up a protein gap, densest sources first."""
    if gap_g <= 0:
        return []
    suggestions = []
    for name in ("whey/protein scoop", "egg", "paneer", "firm tofu", "greek yogurt"):
        per = VEG_SOURCES[name]["protein_g"]
        n = round(gap_g / per)
        if n >= 1:
            suggestions.append(
                f"{n}x {name} ({VEG_SOURCES[name]['serving']}) ≈ {n * per}g")
    return suggestions[:4]


def sources() -> dict:
    """The vegetarian protein reference table."""
    return VEG_SOURCES


def _latest_weight_kg() -> tuple[float | None, str | None]:
    rows = journal.recent_weights(limit=30)
    for r in rows:
        cond = (r.get("conditions") or "").lower()
        if "fasted" in cond and "not fasted" not in cond and r.get("weight_kg"):
            return r["weight_kg"], r["date"]
    for r in rows:
        if r.get("weight_kg"):
            return r["weight_kg"], r["date"]
    return None, None


if __name__ == "__main__":
    import json
    print(json.dumps(target_g(), indent=2))
    print(json.dumps(daily_status(), indent=2))
