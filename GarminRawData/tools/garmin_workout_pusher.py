#!/usr/bin/env python3
"""
garmin_workout_pusher.py — Build & push weekly workouts to Garmin Connect

reads session_prescriber --json → builds Garmin workout JSON → preview → upload + schedule

Usage:
    python3 garmin_workout_pusher.py              # preview สัปดาห์นี้
    python3 garmin_workout_pusher.py --upload     # preview + upload
    python3 garmin_workout_pusher.py --week next  # สัปดาห์หน้า
    python3 garmin_workout_pusher.py --week next --upload
"""

import sys, json, re, argparse, subprocess
from pathlib import Path

TOOLS_DIR = Path(__file__).parent
BASE_DIR  = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(BASE_DIR.parent / "skills" / "garmin_coach_mcp"))

from config import ATHLETE, VDOT_PACES
try:
    from garmin_client import get_client
except ImportError:
    get_client = None
from garminconnect.workout import (
    RunningWorkout, WorkoutSegment,
    ExecutableStep, RepeatGroup as RG,
)

# ── Constants ───────────────────────────────────────────────────────────────

SPORT  = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
STROKE = {"strokeTypeId": 0, "strokeTypeKey": None, "displayOrder": 0}
EQUIP  = {"equipmentTypeId": 0, "equipmentTypeKey": None, "displayOrder": 0}

# stepTypeId reference
WARMUP   = (1, "warmup",   1)
COOLDOWN = (2, "cooldown", 2)
INTERVAL = (3, "interval", 3)
RECOVERY = (4, "recovery", 4)
ACTIVE   = (7, "other",    7)

# endCondition reference
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time",     "displayOrder": 2, "displayable": True}
END_DIST = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
END_ITER = {"conditionTypeId": 7, "conditionTypeKey": "iterations","displayOrder": 7, "displayable": False}

NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}

# ── Pace helpers ─────────────────────────────────────────────────────────────

def pace_to_ms(pace_str: str) -> float:
    """'5:13' or '5:13/km' → m/s (rounded to 4dp)"""
    p = pace_str.replace("/km", "").strip()
    parts = p.split(":")
    sec_per_km = int(parts[0]) * 60 + int(parts[1])
    return round(1000 / sec_per_km, 4)

def pace_to_kmh(pace_str: str) -> float:
    """'5:13' → km/h (1 decimal) — สำหรับตั้ง TM belt speed"""
    return round(pace_to_ms(pace_str) * 3.6, 1)

def tm_note(pace_slow: str, pace_fast: str) -> str:
    """Return TM belt speed range note: 'TM 11.1–11.5 km/h'
    pace_slow = slower pace (min/km) → lower speed
    pace_fast = faster pace (min/km) → higher speed
    """
    return f"TM {pace_to_kmh(pace_slow)}–{pace_to_kmh(pace_fast)} km/h"

def speed_target(pace_slow: str, pace_fast: str) -> dict:
    """pace_slow = slower limit, pace_fast = faster limit → speed zone target in m/s"""
    return {
        "workoutTargetTypeId": 6,
        "workoutTargetTypeKey": "speed.zone",
        "displayOrder": 6,
        "targetValueOne": pace_to_ms(pace_slow),   # lower speed bound (slower pace)
        "targetValueTwo": pace_to_ms(pace_fast),   # upper speed bound (faster pace)
    }

# ── Step builders ────────────────────────────────────────────────────────────

def _base(order: int, stype: tuple, child_id=None) -> dict:
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": stype[0], "stepTypeKey": stype[1], "displayOrder": stype[2]},
        "childStepId": child_id,
        "description": None,
        "endCondition": None,
        "endConditionValue": None,
        "preferredEndConditionUnit": None,
        "endConditionCompare": None,
        "targetType": NO_TARGET,
        "targetValueOne": None,
        "targetValueTwo": None,
        "targetValueUnit": None,
        "zoneNumber": None,
        "secondaryTargetType": None,
        "secondaryTargetValueOne": None,
        "secondaryTargetValueTwo": None,
        "secondaryTargetValueUnit": None,
        "secondaryZoneNumber": None,
        "endConditionZone": None,
        "strokeType": STROKE,
        "equipmentType": EQUIP,
        "category": None,
        "exerciseName": None,
        "workoutProvider": None,
        "providerExerciseSourceId": None,
        "weightValue": None,
        "weightUnit": None,
    }

