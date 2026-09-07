"""
weather_adjuster.py — Race-day weather → adjusted pace + HR targets
Uses OpenWeather API + Ely et al. 2007 heat penalty + WBGT (Stull 2011)

Usage:
    python3 weather_adjuster.py --race sponsor21
    python3 weather_adjuster.py --race bangsaen --date 2026-11-15 --time 03:00
    python3 weather_adjuster.py --manual --temp 24 --humidity 80 --wind 2 --dew 20
    python3 weather_adjuster.py --race sponsor21 --forecast # 3-day lookahead @ race time

Setup:
    Add OPENWEATHER_API_KEY=xxx to ~/.config/garmin-coach/.env
    Get free key at https://openweathermap.org/api
"""

from __future__ import annotations
import sys, os, json, math, argparse
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
_mcp = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "skills", "garmin_coach_mcp"))
if os.path.isdir(_mcp):
    sys.path.insert(0, _mcp)
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.config/garmin-coach/.env"))

try:
    from config import ATHLETE, VDOT_PACES, HR_ZONE_BOUNDS
except ImportError:
    print("❌ config.py not found — run from GarminRawData/tools/")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Bangkok monthly climate averages (6 AM race time approximation)
# Source: Thai Meteorological Department 30-year normals + personal observations
# Used as fallback when no API key and no --manual values given
# ---------------------------------------------------------------------------
BANGKOK_CLIMATE = {
    #  month: (temp_c, humidity_pct, dew_c, wind_mps)
    1:  (24.5, 70, 18.5, 1.8),
    2:  (26.0, 68, 19.5, 1.9),
    3:  (28.0, 72, 21.5, 2.0),
    4:  (29.5, 75, 23.0, 2.1),
    5:  (29.0, 79, 24.0, 2.2),
    6:  (28.5, 82, 24.5, 2.3),
    7:  (28.0, 83, 24.5, 2.2),
    8:  (28.0, 84, 24.5, 2.2),
    9:  (27.5, 84, 24.0, 1.8),
    10: (27.0, 80, 23.0, 1.7),
    11: (26.0, 75, 21.0, 1.6),
    12: (24.5, 68, 18.0, 1.7),
}

# Thailand Local Race (Bangkok, early December) 6 AM averages
FUJI_CLIMATE = {
    12: (24.5, 68, 18.0, 1.7),
}


def get_climate_fallback(race: str, month: int | None = None) -> dict:
    """Return typical climate dict for a race location + month.
    Tries race-specific expected conditions from races.json first,
    then falls back to monthly Bangkok/Fuji climate averages.
    """
    from datetime import date as _date
    m = month or _date.today().month

    # Race-specific expected conditions from races.json (single source of truth)
    try:
        from race_registry import list_races as _lr_cf
        _all_races = dict(_lr_cf(active_only=False))
        r = _all_races.get(race, {})
        if r.get("expected_temp_c"):
            return {
                "temp":     float(r["expected_temp_c"]),
                "humidity": float(r.get("expected_humidity", 75)),
                "dew":      float(r.get("expected_dew_c", r["expected_temp_c"] - 5)),
                "wind":     float(r.get("expected_wind_mps", 1.5)),
                "desc":     f"expected conditions ({r.get('location_name', r.get('name', race))})",
            }
    except Exception:
        pass

    # Generic monthly fallback
    if race == "fuji":
        t, rh, dew, wind = FUJI_CLIMATE.get(m, (3.0, 55, -4.0, 2.5))
    else:
        t, rh, dew, wind = BANGKOK_CLIMATE.get(m, (28.0, 80, 23.0, 2.0))
    return {
        "temp": t, "humidity": rh, "dew": dew,
        "wind": wind, "desc": f"climate avg (month {m})",
    }


# ---------------------------------------------------------------------------
# Race locations — loaded from races.json via race_registry (single source of truth)
# Falls back to hardcoded Bangkok coords if race_registry unavailable
# ---------------------------------------------------------------------------
_BANGKOK_DEFAULT = {"name": "Bangkok, Thailand", "lat": 13.7563, "lon": 100.5018, "tz_offset": 7}
try:
    from race_registry import list_races as _lr_wa
    RACE_LOCATIONS = {
        k: {
            "name": r.get("location_name", r.get("name", k)),
            "lat":  r["lat"],
            "lon":  r["lon"],
            "tz_offset": r.get("tz_offset", 7),
        }
        for k, r in _lr_wa(active_only=False)
        if r.get("lat") and r.get("lon")
    }
    if not RACE_LOCATIONS:
        raise ValueError("empty")
