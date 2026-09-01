#!/usr/bin/env python3
"""
race_pace_planner.py — Race Day Pace Planner

วางแผนวันแข่งแบบ segment-by-segment โดยอิงจาก:
  - VDOT จาก config.py
  - Elevation profile (ถ้ามี CSV/GPX)
  - Aid station intervals
  - HR targets ต่อ segment

ถ้าไม่มี elevation → flat assumption, ใช้ race pace เดียวตลอด
ถ้ามี elevation → adjust pace ตาม grade (+/- sec/km per % grade)

Usage:
    python3 race_pace_planner.py --race sponsor21              # Sponsor Run Bangkok HM
    python3 race_pace_planner.py --race bangsaen                # Bangsaen42 Marathon
    python3 race_pace_planner.py --race sponsor21 --goal 110    # goal 1:50
    python3 race_pace_planner.py --race bangsaen --elevation bangsaen_elevation.csv
    python3 race_pace_planner.py --vdot 40 --race sponsor21     # override VDOT
"""

import sys
import csv
import json
import math
import argparse
from datetime import date
from pathlib import Path

BASE_DIR  = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
COACH_MCP = BASE_DIR.parent / "skills" / "garmin_coach_mcp"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from config import ATHLETE, VDOT_PACES, HR_ZONE_BOUNDS  # noqa: E402

# ---------------------------------------------------------------------------
# Race profiles
# ---------------------------------------------------------------------------
# SINGLE SOURCE: races.json via race_registry (no hardcoded dates/Fuji).
def _build_races() -> dict:
    try:
        from race_registry import load_races
        out = {}
        for k, r in load_races().items():
            if not r.get("active", True):
                continue
            stations = r.get("stations_km") or []
            aid = round(r["dist_km"] / len(stations), 1) if stations else 5.0
            out[k] = {
                "name":             f"{r['name']} ({r['dist_km']:.0f}km)",
                "distance_km":      r["dist_km"],
                "date":             date.fromisoformat(r["date"]),
                "aid_every_km":     aid,
                "terrain":          r.get("terrain", "flat"),
                "default_goal_min": r.get("goal_min"),
                "expected_temp_c":  r.get("expected_temp_c", 27),
            }
        return out
    except Exception:
        return {"atm": {"name": "ATM Bangkok Marathon (42km)", "distance_km": 42.195,
                        "date": date(2026, 11, 29), "aid_every_km": 5.0, "terrain": "flat",
                        "default_goal_min": 240, "expected_temp_c": 27}}

RACES = _build_races()

# Pace adjustment for elevation grade (sec/km per % grade)
# Based on Jack Daniels / Minetti (2002) approximation
GRADE_FACTOR    = 8.0   # +8 sec/km per +1% uphill
DOWNHILL_FACTOR = 6.0   # -6 sec/km per -1% downhill


def _heat_penalty_pct(temp_c: float) -> float:
    """
    Estimate race time slowdown % due to ambient heat.
    Source: Ely et al. (2007) ACSM — marathon data; applied conservatively to HM.
    Reference temps: 10°C=0%, 15°C=1%, 20°C=2%, 25°C=4%, 30°C=7%, 35°C=10%
    """
    ref = [(10, 0.0), (15, 1.0), (20, 2.0), (25, 4.0), (30, 7.0), (35, 10.0)]
    if temp_c <= ref[0][0]:
        return 0.0
    if temp_c >= ref[-1][0]:
        return ref[-1][1]
    for i in range(len(ref) - 1):
        t0, p0 = ref[i]
        t1, p1 = ref[i + 1]
        if t0 <= temp_c <= t1:
            return round(p0 + (temp_c - t0) / (t1 - t0) * (p1 - p0), 1)
    return 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fmt_pace(sec_km: float) -> str:
    m, s = divmod(int(sec_km), 60)
    return f"{m}:{s:02d}/km"