def warmup_step(order, secs, desc="Warm up easy — keep HR low"):
    s = _base(order, WARMUP)
    s["description"] = desc
    s["endCondition"] = END_TIME
    s["endConditionValue"] = float(secs)
    return s

def cooldown_step(order, secs, desc="Cool down easy"):
    s = _base(order, COOLDOWN)
    s["description"] = desc
    s["endCondition"] = END_TIME
    s["endConditionValue"] = float(secs)
    return s

def active_time_step(order, secs, desc, pace_slow=None, pace_fast=None, child_id=None):
    s = _base(order, ACTIVE, child_id)
    s["description"] = desc
    s["endCondition"] = END_TIME
    s["endConditionValue"] = float(secs)
    if pace_slow and pace_fast:
        tgt = speed_target(pace_slow, pace_fast)
        s["targetType"] = {k: v for k, v in tgt.items() if k not in ("targetValueOne","targetValueTwo")}
        s["targetValueOne"] = tgt["targetValueOne"]
        s["targetValueTwo"] = tgt["targetValueTwo"]
    return s

def active_dist_step(order, dist_m, desc, pace_slow=None, pace_fast=None):
    s = _base(order, ACTIVE)
    s["description"] = desc
    s["endCondition"] = END_DIST
    s["endConditionValue"] = float(dist_m)
    if pace_slow and pace_fast:
        tgt = speed_target(pace_slow, pace_fast)
        s["targetType"] = {k: v for k, v in tgt.items() if k not in ("targetValueOne","targetValueTwo")}
        s["targetValueOne"] = tgt["targetValueOne"]
        s["targetValueTwo"] = tgt["targetValueTwo"]
    return s

def interval_step(order, child_id, secs=None, dist_m=None, desc="Interval", pace_slow=None, pace_fast=None):
    s = _base(order, INTERVAL, child_id)
    s["description"] = desc
    s["category"] = "RUN"
    s["exerciseName"] = "RUN"
    if secs:
        s["endCondition"] = END_TIME
        s["endConditionValue"] = float(secs)
    elif dist_m:
        s["endCondition"] = END_DIST
        s["endConditionValue"] = float(dist_m)
    if pace_slow and pace_fast:
        tgt = speed_target(pace_slow, pace_fast)
        s["targetType"] = {k: v for k, v in tgt.items() if k not in ("targetValueOne","targetValueTwo")}
        s["targetValueOne"] = tgt["targetValueOne"]
        s["targetValueTwo"] = tgt["targetValueTwo"]
    return s

def recovery_step(order, child_id, secs, desc="Jog recovery"):
    s = _base(order, RECOVERY, child_id)
    s["description"] = desc
    s["endCondition"] = END_TIME
    s["endConditionValue"] = float(secs)
    return s

def repeat_group(order, iterations, steps, child_id=1):
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
        "childStepId": child_id,
        "numberOfIterations": iterations,
        "workoutSteps": steps,
        "endConditionValue": float(iterations),
        "preferredEndConditionUnit": None,
        "endConditionCompare": None,
        "endCondition": END_ITER,
        "skipLastRestStep": None,
        "smartRepeat": False,
    }

def _to_step(d: dict) -> ExecutableStep | RG:
    """Recursively convert step dict → Pydantic model (garminconnect v0.3.3+)."""
    if d.get("type") == "RepeatGroupDTO":
        children = [_to_step(c) for c in d.get("workoutSteps", [])]
        return RG(**{**d, "workoutSteps": children})
    return ExecutableStep(**d)


def wrap_workout(name: str, steps: list, est_secs: int) -> RunningWorkout:
    """Return RunningWorkout instance (required by garminconnect v0.3.3+)."""
    tag = "P179"
    tagged_name = name
    if not name.startswith(tag):
        tagged_name = f"{tag} - {name}"

    segment = WorkoutSegment(
        segmentOrder=1,
        sportType=SPORT,
        workoutSteps=[_to_step(s) for s in steps],
    )
    return RunningWorkout(
        workoutName=tagged_name,
        description="P179 — auto-generated weekly training plan",
        estimatedDurationInSecs=est_secs,
        workoutSegments=[segment],
    )

# ── Workout templates ─────────────────────────────────────────────────────────

