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

# Month (1–12) → typical early-morning dry-bulb °C in Bangkok
BANGKOK_MORNING_TEMP_C = {
    1: 25.0,   # ม.ค. cool season
    2: 26.0,   # ก.พ.
    3: 28.0,   # มี.ค. heating up
    4: 29.5,   # เม.ย. hottest
    5: 29.0,   # พ.ค.
    6: 28.0,   # มิ.ย. rainy
    7: 27.5,   # ก.ค.
    8: 27.5,   # ส.ค.
    9: 27.0,   # ก.ย.
    10: 27.0,  # ต.ค.
    11: 26.0,  # พ.ย. cool returns (ATM race month)
    12: 24.5,  # ธ.ค. coolest mornings
}

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
    """Ely heat penalty as a fraction (e.g. 0.064 = +6.4% pace cost)."""
    return max(0.0, (temp_c - ELY_NEUTRAL_C) * ELY_PCT_PER_C)


if __name__ == "__main__":
    print("Bangkok early-morning dry-bulb temp + Ely heat penalty by month:")
    for m in range(1, 13):
        t = morning_temp_c(m)
        print(f"  month {m:>2}: {t:>4.1f}°C  → Ely penalty +{ely_penalty_fraction(t)*100:.1f}%")
