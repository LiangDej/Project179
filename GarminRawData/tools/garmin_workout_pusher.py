#!/usr/bin/env python3
"""
garmin_workout_pusher.py — Build & push weekly workouts to Garmin Connect

reads session_prescriber --json → builds Garmin workout JSON → preview → upload + schedule

Usage:
    python3 garmin_workout_pusher.py              # preview สัปดาห์นี้
    python3 garmin_workout_pusher.py --upload     # preview + upload
    python3 garmin_workout_pusher.py --week next  # สัปดาห์หน้า
    python3 garmin_workout_pusher.py --week next --upload
    python3 garmin_workout_pusher.py --plan-file my_week.json [--upload]   # hand-adjusted week
"""

from __future__ import annotations

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

# ── Paces from VDOT (config → vdot_math) — no literal paces in this file ─────

def _fmt_sec(sec_km: float) -> str:
    sec_km = int(round(sec_km))
    return f"{sec_km // 60}:{sec_km % 60:02d}"


PACE = {k: (_fmt_sec(v[1]), _fmt_sec(v[0])) for k, v in VDOT_PACES.items()}  # zone → (slow, fast)
JOG_NOTE    = tm_note(*PACE["E"])          # warm-up / cool-down / recovery jog
STRIDE_NOTE = tm_note(*PACE["R"])          # strides = R pace


def _rest_secs(rest: str | None) -> int:
    """'90s' / '2min' / '400m jog' → seconds (distance jogs at the slow E pace)."""
    if not rest:
        return 120
    r = rest.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(s|sec|min|m)\b", r)
    if not m:
        return 120
    v, unit = float(m.group(1)), m.group(2)
    if unit in ("s", "sec"):
        return int(v)
    if unit == "min":
        return int(v * 60)
    return int(v / pace_to_ms(PACE["E"][0]))   # metres of jog


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
        warmup_step(1, 300, f"Warm up easy 5min — {JOG_NOTE}"),
        active_dist_step(2, dist_km * 1000,
                         desc=f"Easy {dist_km}km @ {pace_slow}–{pace_fast} | {tm_note(pace_slow, pace_fast)}",
                         pace_slow=pace_slow, pace_fast=pace_fast),
        cooldown_step(3, 300, f"Cool down easy 5min — {JOG_NOTE}"),
    ]
    est = 300 + int(dist_km * 1000 / pace_to_ms(pace_fast)) + 300
    return wrap_workout(name, steps, est)


def build_threshold(name, reps, rep_min, t_slow, t_fast, rec_sec=120, skip_warmup=False):
    """[WU 15min → 2×stride →] N×(rep_min T-pace / rec jog) → CD 10min

    skip_warmup=True omits both the warmup jog and the 2×stride wake-up
    (for athletes who warm up and stride out on their own before starting
    the watch workout) — main set / cooldown unchanged.
    """
    rep_sec = rep_min * 60
    order = 1
    steps = []
    if not skip_warmup:
        steps.append(warmup_step(order, 900, f"Warm up easy 15min — {JOG_NOTE}"))
        order += 1
        steps.append(active_time_step(order, 120, f"2×stride to wake up legs — {STRIDE_NOTE}"))
        order += 1
    rgroup = repeat_group(order, reps, [
        interval_step(order + 1, 1, secs=rep_sec,
                      desc=f"T-pace {rep_min}min @ {t_slow}–{t_fast} | {tm_note(t_slow, t_fast)}",
                      pace_slow=t_slow, pace_fast=t_fast),
        recovery_step(order + 2, 1, rec_sec, desc=f"Jog recovery {rec_sec//60}min — {JOG_NOTE}"),
    ])
    steps.append(rgroup)
    order += 3   # group + its 2 children each take a stepOrder (v1.8.1 collision fix)
    steps.append(cooldown_step(order, 600, f"Cool down easy 10min — {JOG_NOTE}"))
    est = (0 if skip_warmup else 1020) + reps * (rep_sec + rec_sec) + 600
    return wrap_workout(name, steps, est)