def build_easy(name, dist_km, pace_slow, pace_fast):
    """WU 5min → Easy dist_km (speed target) → CD 5min"""
    steps = [
        warmup_step(1, 300, f"Warm up easy 5min — {tm_note('7:00', '6:30')}"),
        active_dist_step(2, dist_km * 1000,
                         desc=f"Easy {dist_km}km @ {pace_slow}–{pace_fast} | {tm_note(pace_slow, pace_fast)}",
                         pace_slow=pace_slow, pace_fast=pace_fast),
        cooldown_step(3, 300, f"Cool down easy 5min — {tm_note('7:00', '6:30')}"),
    ]
    est = 300 + int(dist_km * 1000 / pace_to_ms(pace_fast)) + 300
    return wrap_workout(name, steps, est)


def build_threshold(name, reps, rep_min, t_slow, t_fast, rec_sec=120):
    """WU 15min → N×(rep_min T-pace / rec jog) → CD 10min"""
    rep_sec = rep_min * 60
    rgroup = repeat_group(3, reps, [
        interval_step(4, 1, secs=rep_sec,
                      desc=f"T-pace {rep_min}min @ {t_slow}–{t_fast} | {tm_note(t_slow, t_fast)}",
                      pace_slow=t_slow, pace_fast=t_fast),
        recovery_step(5, 1, rec_sec, desc=f"Jog recovery {rec_sec//60}min — {tm_note('7:00', '6:30')}"),
    ])
    steps = [
        warmup_step(1, 900, f"Warm up easy 15min — {tm_note('7:00', '6:30')}"),
        active_time_step(2, 120, f"2×stride to wake up legs — {tm_note('5:30', '5:00')}"),
        rgroup,
        cooldown_step(6, 600, f"Cool down easy 10min — {tm_note('7:00', '6:30')}"),
    ]
    est = 900 + 120 + reps * (rep_sec + rec_sec) + 600
    return wrap_workout(name, steps, est)


def build_interval(name, reps, rep_dist_m, i_slow, i_fast, rec_sec=180):
    """WU 15min → N×(dist I-pace / rec jog) → CD 10min"""
    rgroup = repeat_group(3, reps, [
        interval_step(4, 1, dist_m=rep_dist_m,
                      desc=f"Interval {rep_dist_m}m @ {i_slow}–{i_fast} | {tm_note(i_slow, i_fast)}",
                      pace_slow=i_slow, pace_fast=i_fast),
        recovery_step(5, 1, rec_sec, desc=f"Jog recovery {rec_sec//60}min — {tm_note('7:00', '6:30')}"),
    ])
    steps = [
        warmup_step(1, 900, f"Warm up easy 15min — {tm_note('7:00', '6:30')}"),
        active_time_step(2, 120, f"2×stride — {tm_note('5:30', '5:00')}"),
        rgroup,
        cooldown_step(6, 600, f"Cool down easy 10min — {tm_note('7:00', '6:30')}"),
    ]
    est = 900 + 120 + reps * (int(rep_dist_m / pace_to_ms(i_fast)) + rec_sec) + 600
    return wrap_workout(name, steps, est)


def build_easy_strides(name, easy_km, pace_slow, pace_fast, n_strides=6):
    """WU → Easy km (speed target) → N×(20s stride / 90s jog) → CD"""
    rgroup = repeat_group(3, n_strides, [
        interval_step(4, 1, secs=20, desc=f"Stride 20s — quick feet | {tm_note('5:00', '4:30')}"),
        recovery_step(5, 1, 90, desc=f"Walk/jog recovery 90s — {tm_note('7:00', '6:30')}"),
    ])
    steps = [
        warmup_step(1, 300, f"Warm up easy 5min — {tm_note('7:00', '6:30')}"),
        active_dist_step(2, easy_km * 1000,
                         desc=f"Easy {easy_km}km @ {pace_slow}–{pace_fast} | {tm_note(pace_slow, pace_fast)}",
                         pace_slow=pace_slow, pace_fast=pace_fast),
        rgroup,
        cooldown_step(6, 300, f"Cool down easy 5min — {tm_note('7:00', '6:30')}"),
    ]
    est = 300 + int(easy_km * 1000 / pace_to_ms(pace_fast)) + n_strides * (20 + 90) + 300
    return wrap_workout(name, steps, est)


def build_long(name, dist_km, pace_slow, pace_fast):
    """WU 5min → Long dist_km → CD 5min"""
    steps = [
        warmup_step(1, 300, f"Warm up easy 5min — {tm_note('7:00', '6:30')}"),
        active_dist_step(2, dist_km * 1000,
                         desc=f"Long run {dist_km}km @ {pace_slow}–{pace_fast} | {tm_note(pace_slow, pace_fast)}",
                         pace_slow=pace_slow, pace_fast=pace_fast),
        cooldown_step(3, 300, f"Cool down easy 5min — {tm_note('7:00', '6:30')}"),
    ]
    est = 300 + int(dist_km * 1000 / pace_to_ms(pace_fast)) + 300
    return wrap_workout(name, steps, est)


