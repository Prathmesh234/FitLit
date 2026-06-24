"""Body-recomposition tracker — the math behind the sub-15% goal.

The user's stated goal (body-comp handoff) is **sub-15% body fat with concurrent
hypertrophy**. The scale alone can't tell you if you're getting there — on
creatine and lifting, bodyweight can stay flat while fat drops and muscle rises.
This module turns the few hard numbers we *do* have into a recomposition plan:

    LBM            = weight × (1 − bodyfat%)          # lean body mass held constant
    target weight  = LBM ÷ (1 − target_bodyfat%)
    fat to lose    = current weight − target weight

…then layers a *safe* rate (≈0.5–0.75%/week, the handoff's deficit guidance) to
give an ETA and a daily-deficit target, and reads the journal's fasted weigh-ins
to show actual progress against the plan.

Body-fat % defaults to the handoff's visual estimate (~20%); pass a measured
value (DEXA) for a hard baseline. Everything is read-only + stdlib.
"""
from __future__ import annotations

from fitlit import insights, journal

LB_PER_KG = 2.2046226218

# The body-comp handoff (data/Body-Comp-HandOff) placed the user at ~20% body fat
# from photos (±3–4%). Used as the default until a DEXA gives a hard baseline.
DEFAULT_BODYFAT_PCT = 20.0
# Handoff strategy: target slightly below 15% so post-diet rebound still lands sub-15.
DEFAULT_TARGET_BODYFAT_PCT = 14.0
# Height on record (body.db height = 1778 mm), for BMI context.
HEIGHT_M = 1.778
# Safe fat-loss pace as a fraction of bodyweight per week (handoff: ~0.5–0.75%).
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


def bmi(weight_kg: float, height_m: float = HEIGHT_M) -> float:
    return round(weight_kg / (height_m * height_m), 1)


def plan(
    *,
    bodyfat_pct: float = DEFAULT_BODYFAT_PCT,
    target_bodyfat_pct: float = DEFAULT_TARGET_BODYFAT_PCT,
    weekly_rate_pct: float = DEFAULT_WEEKLY_RATE_PCT,
    weight_kg: float | None = None,
) -> dict:
    """Compute the full recomposition plan from the latest fasted weight.

    Returns current state, target weight, fat-to-lose, a safe weekly rate, an ETA
    in weeks, and the daily calorie deficit that pace implies — all the numbers a
    coach needs to turn "the scale isn't moving" into a concrete trajectory.
    """
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
            "bodyfat_source": "body-comp handoff visual estimate (±3-4%); use DEXA for a hard baseline",
            "target_bodyfat_pct": target_bodyfat_pct,
            "weekly_rate_pct": weekly_rate_pct,
        },
        "current": {
            "weight_kg": round(weight_kg, 1),
            "weight_lb": round(weight_kg * LB_PER_KG, 1),
            "bmi": bmi(weight_kg),
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
