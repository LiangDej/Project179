#!/usr/bin/env python3
"""
fuji_race_pacer.py — Grade-adjusted Fuji Marathon race-day pacing strategy
Combines: built-in Fuji elevation profile + weather + VDOT pacing

Science:
  - Grade-adjusted pace: Minetti et al. 2002 (J Physiol 543:2)
    +8 sec/km per +1% uphill, -5 sec/km per -1% downhill (up to -2%)
  - Heat penalty: Ely et al. 2007 (Fuji Dec is cold → minimal, but computed)
  - Altitude: Fuji course ~780–900m → ~3% VO2max reduction (Wehrlin & Hallén 2006)
    Performance penalty ≈ 1.2% at 850m avg altitude

Usage:
    python3 fuji_race_pacer.py                         # VDOT from config
    python3 fuji_race_pacer.py --vdot 42               # override VDOT
    python3 fuji_race_pacer.py --goal 230              # goal 3:50 (minutes)
    python3 fuji_race_pacer.py --temp 8 --humidity 55  # manual weather
    python3 fuji_race_pacer.py --elevation fuji.csv    # custom CSV profile
"""

import sys, os, csv, math, argparse
from pathlib import Path
from datetime import date

TOOLS_DIR = Path(__file__).parent
BASE_DIR  = TOOLS_DIR.parent
COACH_MCP = BASE_DIR.parent / "skills" / "garmin_coach_mcp"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from config import ATHLETE, VDOT_PACES, HR_ZONE_BOUNDS  # noqa: E402

# Race date — SINGLE SOURCE: races.json (fuji entry, archived but data preserved)
try:
    from race_registry import get_race
    FUJI_DATE = get_race("fuji")["date"]
except Exception:
    FUJI_DATE = "2026-12-13"   # fallback if races.json unavailable

# ---------------------------------------------------------------------------
# Built-in Fuji Marathon elevation profile (km, elevation_m)
# Source: Fuji Marathon official course map + Strava segment data
# Race route: Fujiyoshida → Kawaguchiko lake loop → return
# Labeled "estimated" — replace with real GPX for race day
# ---------------------------------------------------------------------------
FUJI_ELEVATION = [
    # (distance_km, elevation_m)
    (0.0,  780),
    (2.0,  785),
    (5.0,  790),
    (7.0,  800),
    (9.0,  830),
    (11.0, 860),
    (13.0, 875),
    (15.0, 850),
    (17.0, 840),
    (19.0, 838),
    (21.0, 842),   # approx halfway
    (23.0, 855),
    (25.0, 870),
    (27.0, 850),
    (29.0, 830),
    (31.0, 818),
    (33.0, 808),
    (35.0, 800),
    (37.0, 795),
    (39.0, 785),
    (41.0, 780),
    (42.2, 776),
]

# Aid stations (km marks)
FUJI_AID_STATIONS = [5, 10, 15, 20, 25, 30, 35, 40]

# Grade factors (Minetti et al. 2002 approximation)
GRADE_UP_FACTOR   =  8.0   # sec/km per +1% grade (uphill)
GRADE_DOWN_FACTOR = -5.0   # sec/km per -1% grade (downhill, capped at -2%)
GRADE_MIN_PCT     = -2.0   # no further benefit below -2% grade (braking effect)

# Altitude penalty at Fuji avg ~850m.
# IMPORTANT distinction (corrected 2026-06-09): Wehrlin & Hallén 2006 found
# VO2max drops ~6%/1000m in endurance athletes — but that is VO2MAX, NOT marathon
# performance. A marathon is run sub-maximally (~83% VO2max for this athlete), and
# ~850m sits below the ~1000–1500m threshold where arterial O2 desaturation becomes
# meaningful, so the *endurance performance* penalty is far smaller than the VO2max
# drop — roughly ~1–2% per 1000m for sub-maximal endurance at low altitude
# (Péronnet altitude model; Daniels altitude tables). We use a conservative
# performance basis of 1.5%/1000m, NOT Wehrlin's 6% VO2max figure.
ALTITUDE_PERF_PCT_PER_1000M = 1.5
ALTITUDE_PENALTY_PCT = 0.85 * ALTITUDE_PERF_PCT_PER_1000M  # ~1.3% at 850m avg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fmt_pace(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}/km"


def fmt_time(minutes: float) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}min" if h else f"{m:02d}min"


def interp_elevation(km: float, profile: list[tuple]) -> float:
    """Linear interpolation of elevation at given km."""
    if km <= profile[0][0]:
        return profile[0][1]
    if km >= profile[-1][0]:
        return profile[-1][1]
    for i in range(len(profile) - 1):
        k0, e0 = profile[i]
        k1, e1 = profile[i + 1]
        if k0 <= km <= k1:
            t = (km - k0) / (k1 - k0)
            return e0 + t * (e1 - e0)
    return profile[-1][1]


def calc_grade_pct(km_start: float, km_end: float, profile: list[tuple]) -> float:
    """Average grade % over a segment."""
    e0 = interp_elevation(km_start, profile)
    e1 = interp_elevation(km_end, profile)
    dist_m = (km_end - km_start) * 1000
    return ((e1 - e0) / dist_m * 100) if dist_m > 0 else 0.0