def build_tt(name="30-min TT — LT2 Calibration"):
    """WU → TT 6×5min laps (speed target) → CD 10min"""
    # 5:15 start, 5:10 after lap 2
    rgroup_1 = repeat_group(3, 2, [
        interval_step(4, 1, secs=300, desc="TT Phase — 5:15/km start",
                      pace_slow="5:20", pace_fast="5:10"),
        active_time_step(5, 1, "lap", child_id=1),  # auto-lap marker
    ])
    # Use simpler approach: 6 separate 5-min steps for clarity
    steps = [
        warmup_step(1, 600, "Warm up easy 10min @ 8.0 km/h"),
        active_time_step(2, 180, "Build 3min @ 9.2–10.0 km/h"),
        # TT: 6×5min laps
        interval_step(3, None, secs=300, desc="TT Lap 1/6 — 5:15/km (11.4 km/h)", pace_slow="5:20", pace_fast="5:10"),
        interval_step(4, None, secs=300, desc="TT Lap 2/6 — 5:15/km adjust if needed", pace_slow="5:20", pace_fast="5:10"),
        interval_step(5, None, secs=300, desc="TT Lap 3/6 — 5:10/km (11.6 km/h) ★ LT2 window", pace_slow="5:15", pace_fast="5:05"),
        interval_step(6, None, secs=300, desc="TT Lap 4/6 — hold pace ★ LT2", pace_slow="5:15", pace_fast="5:05"),
        interval_step(7, None, secs=300, desc="TT Lap 5/6 — hold pace ★ LT2", pace_slow="5:15", pace_fast="5:05"),
        interval_step(8, None, secs=300, desc="TT Lap 6/6 — finish strong ★ LT2", pace_slow="5:15", pace_fast="5:05"),
        cooldown_step(9, 600, "Cool down easy 10min"),
    ]
    est = 600 + 180 + 6 * 300 + 600
    return wrap_workout(name, steps, est)

# ── session_prescriber → workout mapper ──────────────────────────────────────

