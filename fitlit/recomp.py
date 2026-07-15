"""Configurable body-recomposition tracker.

The scale alone cannot distinguish fat loss from lean-mass change. This module
turns configured body-composition assumptions and logged weigh-ins into a
simple trajectory:

    LBM            = weight × (1 − bodyfat%)          # lean body mass held constant
    target weight  = LBM ÷ (1 − target_bodyfat%)
    fat to lose    = current weight − target weight

It also reads fasted weigh-ins to show actual progress. Personal estimates,
height, and targets belong in .env rather than source control.
"""
from __future__ import annotations

from fitlit import config, insights, journal

LB_PER_KG = 2.2046226218
DEFAULT_WEEKLY_RATE_PCT = 0.6
# 1 kg of body fat ≈ 7700 kcal — the deficit needed to shed it.
KCAL_PER_KG_FAT = 7700


def latest_fasted_weight_kg() -> tuple[float | None, str | None]:
    """Most recent *fasted* weigh-in (kg, date) — the only scale number worth
    planning from. Falls back to the latest reading of any kind if none fasted."""
    rows = journal.recent_weights(limit=60)
    for r in rows:
        cond = (r.get("conditions") or "").lower()
        if "fasted" in cond and "not fasted" not in cond and r.get("weight_kg"):
            return r["weight_kg"], r["date"]
    for r in rows:  # fallback: any reading
        if r.get("weight_kg"):
            return r["weight_kg"], r["date"]
    return None, None


def lbm_kg(weight_kg: float, bodyfat_pct: float) -> float:
    """Lean body mass in kg."""
    return weight_kg * (1 - bodyfat_pct / 100.0)


def target_weight_kg(lbm: float, target_bodyfat_pct: float) -> float:
    """Goal scale weight that puts ``lbm`` at the target body-fat %."""
    return lbm / (1 - target_bodyfat_pct / 100.0)


def bmi(weight_kg: float, height_m: float) -> float:
    return round(weight_kg / (height_m * height_m), 1)


def plan(
    *,
    bodyfat_pct: float | None = None,
    target_bodyfat_pct: float | None = None,
    weekly_rate_pct: float = DEFAULT_WEEKLY_RATE_PCT,
    weight_kg: float | None = None,
    height_m: float | None = None,
) -> dict:
    """Compute the full recomposition plan from the latest fasted weight.

    Returns current state, target weight, fat-to-lose, a safe weekly rate, an ETA
    in weeks, and the daily calorie deficit that pace implies — all the numbers a
    coach needs to turn "the scale isn't moving" into a concrete trajectory.
    """
    bodyfat_pct = bodyfat_pct if bodyfat_pct is not None else config.BODY_FAT_ESTIMATE_PCT
    target_bodyfat_pct = (
        target_bodyfat_pct
        if target_bodyfat_pct is not None
        else config.TARGET_BODY_FAT_PCT
    )
    height_m = height_m if height_m is not None else config.HEIGHT_M
    if bodyfat_pct is None or target_bodyfat_pct is None:
        return {
            "error": "configure FITLIT_BODY_FAT_ESTIMATE_PCT and "
            "FITLIT_TARGET_BODY_FAT_PCT to enable the recomposition model"
        }
    date = None
    if weight_kg is None:
        weight_kg, date = latest_fasted_weight_kg()
    if weight_kg is None:
        return {"error": "no weigh-in logged yet — log a fasted weight first"}

    lbm = lbm_kg(weight_kg, bodyfat_pct)
    target_kg = target_weight_kg(lbm, target_bodyfat_pct)
    fat_to_lose_kg = max(0.0, weight_kg - target_kg)

    weekly_loss_kg = weight_kg * weekly_rate_pct / 100.0
    weeks = round(fat_to_lose_kg / weekly_loss_kg, 1) if weekly_loss_kg else None
    daily_deficit = round(weekly_loss_kg * KCAL_PER_KG_FAT / 7) if weekly_loss_kg else None

    return {
        "as_of": date,
        "assumptions": {
            "bodyfat_pct": bodyfat_pct,
            "bodyfat_source": "configured estimate; use a measured value for a stronger baseline",
            "target_bodyfat_pct": target_bodyfat_pct,
            "weekly_rate_pct": weekly_rate_pct,
        },
        "current": {
            "weight_kg": round(weight_kg, 1),
            "weight_lb": round(weight_kg * LB_PER_KG, 1),
            "bmi": bmi(weight_kg, height_m) if height_m else None,
            "lean_mass_kg": round(lbm, 1),
            "fat_mass_kg": round(weight_kg - lbm, 1),
        },
        "target": {
            "bodyfat_pct": target_bodyfat_pct,
            "weight_kg": round(target_kg, 1),
            "weight_lb": round(target_kg * LB_PER_KG, 1),
        },
        "fat_to_lose_kg": round(fat_to_lose_kg, 1),
        "fat_to_lose_lb": round(fat_to_lose_kg * LB_PER_KG, 1),
        "safe_weekly_loss_lb": round(weekly_loss_kg * LB_PER_KG, 2),
        "eta_weeks": weeks,
        "daily_deficit_kcal": daily_deficit,
        "note": "LBM held constant (the recomp assumption). Lifting + adequate protein "
                "are what keep that true — lose strength and the model breaks.",
    }


def progress(days: int = 30) -> dict:
    """Fasted-weight trend vs the plan: are we actually moving toward target?"""
    trend = insights.weight_trend(days, fasted_only=True)
    p = plan()
    out = {
        "fasted_readings": trend["n_readings"],
        "latest_avg7_lb": trend["avg7_lb"],
        "trend_lb": trend["trend_lb"],
        "target_lb": p.get("target", {}).get("weight_lb"),
    }
    if trend["avg7_lb"] is not None and out["target_lb"] is not None:
        out["to_go_lb"] = round(trend["avg7_lb"] - out["target_lb"], 1)
    if trend["n_readings"] < 5:
        out["caveat"] = ("need ≥5 fasted morning readings for a reliable trend; "
                         "keep logging fasted weigh-ins.")
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(plan(), indent=2))
    print(json.dumps(progress(), indent=2))