except Exception:
    RACE_LOCATIONS = {
        "bangsaen": {"name": "Bangsaen Beach, Chonburi", "lat": 13.2848, "lon": 100.9222, "tz_offset": 7},
        "sponsor21": {"name": "พระราม 8 Bridge, Bangkok", "lat": 13.7735, "lon": 100.4893, "tz_offset": 7},
        "hm":       {"name": "พระราม 8 Bridge, Bangkok", "lat": 13.7735, "lon": 100.4893, "tz_offset": 7},
        "fuji":     {"name": "Fuji Marathon, Yamanashi, Japan", "lat": 35.3606, "lon": 138.7274, "tz_offset": 9},
    }

# ---------------------------------------------------------------------------
# WBGT — Stull (2011) J.Appl.Meteor.Climatol. 50:2267
# Approximates wet-bulb temp from dry-bulb + RH; globe temp replaced by 0.3*Tdb
# Error vs measured WBGT: ±1–2°C (up to ±3°C in direct sun — Liljegren 2008)
# ⚠️  พระราม 8 bridge is exposed course → add +1°C mental buffer on race day
# ---------------------------------------------------------------------------
def calc_wbgt(temp_c: float, humidity_pct: float) -> float:
    rh = humidity_pct
    tw = (temp_c * math.atan(0.151977 * (rh + 8.313659) ** 0.5)
          + math.atan(temp_c + rh)
          - math.atan(rh - 1.676331)
          + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
          - 4.686035)
    return 0.7 * tw + 0.3 * temp_c


# ---------------------------------------------------------------------------
# Heat penalty — Ely et al. (2007) Med Sci Sports Exerc 39(3):487
# Base regression: ~0.3–0.4%/°C above 10–13°C for trained HM runners
# Dew point modifier: coach-derived (not from Ely) — approximates evaporation limit
# Wind modifier: coach-derived (not from Ely) — physiologically plausible
# ---------------------------------------------------------------------------
def calc_penalty(temp_c: float, humidity_pct: float,
                 dew_point_c: float, wind_mps: float) -> float:
    # Base: Ely HM regression (~0.4% per °C above 13°C)
    base = max(0.0, (temp_c - 13.0) * 0.40)

    # Dew point: when dew > 16°C sweat evaporation drops → extra penalty [coach-derived]
    dp_penalty = max(0.0, (dew_point_c - 16.0) * 0.25)

    # Wind bonus: each m/s above 2 reduces penalty (evaporative cooling) [coach-derived]
    wind_bonus = min(2.0, max(0.0, wind_mps - 2.0) * 0.20)

    total = base + dp_penalty - wind_bonus
    return round(max(0.0, min(total, 15.0)), 2)  # cap at 15%


# ---------------------------------------------------------------------------
# HR ceiling adjustment: higher WBGT → lower ceilings
# ---------------------------------------------------------------------------
def hr_adjustment(wbgt: float) -> int:
    if wbgt < 18:
        return 0
    elif wbgt < 22:
        return -2
    elif wbgt < 26:
        return -4
    else:
        return -6