def session_to_workout(session: dict) -> dict | None:
    stype   = session.get("type", "")
    date_s  = session.get("date", "")
    weekday = session.get("weekday", "")
    workout = session.get("workout", "")

    def _fmt_pace_sec(sec_km: int) -> str:
        m, s = divmod(sec_km, 60)
        return f"{m}:{s:02d}"

    # Pull VDOT paces dynamically from config
    try:
        e_slow = _fmt_pace_sec(VDOT_PACES["E"][1])
        e_fast = _fmt_pace_sec(VDOT_PACES["E"][0])
    except Exception:
        e_slow, e_fast = "6:44", "5:37"   # VDOT 40 fallback

    try:
        t_slow = _fmt_pace_sec(VDOT_PACES["T"][1]) if len(VDOT_PACES["T"]) > 1 else _fmt_pace_sec(VDOT_PACES["T"][0])
        t_fast = _fmt_pace_sec(VDOT_PACES["T"][0])
    except Exception:
        t_slow, t_fast = "5:10", "5:00"   # VDOT 40 fallback

    try:
        i_slow = _fmt_pace_sec(VDOT_PACES["I"][1]) if len(VDOT_PACES["I"]) > 1 else _fmt_pace_sec(VDOT_PACES["I"][0])
        i_fast = _fmt_pace_sec(VDOT_PACES["I"][0])
    except Exception:
        i_slow, i_fast = "4:40", "4:31"   # VDOT 40 fallback

    if stype == "easy":
        m = re.search(r"(\d+(?:\.\d+)?)km", workout)
        dist = float(m.group(1)) if m else 8.0
        return build_easy(f"Easy {dist:.0f}km — {weekday} {date_s}", dist, e_slow, e_fast)

    elif stype == "quality1":
        # Readiness downgrades (daily_brief.py / session_prescriber.py) rewrite
        # `workout` to a plain Easy description but leave `type` as "quality1" —
        # match on the actual text, not just the type, or a downgraded day
        # silently pushes a full T-interval workout to the watch anyway.
        m_reps = re.search(r"(\d+)[×x](\d+)min", workout)
        if not m_reps and re.search(r"\beasy\b", workout, re.IGNORECASE):
            m = re.search(r"(\d+(?:\.\d+)?)km", workout)
            dist = float(m.group(1)) if m else 8.0
            return build_easy(f"Easy {dist:.0f}km — {weekday} {date_s}", dist, e_slow, e_fast)
        reps, rep_min = (int(m_reps.group(1)), int(m_reps.group(2))) if m_reps else (3, 10)
        return build_threshold(f"Quality T {reps}×{rep_min}min — {weekday} {date_s}",
                               reps, rep_min, t_slow, t_fast)

    elif stype == "quality2":
        # Same downgrade check as quality1 above.
        m_reps_check = re.search(r"(\d+)[×x](\d+)min", workout)
        has_interval_marker = "Interval" in workout or "×1km" in workout or "×1000" in workout
        has_strides_marker  = "Strides" in workout or "strides" in workout
        if not m_reps_check and not has_interval_marker and not has_strides_marker \
                and re.search(r"\beasy\b", workout, re.IGNORECASE):
            m = re.search(r"(\d+(?:\.\d+)?)km", workout)
            dist = float(m.group(1)) if m else 8.0
            return build_easy(f"Easy {dist:.0f}km — {weekday} {date_s}", dist, e_slow, e_fast)
        if "Interval" in workout or "×1km" in workout or "×1000" in workout:
            m_reps = re.search(r"(\d+)[×x]", workout)
            reps = int(m_reps.group(1)) if m_reps else 5
            return build_interval(f"Quality I {reps}×1km — {weekday} {date_s}",
                                  reps, 1000, i_slow, i_fast)
        elif "Strides" in workout or "strides" in workout:
            m = re.search(r"(\d+(?:\.\d+)?)km", workout)
            dist = float(m.group(1)) if m else 10.0
            return build_easy_strides(f"Easy+Strides — {weekday} {date_s}", dist, e_slow, e_fast)
        else:
            # Default to threshold
            m_reps = re.search(r"(\d+)[×x](\d+)min", workout)
            reps, rep_min = (int(m_reps.group(1)), int(m_reps.group(2))) if m_reps else (3, 10)
            return build_threshold(f"Quality T {reps}×{rep_min}min — {weekday} {date_s}",
                                   reps, rep_min, t_slow, t_fast)

    elif stype == "easy+strides":
        m = re.search(r"(\d+(?:\.\d+)?)km", workout)
        dist = float(m.group(1)) if m else 8.0
        return build_easy_strides(f"Easy+Strides — {weekday} {date_s}", dist, e_slow, e_fast)

    elif stype == "long":
        m = re.search(r"(\d+(?:\.\d+)?)km", workout)
        dist = float(m.group(1)) if m else 18.0
        return build_long(f"Long Run {dist:.0f}km — {weekday} {date_s}", dist, e_slow, e_fast)

    return None  # strength, rest → skip

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Push weekly workouts to Garmin Connect")
    parser.add_argument("--week",   default="current", choices=["current", "next"])
    parser.add_argument("--upload", action="store_true", help="Upload + schedule workouts")
    parser.add_argument("--tt",     action="store_true", help="Preview/upload 30-min TT only")
    parser.add_argument("--yes",    action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    # ── TT mode ──
    if args.tt:
        tt = build_tt()
        est_min = tt.estimatedDurationInSecs // 60
        print(f"\n{'='*60}")
        print(f"🏁 30-min TT — LT2 Calibration")
        print(f"   Duration: ~{est_min} min | Start pace: 5:15/km (11.4 km/h)")
        print(f"   LT2 window: Lap 3–6 (min 10–30)")
        print(f"   → avg HR min 10–30 = LT2 (TM conditions)")
        print(f"{'='*60}")
        if args.upload:
            date_str = input("Schedule date (YYYY-MM-DD, Enter to skip): ").strip()
            c = get_client()
            result = c.upload_running_workout(tt)
            wid = result.get("workoutId")
            print(f"✅ Uploaded TT (id: {wid})")
            if date_str and wid:
                c.schedule_workout(wid, date_str)
                print(f"📅 Scheduled: {date_str}")
        else:
            print("  รัน --tt --upload เพื่อ push ขึ้น Garmin")
        return

    # ── Weekly plan mode ──
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "session_prescriber.py"),
         "--week", args.week, "--json"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"❌ session_prescriber error: {result.stderr}")
        sys.exit(1)

    plan = json.loads(result.stdout)

    print(f"\n{'='*60}")
    print(f"📅 GARMIN WORKOUT PLAN — {plan['generated_for']}")
    print(f"   Phase : {plan['phase']}")
    print(f"   Focus : {plan['focus']}")
    print(f"{'='*60}")

    to_upload: list[tuple[str, dict]] = []

    for session in plan["sessions"]:
        stype   = session.get("type")
        date_s  = session.get("date")
        weekday = session.get("weekday")

        if stype in ("strength", "rest"):
            icon = "🏋️" if stype == "strength" else "🛌"
            print(f"\n  {date_s} {weekday} — {icon} {session['label']} (ไม่ upload)")
            continue

        w = session_to_workout(session)
        if w:
            est_min = w.estimatedDurationInSecs // 60
            print(f"\n  {date_s} {weekday} — {w.workoutName}")
            print(f"    ~{est_min} min | {session.get('workout','')}")
            if session.get("note"):
                print(f"    Note: {session['note']}")
            to_upload.append((date_s, w))

    print(f"\n{'='*60}")
    print(f"  Running sessions to upload: {len(to_upload)}")
    print(f"{'='*60}")

    if not args.upload:
        print("\n  ▶  รัน --upload เพื่อ push ขึ้น Garmin Connect + schedule\n")
        return

    if not args.yes:
        ans = input(f"\nUpload {len(to_upload)} workouts to Garmin Connect? [Y/n]: ").strip().lower()
        if ans not in ("", "y", "yes"):
            print("Cancelled.")
            return

    c = get_client()

    # Idempotent guard — fetch existing workout names to avoid duplicates on re-run
    existing_names: set[str] = set()
    try:
        existing = c.get_workouts(start=0, limit=100)
        existing_names = {w.get("workoutName", "") for w in (existing or [])}
        if existing_names:
            print(f"  🔍 {len(existing_names)} existing workouts checked for duplicates")
    except Exception:
        pass  # listing workouts is best-effort; if it fails, proceed anyway

    for date_s, workout in to_upload:
        if workout.workoutName in existing_names:
            print(f"  ⏭  SKIP (already exists): {workout.workoutName}")
            continue
        try:
            res = c.upload_running_workout(workout)
            wid = res.get("workoutId")
            print(f"  ✅ {workout.workoutName} (id: {wid})")
            if wid:
                c.schedule_workout(wid, date_s)
                print(f"     📅 Scheduled → {date_s}")
        except Exception as e:
            print(f"  ❌ Failed: {workout.workoutName} — {e}")

    print(f"\n✅ Done — เช็คได้ใน Garmin Connect calendar\n")