def build_interval(name, reps, rep_dist_m, i_slow, i_fast, rec_sec=180, skip_warmup=False):
    """[WU 15min → 2×stride →] N×(dist I-pace / rec jog) → CD 10min

    skip_warmup=True omits both the warmup jog and the 2×stride wake-up
    (for athletes who warm up and stride out on their own before starting
    the watch workout) — main set / cooldown unchanged.
    """
    order = 1
    steps = []
    if not skip_warmup:
        steps.append(warmup_step(order, 900, f"Warm up easy 15min — {JOG_NOTE}"))
        order += 1
        steps.append(active_time_step(order, 120, f"2×stride — {STRIDE_NOTE}"))
        order += 1
    rgroup = repeat_group(order, reps, [
        interval_step(order + 1, 1, dist_m=rep_dist_m,
                      desc=f"Interval {rep_dist_m}m @ {i_slow}–{i_fast} | {tm_note(i_slow, i_fast)}",
                      pace_slow=i_slow, pace_fast=i_fast),
        recovery_step(order + 2, 1, rec_sec, desc=f"Jog recovery {rec_sec//60}min — {JOG_NOTE}"),
    ])
    steps.append(rgroup)
    order += 3   # group + its 2 children each take a stepOrder (v1.8.1 collision fix)
    steps.append(cooldown_step(order, 600, f"Cool down easy 10min — {JOG_NOTE}"))
    est = (0 if skip_warmup else 1020) + reps * (int(rep_dist_m / pace_to_ms(i_fast)) + rec_sec) + 600
    return wrap_workout(name, steps, est)


def build_easy_strides(name, easy_km, pace_slow, pace_fast, n_strides=6):
    """WU → Easy km (speed target) → N×(20s stride / 90s jog) → CD"""
    rgroup = repeat_group(3, n_strides, [
        interval_step(4, 1, secs=20, desc=f"Stride 20s — quick feet | {STRIDE_NOTE}"),
        recovery_step(5, 1, 90, desc=f"Walk/jog recovery 90s — {JOG_NOTE}"),
    ])
    steps = [
        warmup_step(1, 300, f"Warm up easy 5min — {JOG_NOTE}"),
        active_dist_step(2, easy_km * 1000,
                         desc=f"Easy {easy_km}km @ {pace_slow}–{pace_fast} | {tm_note(pace_slow, pace_fast)}",
                         pace_slow=pace_slow, pace_fast=pace_fast),
        rgroup,
        cooldown_step(6, 300, f"Cool down easy 5min — {JOG_NOTE}"),
    ]
    est = 300 + int(easy_km * 1000 / pace_to_ms(pace_fast)) + n_strides * (20 + 90) + 300
    return wrap_workout(name, steps, est)


def build_long(name, dist_km, pace_slow, pace_fast, m_finish_km: float = 0):
    """WU 5min → Long E (dist − M finish) → [M-pace finish] → CD 5min"""
    e_km = dist_km - (m_finish_km or 0)
    steps = [warmup_step(1, 300, f"Warm up easy 5min — {JOG_NOTE}"),
             active_dist_step(2, e_km * 1000,
                              desc=f"Long run E {e_km:g}km @ {pace_slow}–{pace_fast} | {tm_note(pace_slow, pace_fast)}",
                              pace_slow=pace_slow, pace_fast=pace_fast)]
    est = 300 + int(e_km * 1000 / pace_to_ms(pace_fast)) + 300
    if m_finish_km:
        m_slow, m_fast = PACE["M"]
        steps.append(active_dist_step(3, m_finish_km * 1000,
                                      desc=f"M-pace finish {m_finish_km:g}km @ {m_fast}–{m_slow} | {tm_note(m_slow, m_fast)}",
                                      pace_slow=m_slow, pace_fast=m_fast))
        est += int(m_finish_km * 1000 / pace_to_ms(m_fast))
    steps.append(cooldown_step(len(steps) + 1, 300, f"Cool down easy 5min — {JOG_NOTE}"))
    return wrap_workout(name, steps, est)