def grade_pace_adj(grade_pct: float) -> float:
    """Pace adjustment in sec/km for given grade %."""
    if grade_pct >= 0:
        return grade_pct * GRADE_UP_FACTOR
    else:
        effective = max(grade_pct, GRADE_MIN_PCT)
        return effective * GRADE_DOWN_FACTOR  # negative → faster


def calc_wbgt(temp_c: float, humidity_pct: float) -> float:
    rh = humidity_pct
    tw = (temp_c * math.atan(0.151977 * (rh + 8.313659) ** 0.5)
          + math.atan(temp_c + rh)
          - math.atan(rh - 1.676331)
          + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
          - 4.686035)
    return 0.7 * tw + 0.3 * temp_c


def heat_penalty_pct(temp_c: float, humidity_pct: float, dew_c: float, wind_mps: float) -> float:
    """Ely et al. 2007 heat penalty."""
    base    = max(0.0, (temp_c - 13.0) * 0.40)
    dp_pen  = max(0.0, (dew_c  - 16.0) * 0.25)
    w_bonus = min(2.0, max(0.0, wind_mps - 2.0) * 0.20)
    return round(max(0.0, min(base + dp_pen - w_bonus, 15.0)), 2)


def load_elevation_csv(path: str) -> list[tuple]:
    """Load CSV with columns: distance_km, elevation_m"""
    pts = []
    with open(path) as f:
        reader = csv.reader(f)
        for row in reader:
            try:
                pts.append((float(row[0]), float(row[1])))
            except (ValueError, IndexError):
                continue
    return sorted(pts, key=lambda x: x[0]) if pts else FUJI_ELEVATION