# ---------------------------------------------------------------------------
# Fetch weather from OpenWeather Current Weather API (free tier)
# ---------------------------------------------------------------------------
def fetch_weather(lat: float, lon: float) -> dict | None:
    api_key = os.getenv("OPENWEATHER_API_KEY", "")
    if not api_key:
        return None
    try:
        import urllib.request
        url = (f"https://api.openweathermap.org/data/2.5/weather"
               f"?lat={lat}&lon={lon}&appid={api_key}&units=metric")
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read())
        main = data["main"]
        wind = data.get("wind", {})
        # dew point approximation: Magnus formula
        t = main["temp"]
        rh = main["humidity"]
        dew = t - (100 - rh) / 5.0
        return {
            "temp":     round(t, 1),
            "humidity": rh,
            "dew":      round(dew, 1),
            "wind":     round(wind.get("speed", 0), 1),
            "desc":     data["weather"][0]["description"],
        }
    except Exception as e:
        print(f"⚠️  OpenWeather fetch failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Fetch 5-day / 3-hour forecast from OpenWeather (free tier)
# Returns list of dicts with temp/humidity/dew/wind/desc/dt (UTC datetime)
# ---------------------------------------------------------------------------
def fetch_forecast(lat: float, lon: float) -> list | None:
    api_key = os.getenv("OPENWEATHER_API_KEY", "")
    if not api_key:
        return None
    try:
        import urllib.request
        url = (f"https://api.openweathermap.org/data/2.5/forecast"
               f"?lat={lat}&lon={lon}&appid={api_key}&units=metric")
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        out = []
        for entry in data.get("list", []):
            main = entry["main"]
            t  = main["temp"]
            rh = main["humidity"]
            dew = t - (100 - rh) / 5.0
            out.append({
                "dt":       datetime.fromtimestamp(entry["dt"], tz=timezone.utc),
                "temp":     round(t, 1),
                "humidity": rh,
                "dew":      round(dew, 1),
                "wind":     round(entry.get("wind", {}).get("speed", 0), 1),
                "desc":     entry["weather"][0]["description"],
            })
        return out
    except Exception as e:
        print(f"⚠️  OpenWeather forecast fetch failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Forecast lookahead — pick closest 3-hr slot to race-time on each upcoming day
# ---------------------------------------------------------------------------
def forecast_lookahead(race: str, race_time_str: str = "03:30",
                       base_pace_str: str | None = None, days: int = 3):
    loc = RACE_LOCATIONS.get(race) or RACE_LOCATIONS.get("hm") or _BANGKOK_DEFAULT
    print(f"📡 Fetching 5-day forecast for {loc['name']}...")
    forecast = fetch_forecast(loc["lat"], loc["lon"])
    if not forecast:
        print("❌ Forecast unavailable — check OPENWEATHER_API_KEY")
        return

    # Parse race-time hour (local). OpenWeather returns UTC; for BKK/Thai (+7) we
    # offset to find matching local slot.
    tz_offset = loc.get("tz_offset", 7)
    hh, mm = [int(x) for x in race_time_str.split(":")]
    target_local_h = hh + mm / 60.0

    from datetime import timedelta as _td
    today = datetime.now(timezone.utc).date()

    print("=" * 70)
    print(f"🔭 3-DAY FORECAST LOOKAHEAD — {loc['name']} @ {race_time_str} local")
    print("=" * 70)
    print(f"{'Date':<12} {'Temp':>6} {'RH':>5} {'Dew':>6} {'Wind':>6}  {'WBGT':>6} {'+Pace':>7}  Risk")
    print("-" * 70)

    for day_offset in range(days):
        target_day = today + _td(days=day_offset)
        # Find forecast slot nearest target local hour on that day
        best = None
        best_diff = 99
        for f in forecast:
            local_dt = f["dt"] + _td(hours=tz_offset)
            if local_dt.date() != target_day:
                continue
            diff = abs(local_dt.hour + local_dt.minute / 60.0 - target_local_h)
            if diff < best_diff:
                best_diff = diff
                best = f
        if best is None:
            print(f"{str(target_day):<12}  (no forecast slot)")
            continue

        wbgt = calc_wbgt(best["temp"], best["humidity"])
        pen  = calc_penalty(best["temp"], best["humidity"], best["dew"], best["wind"])
        if wbgt < 18:   risk = "🟢 Safe"
        elif wbgt < 23: risk = "🟡 Caution"
        elif wbgt < 28: risk = "🟠 High"
        else:           risk = "🔴 Extreme"

        print(f"{str(target_day):<12} {best['temp']:>5.1f}° {best['humidity']:>4}% "
              f"{best['dew']:>5.1f}° {best['wind']:>4.1f}m/s "
              f"{wbgt:>5.1f}° {pen:>5.1f}%  {risk}")

    print("=" * 70)
    print("💡 รัน `--race ... ` (ไม่มี --forecast) เพื่อ full plan ของวันเดียว")


# ---------------------------------------------------------------------------
# Format pace (seconds/km → MM:SS)
# ---------------------------------------------------------------------------
def fmt_pace(sec_per_km: float) -> str:
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


# ---------------------------------------------------------------------------
# Main output
# ---------------------------------------------------------------------------
def parse_pace(pace_str: str) -> float:
    """Convert 'M:SS' string to seconds/km."""
    parts = pace_str.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def run(race: str, weather: dict, race_time_str: str = "03:30", base_pace_str: str | None = None):
    loc = RACE_LOCATIONS.get(race) or RACE_LOCATIONS.get("hm") or _BANGKOK_DEFAULT
    temp = weather["temp"]
    humidity = weather["humidity"]
    dew = weather["dew"]
    wind = weather["wind"]

    wbgt = calc_wbgt(temp, humidity)
    penalty_pct = calc_penalty(temp, humidity, dew, wind)
    hr_adj = hr_adjustment(wbgt)

    # WBGT risk label
    if wbgt < 18:
        risk = "🟢 Safe"
    elif wbgt < 23:
        risk = "🟡 Caution"
    elif wbgt < 28:
        risk = "🟠 High Risk"
    else:
        risk = "🔴 Extreme — พิจารณา withdraw"

    # Adjusted paces (from VDOT_PACES T/M)
    rhr = ATHLETE["rhr"]
    mhr = ATHLETE["mhr"]
    hrr = ATHLETE["hrr"]

    # Actual race distance (km) from race_registry — was hardcoded to 21.097
    # (half-marathon) everywhere below, so a marathon (e.g. --race bangsaen,
    # 42.195km) silently got HM-distance pace prediction AND a segment plan
    # that stopped at km 21, leaving the back half of the race with no plan
    # at all. Look it up once here and use it for both.
    try:
        from race_registry import load_races
        dist_km = load_races()[race]["dist_km"]
    except Exception:
        dist_km = 21.097

    # Base race pace — prefer user input, fallback to vdot_math
    if base_pace_str:
        base_hm_pace = parse_pace(base_pace_str)
    else:
        try:
            from vdot_math import predict_race_time
            race_min = predict_race_time(ATHLETE["vdot"], dist_km * 1000)
            base_hm_pace = race_min * 60 / dist_km
        except Exception:
            base_hm_pace = (VDOT_PACES["M"][0] + VDOT_PACES["T"][1]) / 2
    adj_pace = base_hm_pace * (1 + penalty_pct / 100)

    # Segment paces — same 5-phase shape (conservative / settle / race pace /
    # push / empty the tank) as fractions of total distance, scaled to
    # whatever race this actually is (was hardcoded km 0-21 for every race).
    _seg_fracs  = [0.0, 0.237, 0.474, 0.711, 0.858, 1.0]
    _seg_deltas = [8, 3, 0, -4, -8]
    _seg_labels = ["Conservative", "Settle", "Race Pace", "Push", "Empty the tank"]
    _bounds = [round(f * dist_km, 1) for f in _seg_fracs]
    segments = [
        (f"km {_bounds[i]:g}–{_bounds[i + 1]:g}", adj_pace + _seg_deltas[i], _seg_labels[i])
        for i in range(5)
    ]

    # HR ceilings — derived from LTHR/MHR (HM ≈ threshold effort):
    # segments build LTHR-13 → LTHR+2, final segment = MHR uncapped
    lthr, mhr = ATHLETE["lthr"], ATHLETE["mhr"]
    base_ceilings = [lthr - 13, lthr - 8, lthr - 3, lthr + 2, mhr]
    adj_ceilings = [c + hr_adj for c in base_ceilings]

    # Electrolyte advice — computed by nutrition_calculator.py (ACSM/Sawka 2007)
    # Run: python3 nutrition_calculator.py --race sponsor21 --temp T --humidity H --duration D
    # Delegate to nutrition_calculator.plan_caps() — SINGLE source of truth.
    # (Previously this block duplicated the buggy max(1,…)+pre_caps=2 logic that
    #  produced ~109% over-replacement; now it calls the clamped solver.)
    try:
        from nutrition_calculator import (sweat_rate_lhr, sweat_na_mgl,
                                          na_requirement, RACE_CONFIG, plan_caps)
        rc = RACE_CONFIG[race]
        duration_min = rc["default_duration_min"]
        sr, _, _ = sweat_rate_lhr(temp, humidity)
        na_loss = sr * duration_min / 60 * sweat_na_mgl()
        na_min, na_max = na_requirement(sr, duration_min)
        n_stations = len(rc["stations"])
        pc = plan_caps(na_loss, na_min, na_max, n_stations)
        stations_str = ", ".join(f"km {k}" for k in rc["stations"])
        pre_txt = f"{pc['pre_caps']} แคปก่อนปืน" if pc["pre_caps"] else "ไม่ต้อง preload"
        prevo_note = (f"Prevo {pre_txt} + รวม {pc['caps_total']} แคป กระจาย {n_stations} station "
                      f"({stations_str}) = {pc['replace_pct']}% Na replacement [ACSM 40–80%]")
    except Exception:
        prevo_note = "ดู nutrition_calculator.py สำหรับแผน Na ที่แม่นยำ"

    print("=" * 60)
    print(f"🌡️  WEATHER ADJUSTER — {loc['name']}")
    print(f"   Race: {race.upper()} | Time: {race_time_str}")
    print("=" * 60)
    print()
    print(f"📡 Weather:")
    print(f"   Temp     : {temp}°C")
    print(f"   Humidity : {humidity}%")
    print(f"   Dew Point: {dew}°C")
    print(f"   Wind     : {wind} m/s")
    print(f"   Desc     : {weather.get('desc', 'N/A')}")
    print()
    print(f"🌡️  WBGT     : {wbgt:.1f}°C  {risk}")
    print(f"📉 Heat Penalty: +{penalty_pct:.1f}%")
    print(f"❤️  HR Ceiling Adj: {hr_adj:+d} bpm")
    print()
    print(f"📋 Adjusted Segment Plan:")
    print(f"   {'ช่วง':<12} {'Pace':>10}  {'HR ceiling':>12}  Notes")
    print("   " + "-" * 50)
    for i, (seg, pace, note) in enumerate(segments):
        ceil_str = f"< {adj_ceilings[i]}" if adj_ceilings[i] < mhr else "🔥 max"
        print(f"   {seg:<12} {fmt_pace(pace):>10}  {ceil_str:>12}  {note}")
    print()
    print(f"💊 Electrolyte: {prevo_note}")
    print()

    # Compare with base (no heat)
    print(f"📊 vs Base Plan (no heat adjustment):")
    print(f"   Base pace ({dist_km:g}km): {fmt_pace(base_hm_pace)}")
    print(f"   Adjusted     : {fmt_pace(adj_pace)}  (+{penalty_pct:.1f}%)")
    time_add_sec = penalty_pct / 100 * dist_km * base_hm_pace
    m, s = divmod(int(time_add_sec), 60)
    print(f"   Time impact  : +{m}:{s:02d} vs lab prediction")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Race weather adjustment")
    try:
        from race_registry import race_choices, active_race_key
        _wc, _wd = race_choices(), active_race_key()
    except Exception:
        _wc, _wd = ["hm", "fuji"], "hm"
    parser.add_argument("--race", default=_wd, choices=_wc)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--time", default="03:30", help="HH:MM race start")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--temp",     type=float, default=27.0)
    parser.add_argument("--humidity", type=float, default=80.0)
    parser.add_argument("--wind",     type=float, default=1.5)
    parser.add_argument("--dew",      type=float, default=21.0)
    parser.add_argument("--base-pace", type=str, default=None,
                        help="Target HM pace from race_pace_planner e.g. 5:07")
    parser.add_argument("--forecast", action="store_true",
                        help="Show 3-day lookahead at race-time (uses 5-day API)")
    args = parser.parse_args()

    if args.forecast:
        forecast_lookahead(args.race, args.time, args.base_pace, days=3)
        return

    loc = RACE_LOCATIONS.get(args.race) or RACE_LOCATIONS.get("hm") or _BANGKOK_DEFAULT

    if args.manual:
        weather = {
            "temp": args.temp, "humidity": args.humidity,
            "wind": args.wind, "dew": args.dew, "desc": "manual input"
        }
    else:
        print(f"📡 Fetching weather for {loc['name']}...")
        weather = fetch_weather(loc["lat"], loc["lon"])
        if weather is None:
            print("⚠️  No API key or fetch failed — using monthly climate averages")
            print("   💡 Add OPENWEATHER_API_KEY to ~/.config/garmin-coach/.env for live data")
            print(f"   💡 หรือระบุเอง: --manual --temp 24 --humidity 80 --wind 2 --dew 20")
            print()
            _month = None
            if args.date:
                try:
                    _month = date.fromisoformat(args.date).month
                except Exception:
                    pass
            weather = get_climate_fallback(args.race, _month)

    run(args.race, weather, args.time, args.base_pace)


if __name__ == "__main__":
    main()
