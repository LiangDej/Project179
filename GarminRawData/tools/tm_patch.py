#!/usr/bin/env python3
"""
tm_patch.py — Session Corrector (sessions_master → sessions.json)

sessions_master.json is the source of truth (user-confirmed session_type, is_treadmill).
sessions.json is written by protected post_session_analyzer — may have wrong type/pace.

This script syncs corrections from sessions_master into sessions.json:
  1. session_type  — user-confirmed type from sessions_master
  2. is_treadmill  — from sessions_master (more reliable than post_session_analyzer)
  3. pace          — for TM sessions: HR-inferred from quality laps (GPS is unreliable)

Raw Garmin data (running_activities_all.json) is NEVER modified.
Run automatically from run_post_quality.sh (Step 1.5).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR      = Path(__file__).parent.parent
SESSIONS_JSON = BASE_DIR / "QualitySessionLog" / "sessions.json"
MASTER_JSON   = BASE_DIR / "QualitySessionLog" / "sessions_master.json"

sys.path.insert(0, str(Path(__file__).parent))
from treadmill_pace_model import infer_pace_from_hr as _infer_pace_from_hr  # noqa: E402

# Map sessions_master type → sessions.json display type
TYPE_MAP = {
    "threshold":   "Threshold (T)",
    "interval":    "Interval (I)",
    "tempo":       "Tempo",
    "fast_finish": "Fast Finish",
    "easy":        "Easy Run",
    "long_run":    "Easy Run",
}


def _tm_pace_from_belt(master_rec: dict) -> str | None:
    """Extract quality pace from treadmill_phases belt speed — most accurate source.

    Method: identify quality phases as those within 0.5 km/h of the session's
    peak speed (handles 5×1km intervals and continuous T-sessions equally).
    Returns distance-weighted average pace of quality phases.
    Falls back to None if treadmill_phases missing or too little quality data.
    """
    phases = master_rec.get("treadmill_phases") or []
    if not phases:
        return None

    speeds = [p.get("speed_kmh", 0) for p in phases if p.get("speed_kmh")]
    if not speeds:
        return None
    peak_speed = max(speeds)

    # Quality phases = within 0.5 km/h of peak (captures all reps of same speed)
    quality_phases = [
        p for p in phases
        if abs(p.get("speed_kmh", 0) - peak_speed) <= 0.5
        and p.get("distance_km", 0) >= 0.3   # exclude tiny transition phases
    ]
    if not quality_phases:
        return None

    total_dist = sum(p["distance_km"] for p in quality_phases)
    if total_dist < 0.5:
        return None

    # pace_sec/km from speed_kmh: pace = 3600 / speed
    total_weighted = sum(
        (3600 / p["speed_kmh"]) * p["distance_km"]
        for p in quality_phases
    )
    avg_pace_sec = round(total_weighted / total_dist)
    m, s = divmod(avg_pace_sec, 60)
    return f"{m}:{s:02d}/km"


def _tm_pace_from_master(master_rec: dict) -> str | None:
    """Fallback: weighted avg HR-inferred pace from quality laps in sessions_master."""
    laps = master_rec.get("laps") or []
    quality_laps = [l for l in laps if l.get("role") == "quality" and l.get("avg_hr")]
    if not quality_laps:
        return None

    total_s, total_weighted = 0.0, 0.0
    for lap in quality_laps:
        pace_str = _infer_pace_from_hr(lap.get("avg_hr"))
        if not pace_str or ":" not in pace_str:
            continue
        m, s_raw = pace_str.split(":")
        pace_s = int(m) * 60 + int(s_raw.replace("/km", "").strip())
        dur = lap.get("duration_s") or 0
        total_weighted += pace_s * dur
        total_s += dur

    if not total_s:
        return None
    m, s = divmod(int(total_weighted / total_s), 60)
    return f"{m}:{s:02d}/km"


def patch():
    if not SESSIONS_JSON.exists():
        print("sessions.json not found — nothing to patch")
        return

    with open(SESSIONS_JSON) as f:
        sessions_data = json.load(f)

    master_sessions = []
    if MASTER_JSON.exists():
        with open(MASTER_JSON) as f:
            master_sessions = json.load(f).get("sessions", [])

    # Index master by activity_id for fast lookup
    master_idx = {s["activity_id"]: s for s in master_sessions if s.get("activity_id")}

    sessions = sessions_data.get("sessions", [])
    patched = 0

    for s in sessions:
        act_id = s.get("activity_id")
        master_rec = master_idx.get(act_id)
        if not master_rec:
            continue  # not in master — leave unchanged

        changes = []

        # 1. Sync session_type
        master_type_raw = master_rec.get("session_type", "")
        correct_type    = TYPE_MAP.get(master_type_raw, s.get("session_type"))
        if correct_type and correct_type != s.get("session_type"):
            s["session_type"] = correct_type
            changes.append(f"type→{correct_type}")

        # 2. Sync is_treadmill
        master_tm = master_rec.get("is_treadmill", False)
        if master_tm != s.get("is_treadmill"):
            s["is_treadmill"] = master_tm
            changes.append(f"TM→{master_tm}")

        # 3. Correct pace for TM sessions
        #    Priority: belt_speed (treadmill_phases) > hr_inferred (quality laps) > hr_inferred (avg_hr)
        if s.get("is_treadmill"):
            original_pace = s.get("pace", "?")
            belt_pace     = _tm_pace_from_belt(master_rec)
            corrected     = belt_pace or _tm_pace_from_master(master_rec) or _infer_pace_from_hr(s.get("avg_hr"))
            source        = "belt_speed" if belt_pace else "hr_inferred"
            if corrected and corrected != original_pace:
                s["pace"]          = corrected
                s["pace_source"]   = source
                changes.append(f"pace {original_pace}→{corrected} ({source})")

        if changes:
            patched += 1
            print(f"  ✅ {s['date']} [{act_id}]: {' | '.join(changes)}")

    if patched:
        with open(SESSIONS_JSON, "w") as f:
            json.dump(sessions_data, f, indent=2, ensure_ascii=False)
        print(f"\n✅ tm_patch: {patched} session(s) corrected in sessions.json")
    else:
        print("✅ tm_patch: sessions.json already clean")


if __name__ == "__main__":
    print("\n🔧 Session Corrector (sessions_master → sessions.json)")
    print("────────────────────────────────────────────────────")
    patch()