def _fmt_time(total_sec: float) -> str:
    h = int(total_sec // 3600)
    m = int((total_sec % 3600) // 60)
    s = int(total_sec % 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


def _hr_zone(hr: float) -> str:
    for zone, (lo, hi) in HR_ZONE_BOUNDS.items():
        if lo <= hr < hi:
            return zone
    return "Z5_R" if hr >= HR_ZONE_BOUNDS["Z5_R"][0] else "Z1_E"


def _vdot_to_race_pace(vdot: float, distance_km: float) -> float:
    """Race pace (sec/km) for a given distance — delegates to vdot_math.

    FIX (2026-06-09): previously this rolled its own %VO2max buckets
    (HM=92%, FM=83.5%) which assume an elite ~70-min HM. For a VDOT-40 runner
    whose HM takes ~110 min, the true sustainable intensity is ~84% VO2max, so
    the old buckets over-predicted by 8–20 sec/km (HM 4:55 vs true 5:15) and
    produced dangerously aggressive race paces. vdot_math.predict_race_time()
    uses Daniels' duration-dependent %VO2max curve — the validated model and
    the single source of truth (its docstring: "DO NOT duplicate elsewhere").
    """
    from vdot_math import predict_race_time
    finish_min = predict_race_time(vdot, distance_km * 1000)
    return round(finish_min * 60 / distance_km)


def _load_elevation_csv(path: Path) -> list[tuple[float, float]]:
    """
    Load elevation profile CSV.
    Expected columns: distance_km, elevation_m  (or km, m)
    Returns: list of (distance_km, elevation_m)
    """
    points = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                dist_key = next((k for k in row if "dist" in k.lower() or k.lower() == "km"), None)
                elev_key = next((k for k in row if "elev" in k.lower() or k.lower() in ("m", "alt")), None)
                if dist_key and elev_key:
                    try:
                        points.append((float(row[dist_key]), float(row[elev_key])))
                    except ValueError:
                        continue
    except Exception as e:
        print(f"⚠️  Could not load elevation CSV: {e}")
    return sorted(points, key=lambda x: x[0])


# ---------------------------------------------------------------------------
# Segment planner
# ---------------------------------------------------------------------------
def _build_segments(race: dict, goal_min: float, vdot: float,
                    elevation_points: list[tuple[float, float]]) -> list[dict]:
    dist_km  = race["distance_km"]
    aid_step = race["aid_every_km"]
    base_pace_sec = goal_min * 60 / dist_km

    # Segment boundaries at aid stations
    boundaries = []
    km = 0.0
    while km < dist_km:
        km = min(km + aid_step, dist_km)
        boundaries.append(round(km, 2))
    if boundaries[-1] < dist_km:
        boundaries.append(dist_km)

    segments = []
    prev_km = 0.0

    for seg_end in boundaries:
        seg_start = prev_km
        seg_dist  = seg_end - seg_start
        seg_frac  = seg_start / dist_km

        # Elevation grade for this segment
        grade_pct = 0.0
        if len(elevation_points) >= 2:
            def _interp_elev(km_pos: float) -> float:
                if km_pos <= elevation_points[0][0]:
                    return elevation_points[0][1]
                if km_pos >= elevation_points[-1][0]:
                    return elevation_points[-1][1]
                for i in range(len(elevation_points) - 1):
                    d0, e0 = elevation_points[i]
                    d1, e1 = elevation_points[i+1]
                    if d0 <= km_pos <= d1:
                        t = (km_pos - d0) / (d1 - d0) if d1 > d0 else 0
                        return e0 + t * (e1 - e0)
                return 0.0

            e_start = _interp_elev(seg_start)
            e_end   = _interp_elev(seg_end)
            rise_m  = e_end - e_start
            grade_pct = rise_m / (seg_dist * 1000) * 100

        # Grade adjustment
        pace_adj = grade_pct * GRADE_FACTOR if grade_pct > 0 else grade_pct * DOWNHILL_FACTOR

        # Conservative start strategy
        if seg_frac < 0.25:
            conserve_adj = 8
        elif seg_frac < 0.50:
            conserve_adj = 3
        elif seg_frac < 0.75:
            conserve_adj = 0
        else:
            conserve_adj = -3

        target_pace = base_pace_sec + pace_adj + conserve_adj

        # HR estimate
        rhr     = ATHLETE["rhr"]
        mhr     = ATHLETE["mhr"]
        base_hr = rhr + (mhr - rhr) * 0.82
        hr_drift = (mhr - base_hr) * seg_frac * 0.7
        target_hr = round(base_hr + hr_drift)

        segments.append({
            "segment":         f"km {seg_start:.1f}–{seg_end:.1f}",
            "distance_km":     round(seg_dist, 2),
            "grade_pct":       round(grade_pct, 1),
            "target_pace":     _fmt_pace(target_pace),
            "target_pace_sec": round(target_pace),
            "target_hr":       target_hr,
            "hr_zone":         _hr_zone(target_hr),
            "aid_station":     seg_end < dist_km,
            "cumulative_km":   round(seg_end, 2),
        })
        prev_km = seg_end

    return segments


def plan_race(race_key: str, goal_min: float | None, vdot_override: float | None,
              elevation_path: Path | None) -> dict:
    race = RACES[race_key]
    vdot = vdot_override or ATHLETE["vdot"]

    if goal_min is None:
        predicted_pace = _vdot_to_race_pace(vdot, race["distance_km"])
        goal_min = round(predicted_pace * race["distance_km"] / 60, 1)
    else:
        predicted_pace = goal_min * 60 / race["distance_km"]

    elevation_points = []
    if elevation_path and elevation_path.exists():
        elevation_points = _load_elevation_csv(elevation_path)

    segments = _build_segments(race, goal_min, vdot, elevation_points)
    total_sec = sum(s["target_pace_sec"] * s["distance_km"] for s in segments)

    # Heat adjustment (Ely et al. 2007)
    temp_c    = race.get("expected_temp_c")
    heat_pct  = _heat_penalty_pct(temp_c) if temp_c is not None else 0.0
    heat_adj_min = round(goal_min * (1 + heat_pct / 100), 1) if heat_pct > 0 else None

    return {
        "race_name":        race["name"],
        "race_date":        race["date"].isoformat(),
        "distance_km":      race["distance_km"],
        "vdot":             vdot,
        "goal_time":        _fmt_time(goal_min * 60),
        "goal_min":         goal_min,
        "projected_finish": _fmt_time(total_sec),
        "target_pace_avg":  _fmt_pace(predicted_pace),
        "has_elevation":    bool(elevation_points),
        "heat_temp_c":      temp_c,
        "heat_pct":         heat_pct,
        "heat_adj_finish":  _fmt_time(heat_adj_min * 60) if heat_adj_min else None,
        "segments":         segments,
        "race_strategy": {
            "start":  f"km 0–{race['distance_km']*0.25:.0f}: Conservative +8 sec/km buffer",
            "build":  f"km {race['distance_km']*0.25:.0f}–{race['distance_km']*0.75:.0f}: Settle into race pace",
            "finish": f"km {race['distance_km']*0.75:.0f}–{race['distance_km']:.1f}: Negative split — push if HR < 175",
        },
        "hr_strategy": {
            "rule":   "จ้อง HR ไม่ใช่ pace — ถ้า HR แตะ 178 ก่อน km 15 ให้ถอย pace 5–8 sec/km",
            "target": f"Avg HR: {round(ATHLETE['rhr'] + (ATHLETE['mhr']-ATHLETE['rhr'])*0.85)}–"
                      f"{round(ATHLETE['rhr'] + (ATHLETE['mhr']-ATHLETE['rhr'])*0.90)} bpm",
        },
    }


def print_plan(p: dict):
    print(f"\n{'='*60}")
    print(f"🏁 RACE PACE PLAN — {p['race_name']}")
    print(f"   Date: {p['race_date']}  |  VDOT: {p['vdot']}")
    print(f"   Goal: {p['goal_time']}  |  Target: {p['target_pace_avg']}")
    print(f"   Projected finish (lab): {p['projected_finish']}")
    if p.get('heat_adj_finish'):
        print(f"   🌡️  Heat-adjusted ({p['heat_temp_c']}°C, +{p['heat_pct']:.0f}%): {p['heat_adj_finish']}  ← realistic")
    if p['has_elevation']:
        print("   📈 Elevation profile loaded")
    print(f"{'='*60}")

    print(f"\n📋 Race Strategy:")
    for v in p['race_strategy'].values():
        print(f"   {v}")

    print(f"\n❤️  HR Strategy:")
    print(f"   {p['hr_strategy']['rule']}")
    print(f"   {p['hr_strategy']['target']}")

    print(f"\n📏 Segment Plan:")
    print(f"   {'Segment':<18} {'Dist':>5} {'Grade':>6} {'Pace':>9} {'HR':>5} {'Zone':<7} {'Aid'}")
    print(f"   {'-'*60}")
    for s in p['segments']:
        aid       = "💧" if s['aid_station'] else ""
        grade_str = f"{s['grade_pct']:+.1f}%" if s['grade_pct'] != 0 else "flat"
        print(f"   {s['segment']:<18} {s['distance_km']:>4.1f}km "
              f"{grade_str:>6} {s['target_pace']:>9} "
              f"{s['target_hr']:>4}bpm {s['hr_zone']:<7} {aid}")

    print(f"\n{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Race Pace Planner")
    _default_race = "atm" if "atm" in RACES else list(RACES.keys())[0]
    parser.add_argument("--race",      choices=list(RACES.keys()), default=_default_race)
    parser.add_argument("--goal",      type=float, default=None,
                        help="Goal time in minutes (e.g. 110 for 1:50)")
    parser.add_argument("--vdot",      type=float, default=None)
    parser.add_argument("--elevation", type=Path,  default=None,
                        help="Path to elevation CSV (distance_km, elevation_m)")
    parser.add_argument("--json",      action="store_true")
    args = parser.parse_args()

    p = plan_race(args.race, args.goal, args.vdot, args.elevation)

    if args.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
    else:
        print_plan(p)


if __name__ == "__main__":
    main()