def build_tt(name="30-min TT — LT2 Calibration"):
    """WU → 30-min TT as 6×5min laps (start at the slow edge of T, settle into
    T from lap 3 — Friel LTHR test) → CD 10min. Paces from current VDOT."""
    t_slow, t_fast = PACE["T"]
    t_tol = _fmt_sec(VDOT_PACES["T"][1] + 5)
    laps = []
    for i in range(6):
        slow, fast = (t_tol, t_slow) if i < 2 else (t_slow, t_fast)
        tag = " ★ LT2 window" if i >= 2 else ""
        laps.append(interval_step(3 + i, None, secs=300,
                                  desc=f"TT Lap {i + 1}/6 — {fast}–{slow}/km ({tm_note(slow, fast)}){tag}",
                                  pace_slow=slow, pace_fast=fast))
    steps = [warmup_step(1, 600, f"Warm up easy 10min — {JOG_NOTE}"),
             active_time_step(2, 180, f"Build 3min → T — {tm_note(PACE['M'][0], PACE['T'][0])}"),
             *laps,
             cooldown_step(9, 600, f"Cool down easy 10min — {JOG_NOTE}")]
    return wrap_workout(name, steps, 600 + 180 + 6 * 300 + 600)


def build_from_spec(name: str, spec: dict, skip_warmup: bool = False):
    """Quality workout from a session `spec` (T/I/R/M or combo parts) — the
    exact reps the planner settled on after JD caps; no text parsing."""
    parts = spec.get("parts") or [spec]
    order, steps, est, group_id = 1, [], 0, 0
    if not skip_warmup:
        steps.append(warmup_step(order, 900, f"Warm up easy 15min — {JOG_NOTE}")); order += 1
        steps.append(active_time_step(order, 120, f"2×stride to wake up legs — {STRIDE_NOTE}")); order += 1
        est += 1020
    for part in parts:
        kind = part["type"]
        slow, fast = PACE[kind]
        reps = int(part.get("reps", 1))
        rec = _rest_secs(part.get("rest"))
        if part.get("rep_min"):
            body = dict(secs=int(part["rep_min"] * 60)); lab = f"{part['rep_min']}min"
            work = body["secs"]
        else:
            dist = int((part.get("rep_km") or part.get("km") or 0) * 1000 or part.get("rep_m", 0))
            body = dict(dist_m=dist); lab = f"{dist}m"
            work = int(dist / pace_to_ms(fast))
        group_id += 1
        main = interval_step(order + 1, group_id, desc=f"{kind}-pace {lab} @ {fast}–{slow} | {tm_note(slow, fast)}",
                             pace_slow=slow, pace_fast=fast, **body)
        if reps > 1:
            steps.append(repeat_group(order, reps, [
                main, recovery_step(order + 2, group_id, rec, desc=f"Jog recovery {rec}s — {JOG_NOTE}")],
                child_id=group_id))
            est += reps * (work + rec)
            order += 3   # group + 2 children (unique stepOrder / childStepId per group)
        else:
            main["stepOrder"] = order
            main["childStepId"] = None
            steps.append(main)
            est += work
            order += 1
    steps.append(cooldown_step(order, 600, f"Cool down easy 10min — {JOG_NOTE}"))
    return wrap_workout(name, steps, est + 600)

# ── session_prescriber → workout mapper ──────────────────────────────────────