# ---------------------------------------------------------------------------
# Main planner
# ---------------------------------------------------------------------------
def build_fuji_plan(vdot: float, goal_min: float | None,
                    temp_c: float, humidity_pct: float,
                    dew_c: float, wind_mps: float,
                    elevation_profile: list[tuple],
                    elevation_source: str = "built-in estimate"):
    from vdot_math import predict_race_time
    rhr = ATHLETE["rhr"]
    mhr = ATHLETE["mhr"]

    # Base flat pace from VDOT
    if goal_min:
        base_pace_sec = goal_min * 60 / 42.195
    else:
        flat_min      = predict_race_time(vdot, 42195)
        base_pace_sec = flat_min * 60 / 42.195

    # Penalties
    alt_pen  = ALTITUDE_PENALTY_PCT / 100
    heat_pen = heat_penalty_pct(temp_c, humidity_pct, dew_c, wind_mps) / 100
    wbgt     = calc_wbgt(temp_c, humidity_pct)
    total_pen = alt_pen + heat_pen
    adj_pace_sec = base_pace_sec * (1 + total_pen)

    # Elevation gain/loss
    total_gain = 0.0
    total_loss = 0.0
    prev_e = interp_elevation(0, elevation_profile)
    for km in range(1, 43):
        curr_e = interp_elevation(km, elevation_profile)
        diff = curr_e - prev_e
        if diff > 0:
            total_gain += diff
        else:
            total_loss += abs(diff)
        prev_e = curr_e

    # Build 5km segments
    segment_km = 5.0
    segments = []
    km = 0.0
    seg_num = 0
    while km < 42.195:
        km_end = min(km + segment_km, 42.195)
        grade  = calc_grade_pct(km, km_end, elevation_profile)
        g_adj  = grade_pace_adj(grade)

        # Progressive pacing: conservative start, build through middle, push finale
        dist_pct = km / 42.195
        if dist_pct < 0.25:
            phase_adj = +8.0   # conservative first 10km
        elif dist_pct < 0.75:
            phase_adj = 0.0    # race pace middle
        else:
            phase_adj = -5.0   # negative split last 10km

        seg_pace = adj_pace_sec + g_adj + phase_adj
        seg_dist = km_end - km
        seg_time = seg_pace * seg_dist / 60   # minutes
        elapsed  = sum(s["time_min"] for s in segments) if segments else 0.0

        # Aid stations
        aid = any(km < st <= km_end for st in FUJI_AID_STATIONS)

        # HR estimate (rises progressively with fatigue + heat)
        hr_base = rhr + (mhr - rhr) * (0.82 + dist_pct * 0.12)
        hr_est  = int(min(hr_base, mhr - 3))

        # Elevation for segment midpoint
        mid_e = interp_elevation((km + km_end) / 2, elevation_profile)
        grade_str = f"{grade:+.1f}%"

        segments.append({
            "label":      f"km {km:.0f}–{km_end:.0f}",
            "dist":       seg_dist,
            "grade":      grade,
            "grade_str":  grade_str,
            "elevation":  round(mid_e),
            "pace_sec":   seg_pace,
            "time_min":   seg_time,
            "elapsed":    elapsed,
            "hr_est":     hr_est,
            "aid":        aid,
        })
        km = km_end
        seg_num += 1

    total_min = sum(s["time_min"] for s in segments)

    # ─── Output ───────────────────────────────────────────────────────────────
    print("=" * 70)
    print(f"🗻 FUJI MARATHON RACE DAY PACER")
    print(f"   VDOT {vdot:.0f} | Race: {FUJI_DATE} | {elevation_source}")
    print("=" * 70)
    print()

    print(f"📐 Course Profile:")
    print(f"   Total gain  : {total_gain:.0f}m ↑  |  Total loss: {total_loss:.0f}m ↓")
    print(f"   Avg altitude: ~850m → -{ALTITUDE_PENALTY_PCT:.1f}% endurance pace (sub-max, ไม่ใช่ VO2max drop เต็ม)")
    print()

    print(f"🌡️  Race Conditions (Fuji, Dec):")
    wbgt_str = "🟢 Safe" if wbgt < 18 else "🟡 Caution" if wbgt < 23 else "🟠 High"
    print(f"   Temp: {temp_c}°C | Humidity: {humidity_pct}% | WBGT: {wbgt:.1f}°C {wbgt_str}")
    print(f"   Heat penalty: +{heat_pen*100:.1f}%  |  Altitude: +{alt_pen*100:.1f}%")
    print()

    print(f"🎯 Target:")
    print(f"   Flat VDOT pace : {fmt_pace(base_pace_sec)}  →  finish {fmt_time(base_pace_sec*42.195/60)}")
    print(f"   Adj (alt+heat) : {fmt_pace(adj_pace_sec)}  →  finish {fmt_time(total_min)}")
    print()

    print(f"📋 Segment-by-Segment Plan:")
    print(f"   {'Segment':<14} {'km':>4} {'Grade':>7} {'Elev':>5} {'Pace':>9} {'HR':>4} {'Elapsed':>8}  Notes")
    print("   " + "─" * 65)

    for s in segments:
        elapsed_h, elapsed_m = divmod(int(s["elapsed"]), 60)
        elapsed_str = f"{elapsed_h}:{elapsed_m:02d}"
        aid_str  = "💧" if s["aid"] else ""

        # Grade annotation
        if s["grade"] > 1.5:
            terrain = "↑ hill"
        elif s["grade"] < -1.5:
            terrain = "↓ descent"
        else:
            terrain = "flat"

        print(f"   {s['label']:<14} {s['dist']:>3.1f} {s['grade_str']:>7} "
              f"{s['elevation']:>4}m {fmt_pace(s['pace_sec']):>9} "
              f"{s['hr_est']:>4} {elapsed_str:>8}  {terrain} {aid_str}")

    print("   " + "─" * 65)
    total_h, total_m = divmod(int(total_min), 60)
    print(f"   {'FINISH':<14} {'42.2':>4} {'':>7} {'':>5} {'':>9} {'':>4} "
          f"{total_h}:{total_m:02d}:00  🏁")
    print()

    print(f"💡 Race Strategy:")
    print(f"   km  0–11 : Conservative +8 sec/km — let legs warm at altitude")
    print(f"   km 11–32 : Lock into {fmt_pace(adj_pace_sec)} — HR ceiling 178 bpm")
    print(f"   km 32–42 : Negative split — push if HR < 180 and legs respond")
    print(f"   Uphill   : Shorten stride, maintain effort not pace")
    print(f"   Downhill : Controlled — eccentric load damages quads for 30+ km")
    print()

    print(f"💧 Nutrition (cold weather):")
    print(f"   Pre-race : Palatinose 30g + Prevo 1 แคป (T-45min)")
    print(f"   Race     : Gel every 7km + 200ml water each aid station")
    print(f"   Na       : Prevo 1 แคป/station — ถึงแม้อากาศเย็น Na loss ยังสำคัญ")
    print(f"   ⚠️  อากาศเย็น → ความหิวน้ำลด แต่ร่างกายยัง dehydrate — ดื่มตาม plan !")
    print("=" * 70)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Fuji Marathon grade-adjusted pacer")
    parser.add_argument("--vdot",      type=float, default=None)
    parser.add_argument("--goal",      type=float, default=None, help="Goal time in minutes (e.g. 230 = 3:50)")
    parser.add_argument("--temp",      type=float, default=8.0,  help="Race temp °C (Fuji Dec avg: 3–10°C)")
    parser.add_argument("--humidity",  type=float, default=55.0)
    parser.add_argument("--dew",       type=float, default=0.0)
    parser.add_argument("--wind",      type=float, default=2.5,  help="Wind m/s")
    parser.add_argument("--elevation", type=str,   default=None, help="CSV file: distance_km,elevation_m")
    args = parser.parse_args()

    vdot = args.vdot or ATHLETE["vdot"]

    if args.elevation:
        profile = load_elevation_csv(args.elevation)
        elev_source = f"CSV: {args.elevation}"
    else:
        profile = FUJI_ELEVATION
        elev_source = "built-in estimate (replace with real GPX for accuracy)"

    build_fuji_plan(
        vdot=vdot,
        goal_min=args.goal,
        temp_c=args.temp,
        humidity_pct=args.humidity,
        dew_c=args.dew,
        wind_mps=args.wind,
        elevation_profile=profile,
        elevation_source=elev_source,
    )


if __name__ == "__main__":
    main()
