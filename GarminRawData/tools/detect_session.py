#!/usr/bin/env python3
"""
detect_session.py — Auto-detect session type from latest Garmin activity.

Outputs one of: "quality" | "long" | "easy"
Used by run_post_run.sh to auto-route to the correct post-run pipeline.

Detection logic (priority order):
  1. sessions_master already has this activity_id with quality type → quality
  2. max HR ≥ T_HR_LO (170bpm) AND quality zone time > 10min → quality
  3. avg HR < 84% HRR AND distance ≥ LONG_RUN_KM → long
  4. else → easy

Why max HR, not avg HR:
  WU + CD (~2km each at 145bpm) dilute avg_hr of quality sessions.
  May 7 Interval: avg=162bpm (79%HRR) → wrong if using avg.
              max=178bpm (89%HRR) → correctly detected as quality.

Usage:
    python3 detect_session.py              # detect latest activity
    python3 detect_session.py [activity_id]
    python3 detect_session.py --verbose
"""

import json
import sys
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent
DATA_FILE   = BASE_DIR / "running_activities_all.json"
MASTER_JSON = BASE_DIR / "QualitySessionLog" / "sessions_master.json"
COACH_MCP   = BASE_DIR.parent / "skills" / "garmin_coach_mcp"
sys.path.insert(0, str(COACH_MCP))

from config import ATHLETE, HR_ZONE_BOUNDS  # noqa: E402

RHR         = ATHLETE["rhr"]
MHR         = ATHLETE["mhr"]
HRR         = MHR - RHR
T_HR_LO     = HR_ZONE_BOUNDS["Z3_T"][0]   # 170 bpm — Threshold zone low
LONG_RUN_KM = 14.0                         # ≥ this km = long run candidate
QUALITY_ZONE_MIN = 10.0                    # ≥ 10 min in Z3+Z4+Z5 = quality effort

QUALITY_TYPES = {"Threshold (T)", "Interval (I)", "Repetition (R)", "Tempo", "Fast Finish"}


def _hr_pct(avg_hr: float) -> float:
    return (avg_hr - RHR) / HRR * 100


def _load_master_types() -> dict:
    """Return {activity_id: session_type} for quality sessions already in sessions_master."""
    if not MASTER_JSON.exists():
        return {}
    try:
        with open(MASTER_JSON, encoding="utf-8") as f:
            master = json.load(f)
        return {
            str(s["activity_id"]): s.get("session_type", "")
            for s in master.get("sessions", [])
            if s.get("activity_id") and s.get("session_type") in QUALITY_TYPES
        }
    except Exception:
        return {}


def _load_activity(activity_id: str | None) -> dict | None:
    if not DATA_FILE.exists():
        return None
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    activities = data if isinstance(data, list) else data.get("activities", [])
    if not activities:
        return None
    if activity_id:
        for a in activities:
            if str(a.get("activityId", "")) == str(activity_id):
                return a
        return None
    return max(activities, key=lambda a: a.get("activityId", 0))


def detect(activity_id: str | None = None, verbose: bool = False) -> str:
    act = _load_activity(activity_id)
    if not act:
        if verbose:
            print("⚠️  ไม่พบ activity — fallback: easy", file=sys.stderr)
        return "easy"

    act_id   = str(act.get("activityId", ""))
    avg_hr   = float(act.get("averageHR") or act.get("avgHr") or 0)
    max_hr   = float(act.get("maxHR") or act.get("maxHeartRate") or 0)
    distance = float(act.get("distance", 0)) / 1000   # m → km
    act_type = act.get("activityType", {})
    type_key = act_type.get("typeKey", "") if isinstance(act_type, dict) else str(act_type)

    # Quality zone time = Z3 + Z4 + Z5 (seconds)
    quality_zone_sec = (
        act.get("hrTimeInZone_3", 0) +
        act.get("hrTimeInZone_4", 0) +
        act.get("hrTimeInZone_5", 0)
    )
    quality_zone_min = quality_zone_sec / 60

    avg_hr_pct = _hr_pct(avg_hr) if avg_hr else 0

    if verbose:
        print(f"\n🔍 Session Detector — Activity {act_id}", file=sys.stderr)
        print(f"   Distance       : {distance:.2f} km", file=sys.stderr)
        print(f"   Avg HR         : {avg_hr} bpm  ({avg_hr_pct:.1f}% HRR)", file=sys.stderr)
        print(f"   Max HR         : {max_hr} bpm  (T_zone_lo={T_HR_LO})", file=sys.stderr)
        print(f"   Quality zone   : {quality_zone_min:.1f} min in Z3+Z4+Z5", file=sys.stderr)
        print(f"   Activity type  : {type_key}", file=sys.stderr)

    # Non-running → easy
    if type_key and "running" not in type_key.lower() and "treadmill" not in type_key.lower():
        if verbose:
            print(f"   → EASY (not a run: {type_key})", file=sys.stderr)
        return "easy"

    # ── Rule 1: sessions_master already confirmed quality type ──────────────
    master_types = _load_master_types()
    if act_id in master_types:
        result = "quality"
        reason = f"sessions_master: {master_types[act_id]}"
        if verbose:
            print(f"   → QUALITY ({reason})", file=sys.stderr)
        return result

    # ── Rule 2: max HR hit T-zone AND significant quality zone time ──────────
    # max HR ≥ T_HR_LO means the athlete definitely pushed into threshold/interval
    # quality_zone_min > 10 filters out easy runs that briefly spike (e.g. final hill)
    if max_hr >= T_HR_LO and quality_zone_min >= QUALITY_ZONE_MIN:
        result = "quality"
        reason = (f"max HR {max_hr:.0f}bpm ≥ T_zone_lo {T_HR_LO}bpm "
                  f"AND {quality_zone_min:.0f}min in Z3/Z4/Z5 ≥ {QUALITY_ZONE_MIN}min")
        if verbose:
            print(f"   → QUALITY ({reason})", file=sys.stderr)
        return result

    # ── Rule 3: Long Run — easy/M effort + distance qualifies ───────────────
    if distance >= LONG_RUN_KM:
        result = "long"
        reason = f"{distance:.1f}km ≥ {LONG_RUN_KM}km, max HR {max_hr:.0f}bpm < {T_HR_LO} or zone time {quality_zone_min:.0f}min < {QUALITY_ZONE_MIN}min"
        if verbose:
            print(f"   → LONG ({reason})", file=sys.stderr)
        return result

    # ── Rule 4: Easy ─────────────────────────────────────────────────────────
    result = "easy"
    reason = f"{distance:.1f}km < {LONG_RUN_KM}km, easy effort"
    if verbose:
        print(f"   → EASY ({reason})", file=sys.stderr)
    return result


if __name__ == "__main__":
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    args    = [a for a in sys.argv[1:] if not a.startswith("-")]
    act_id  = args[0] if args else None

    result = detect(activity_id=act_id, verbose=verbose)
    print(result)   # stdout — captured by bash