def session_to_workout(session: dict, skip_warmup: bool = False) -> dict | None:
    stype   = session.get("type", "")
    date_s  = session.get("date", "")
    weekday = session.get("weekday", "")
    workout = session.get("workout", "")

    def _fmt_pace_sec(sec_km: int) -> str:
        m, s = divmod(sec_km, 60)
        return f"{m}:{s:02d}"

    e_slow, e_fast = PACE["E"]
    t_slow, t_fast = PACE["T"]
    i_slow, i_fast = PACE["I"]

    # REST / RACE never become a watch workout (v1.8.1: a readiness REST on a
    # quality day fell through to the text parser and built T 3×10min).
    if session.get("decision") in ("REST", "RACE") or stype in ("rest", "strength", "race"):
        return None

    # v1.8: sessions carry the final structured spec — use it, no regex.
    spec = session.get("spec")
    if spec:
        st = spec.get("type")
        if st == "easy":
            return build_easy(f"Easy {spec['km']:g}km — {weekday} {date_s}", spec["km"], e_slow, e_fast)
        if st == "E+strides":
            return build_easy_strides(f"Easy+Strides — {weekday} {date_s}", spec["km"], e_slow, e_fast,
                                      n_strides=spec.get("strides", 6))
        if st == "long":
            return build_long(f"Long Run {spec['km']:g}km — {weekday} {date_s}", spec["km"], e_slow, e_fast,
                              m_finish_km=spec.get("m_finish_km") or 0)
        if st in ("T", "I", "R", "M", "combo"):
            return build_from_spec(f"Quality {st} — {weekday} {date_s}", spec, skip_warmup)

    # Legacy plan files (no spec) — text parsing below.

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
                               reps, rep_min, t_slow, t_fast, skip_warmup=skip_warmup)

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
                                  reps, 1000, i_slow, i_fast, skip_warmup=skip_warmup)
        elif "Strides" in workout or "strides" in workout:
            m = re.search(r"(\d+(?:\.\d+)?)km", workout)
            dist = float(m.group(1)) if m else 10.0
            return build_easy_strides(f"Easy+Strides — {weekday} {date_s}", dist, e_slow, e_fast)
        else:
            # Default to threshold
            m_reps = re.search(r"(\d+)[×x](\d+)min", workout)
            reps, rep_min = (int(m_reps.group(1)), int(m_reps.group(2))) if m_reps else (3, 10)
            return build_threshold(f"Quality T {reps}×{rep_min}min — {weekday} {date_s}",
                                   reps, rep_min, t_slow, t_fast, skip_warmup=skip_warmup)

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
    parser.add_argument("--no-warmup", action="store_true",
                         help="Omit the watch-guided warmup jog + stride wake-up on Quality (T/I) workouts — athlete warms up on their own before starting the structured set")
    parser.add_argument("--plan-file", metavar="PATH",
                         help="Read the weekly plan from a JSON file (same shape as `session_prescriber.py --json`) "
                              "instead of generating it — for hand-adjusted weeks (travel, mini-taper). --week is ignored.")
    args = parser.parse_args()

    # ── TT mode ──
    if args.tt:
        tt = build_tt()
        est_min = tt.estimatedDurationInSecs // 60
        print(f"\n{'='*60}")
        print(f"🏁 30-min TT — LT2 Calibration")
        print(f"   Duration: ~{est_min} min | Start: T {PACE['T'][0]}/km ({tm_note(*PACE['T'])})")
        print(f"   LT2 window: Lap 3–6 (min 10–30)")
        print(f"   → avg HR min 10–30 = LT2 (TM conditions)")
        print(f"{'='*60}")
        if args.upload:
            date_str = input("Schedule date (YYYY-MM-DD, Enter to skip): ").strip() if sys.stdin.isatty() else ""
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
    if args.plan_file:
        try:
            plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"❌ --plan-file error: {e}")
            sys.exit(1)
        missing = [k for k in ("generated_for", "phase", "focus", "sessions") if k not in plan]
        if missing:
            print(f"❌ --plan-file missing keys: {', '.join(missing)}")
            sys.exit(1)
    else:
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

        if stype in ("strength", "rest") or session.get("decision") in ("REST", "RACE"):
            icon = "🏋️" if stype == "strength" else "🛌"
            print(f"\n  {date_s} {weekday} — {icon} {session['label']} (ไม่ upload)")
            continue

        w = session_to_workout(session, skip_warmup=args.no_warmup)
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
        if not sys.stdin.isatty():
            print("❌ non-interactive run: pass --yes to upload (refusing to hang on a prompt)")
            return
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


def push_weekly_plan_to_garmin(client, plan: dict, skip_warmup: bool = False) -> dict:
    """
    Push and schedule running workouts from a weekly plan to Garmin Connect.
    Uses an idempotent guard to avoid duplicate uploads.

    skip_warmup=True omits the watch-guided warmup on Quality (T/I) workouts.

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
        
        if stype in ("strength", "rest") or session.get("decision") in ("REST", "RACE"):
            results["rest_days"].append(f"{date_s} ({weekday}) - {session.get('label')}")
            continue
            
        w = session_to_workout(session, skip_warmup=skip_warmup)
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
