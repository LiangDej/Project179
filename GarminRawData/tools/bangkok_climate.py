#!/usr/bin/env python3
"""
bangkok_climate.py — Shared Bangkok early-morning climate model (single source).

Why: several tools heat-correct outdoor paces (Ely et al. 2007). They used to
assume a FLAT 30°C, which over-corrects cool-season runs (Nov–Feb mornings are
~24–26°C) and inflates the resulting VDOT estimate. This module gives a
month-aware early-morning (≈05:30–07:00, when this athlete trains) dry-bulb
temperature so the correction tracks the actual season.

Source: Thai Meteorological Department climatology for Bangkok metropolitan,
daily-minimum / early-morning band (monthly normals, 1991–2020). Values are the
typical temperature at the athlete's run time, not the daytime max.

⚠️ Training-location-specific: only correct for athletes actually training in
Bangkok. This is used only as a FALLBACK inside weather_adjuster.py when a live
weather fetch isn't available (no OPENWEATHER_API_KEY, or the API call failed)
— an athlete training somewhere else (Chiang Mai, Japan, Europe, etc.) who
hits this fallback would get Bangkok's heat/humidity applied to their own
paces. Set OPENWEATHER_API_KEY (see .env.example) to always use live weather
for your actual location instead of this fallback.

    morning_temp_c(month)        -> float  (dry-bulb °C at run time)
    morning_temp_c_for(date_obj) -> float
"""
from datetime import date

# Month → (early-morning dry-bulb °C, RH %, dew point °C, wind m/s) in Bangkok.
# The ONE climate table (v1.8: weather_adjuster had a second, slightly
# different copy). TMD 1991–2020 normals at the athlete's run time.
BANGKOK_CLIMATE = {
    1:  (25.0, 70, 18.5, 1.8),
    2:  (26.0, 68, 19.5, 1.9),
    3:  (28.0, 72, 21.5, 2.0),
    4:  (29.5, 75, 23.0, 2.1),
    5:  (29.0, 79, 24.0, 2.2),
    6:  (28.0, 82, 24.5, 2.3),
    7:  (27.5, 83, 24.5, 2.2),
    8:  (27.5, 84, 24.5, 2.2),
    9:  (27.0, 84, 24.0, 1.8),
    10: (27.0, 80, 23.0, 1.7),
    11: (26.0, 75, 21.0, 1.6),
    12: (24.5, 68, 18.0, 1.7),
}
BANGKOK_MORNING_TEMP_C = {m: v[0] for m, v in BANGKOK_CLIMATE.items()}

# Daniels/Ely reference temperature — no heat penalty at/below this
ELY_NEUTRAL_C = 13.0
# Ely et al. 2007: ~0.4% pace slowdown per °C above neutral (fraction form)
ELY_PCT_PER_C = 0.004


def morning_temp_c(month: int) -> float:
    """Typical Bangkok early-morning dry-bulb temp (°C) for a calendar month."""
    return BANGKOK_MORNING_TEMP_C.get(int(month), 28.0)


def morning_temp_c_for(d: date) -> float:
    return morning_temp_c(d.month)


def ely_penalty_fraction(temp_c: float) -> float:
    """Ely heat penalty as a fraction (e.g. 0.064 = +6.4% pace cost).
    THE heat model — race_pace_planner and weather_adjuster build on this."""
    return max(0.0, (temp_c - ELY_NEUTRAL_C) * ELY_PCT_PER_C)


if __name__ == "__main__":
    print("Bangkok early-morning dry-bulb temp + Ely heat penalty by month:")
    for m in range(1, 13):
        t = morning_temp_c(m)
        print(f"  month {m:>2}: {t:>4.1f}°C  → Ely penalty +{ely_penalty_fraction(t)*100:.1f}%")