def push_weekly_plan_to_garmin(client, plan: dict) -> dict:
    """
    Push and schedule running workouts from a weekly plan to Garmin Connect.
    Uses an idempotent guard to avoid duplicate uploads.
    
    Returns:
        dict: A summary of results.
    """
    results = {
        "uploaded": [],
        "skipped": [],
        "rest_days": [],
        "errors": []
    }
    
    # 1. Gather workouts to upload
    to_upload = []
    for session in plan.get("sessions", []):
        stype = session.get("type")
        date_s = session.get("date")
        weekday = session.get("weekday")
        
        if stype in ("strength", "rest"):
            results["rest_days"].append(f"{date_s} ({weekday}) - {session.get('label')}")
            continue
            
        w = session_to_workout(session)
        if w:
            to_upload.append((date_s, w))
        else:
            results["rest_days"].append(f"{date_s} ({weekday}) - {session.get('label', 'REST')}")

    if not to_upload:
        return results

    # 2. Fetch existing workout names to prevent duplicates
    existing_names = set()
    try:
        existing = client.get_workouts(start=0, limit=100)
        existing_names = {w.get("workoutName", "") for w in (existing or [])}
    except Exception as e:
        results["errors"].append(f"Could not fetch existing workouts: {str(e)}")

    # 3. Push and schedule
    for date_s, workout in to_upload:
        if workout.workoutName in existing_names:
            results["skipped"].append(workout.workoutName)
            continue
        try:
            res = client.upload_running_workout(workout)
            wid = res.get("workoutId")
            if wid:
                client.schedule_workout(wid, date_s)
                results["uploaded"].append(workout.workoutName)
            else:
                results["errors"].append(f"Upload succeeded but no workoutId returned for {workout.workoutName}")
        except Exception as e:
            results["errors"].append(f"Failed to push {workout.workoutName}: {str(e)}")

    return results


if __name__ == "__main__":
    main()
