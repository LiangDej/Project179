#!/usr/bin/env python3
"""
session_logger.py — Central Session Master Logger

บันทึกข้อมูล session แบบ lap-level ลง sessions_master.json
รองรับ 5 session types: easy | interval | tempo | threshold | fast_finish

Usage:
    python3 session_logger.py --latest            # log latest activity (interactive)
    python3 session_logger.py --id 22826276241    # log specific activity (interactive)
    python3 session_logger.py --batch-easy        # import easy runs ทั้งหมดตั้งแต่ --since
    python3 session_logger.py --list              # แสดง sessions ทั้งหมด
    python3 session_logger.py --summary           # trend แยก type

Called automatically by post_session_analyzer via auto_log().
"""

import sys
import os
import json
import argparse
from pathlib import Path
from datetime import datetime, date, timedelta

BASE_DIR   = Path(__file__).parent.parent
TOOLS_DIR  = Path(__file__).parent
COACH_MCP  = BASE_DIR.parent / "skills" / "garmin_coach_mcp"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from treadmill_pace_model import infer_pace_from_hr as _tm_pace_from_hr  # noqa: E402

from config import ATHLETE, VDOT_PACES, HR_ZONE_BOUNDS  # noqa: E402
from garmin_client import get_client, get_health_cached, get_laps_cached  # noqa: E402

MASTER_PATH     = BASE_DIR / "QualitySessionLog" / "sessions_master.json"
ACTIVITIES_PATH = BASE_DIR / "running_activities_all.json"

SESSION_TYPES = {
    "1": ("easy",        "Easy Run — aerobic base, HR < Z2"),
    "2": ("interval",    "Interval (I) — reps at I-pace, full recovery between"),
    "3": ("tempo",       "Tempo — sustained M-pace 20–40min"),
    "4": ("threshold",   "Threshold (T) — cruise intervals or sustained T-pace"),
    "5": ("fast_finish", "Fast Finish — Easy + last 20–30% at M or T pace"),
}

LAP_ROLES = {"w": "warmup", "q": "quality", "r": "recovery", "c": "cooldown"}

EASY_HR_THRESHOLD = HR_ZONE_BOUNDS["Z1_E"][1]   # Z1 Easy ceiling (155) from config — avg HR ≤ this → easy
MIN_DIST_KM       = 3.0   # skip activities shorter than this


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_master() -> dict:
    if MASTER_PATH.exists():
        with open(MASTER_PATH) as f:
            return json.load(f)
    return {"_schema_version": "1.0", "sessions": []}


def _save_master(data: dict):
    MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MASTER_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, MASTER_PATH)  # atomic — safe against crash mid-write


def _load_activities() -> list:
    if not ACTIVITIES_PATH.exists():
        return []
    with open(ACTIVITIES_PATH) as f:
        return json.load(f)


def _is_logged(activity_id: int, master: dict) -> bool:
    return any(s.get("activity_id") == activity_id for s in master.get("sessions", []))


def _fmt_pace(sec_km) -> str:
    if not sec_km:
        return "N/A"
    m, s = divmod(int(sec_km), 60)
    return f"{m}:{s:02d}/km"


def _infer_tm_pace_from_hr(avg_hr: float | None) -> str:
    """Infer TM pace from HR. Source: treadmill_pace_model.py (single source of truth)."""
    result = _tm_pace_from_hr(avg_hr)
    return f"{result}~" if result else "N/A~"   # ~ = HR-inferred (not belt speed)


# ---------------------------------------------------------------------------
# Lap field slimming — keep only what analysis tools need
# ---------------------------------------------------------------------------

_LAP_KEEP = {"lap_num", "role", "distance_km", "pace", "avg_hr", "max_hr", "duration_s"}

def _slim_lap(lap: dict) -> dict:
    return {k: v for k, v in lap.items() if k in _LAP_KEEP}


# ---------------------------------------------------------------------------
# Lap auto-annotation (non-interactive)
# ---------------------------------------------------------------------------

def _auto_annotate_laps(laps: list, session_type: str, is_treadmill: bool = False) -> list:
    """
    Auto-assign lap roles without prompting.
      easy / tempo / fast_finish  → all laps = quality
      threshold                   → alternating quality/recovery
      interval                    → alternating quality/recovery (odd=quality)
    First lap is always warmup if ≥2 laps and pace > E-pace threshold.
    Last lap is always cooldown if ≥3 laps.
    """
    annotated = []
    n = len(laps)
    # Pre-scan: detect strides (short ACTIVE laps followed by ACTIVE, not RECOVERY)
    for i, lap in enumerate(laps):
        it = (lap.get("intensityType") or "").upper()
        if it in ("ACTIVE", "INTERVAL") and (lap.get("duration_s") or 0) < 180:
            nxt = laps[i + 1] if i + 1 < n else None
            nxt_it = (nxt.get("intensityType") or "").upper() if nxt else ""
            if nxt_it in ("ACTIVE", "INTERVAL"):
                lap["_is_stride"] = True

    for i, lap in enumerate(laps):
        dist_km = round(lap["distance_m"] / 1000, 2)
        pace    = _fmt_pace(lap.get("avg_pace_sec_km"))
        intensity = (lap.get("intensityType") or "").upper()

        # Primary: use Garmin's intensityType when available
        if intensity == "WARMUP":
            role = "warmup"
        elif intensity == "COOLDOWN":
            role = "cooldown"
        elif intensity in ("RECOVERY", "REST"):
            role = "recovery"
        elif lap.get("_is_stride"):
            role = "stride"
        elif intensity in ("ACTIVE", "INTERVAL"):
            role = "quality" if session_type != "easy" else "quality"
        # Fallback: position-based heuristic if no intensityType
        elif session_type == "easy":
            role = "quality"
        elif n <= 2:
            role = "quality"
        elif i == 0:
            role = "warmup"
        elif i == n - 1:
            role = "cooldown"
        elif session_type in ("interval", "threshold"):
            role = "quality" if i % 2 == 1 else "recovery"
        else:
            role = "quality"

        avg_hr_val = lap.get("avg_hr") or lap.get("averageHR")
        # For treadmill sessions GPS pace is unreliable — infer from HR instead
        if is_treadmill and session_type != "easy":
            pace = _infer_tm_pace_from_hr(avg_hr_val)
        annotated.append({
            "lap_num":     i + 1,
            "distance_km": dist_km,
            "duration_s":  lap.get("duration_s") or lap.get("totalElapsedDuration"),
            "pace":        pace,
            "avg_hr":      avg_hr_val,
            "max_hr":      lap.get("max_hr") or lap.get("maxHR"),
            "role":        role,
        })
    return annotated


# ---------------------------------------------------------------------------
# Interactive lap annotation
# ---------------------------------------------------------------------------

def _annotate_laps_interactive(laps: list, session_type: str, is_treadmill: bool = False) -> list:
    auto = _auto_annotate_laps(laps, session_type, is_treadmill=is_treadmill)
    print(f"\n📋 Lap Annotation ({len(laps)} laps) — Enter to accept suggestion")
    print("   Labels: [w]armup  [q]uality  [r]ecovery  [c]ooldown  [s]kip")
    print(f"   {'#':<3} {'dist':>6} {'pace':>8} {'hr':>5}  suggest  → label?")
    print("   " + "─" * 50)
    annotated = []
    for lap in auto:
        suggest = lap["role"][0] if lap["role"] else "q"
        print(f"   {lap['lap_num']:<3} {lap['distance_km']:>6.2f}km "
              f"{lap['pace']:>8} {str(lap.get('avg_hr','—')):>5}  [{suggest}]", end="  ")
        raw = input().strip().lower() or suggest
        lap["role"] = LAP_ROLES.get(raw, "quality") if raw != "s" else None
        annotated.append(lap)
    return annotated


# ---------------------------------------------------------------------------
# Treadmill phases
# ---------------------------------------------------------------------------

def _prompt_treadmill_phases() -> list | None:
    print("\n🏃 Treadmill Session — กรอก phases จากหน้าจอลู่วิ่ง")
    print("   Format: speed1xkm1,speed2xkm2,...  เช่น  8.4x2,11.2x6,8.4x2")
    raw = input("   Phases (Enter=skip): ").strip()
    if not raw:
        return None
    phases = []
    for part in raw.split(","):
        part = part.strip()
        if "x" not in part:
            continue
        try:
            spd_str, km_str = part.split("x", 1)
            speed   = float(spd_str)
            dist_km = float(km_str)
            phases.append({
                "speed_kmh":    speed,
                "distance_km":  dist_km,
                "duration_min": round(dist_km / speed * 60, 1),
                "pace":         _fmt_pace(round(3600 / speed)),
            })
        except ValueError:
            continue
    return phases if phases else None


# ---------------------------------------------------------------------------
# Core record builder
# ---------------------------------------------------------------------------

def _build_record(activity: dict, laps_data: dict, health: dict | None,
                  session_type: str, interactive: bool = True,
                  treadmill_phases: list | None = None,
                  date_str_fallback: str = "") -> dict:
    activity_id = activity.get("activityId")

    # Date — try multiple field formats, fall back to caller-supplied value
    date_str = (activity.get("startTimeLocal") or activity.get("date") or "")[:10] or date_str_fallback

    # Treadmill detection — multiple paths: legacy, MCP, or summaryDTO
    summary_dto = (activity.get("_activitySummary") or {})
    type_key = (activity.get("activityType") or {}).get("typeKey") or activity.get("type", "")
    if not type_key:
        type_key = (summary_dto.get("activityTypeDTO") or {}).get("typeKey", "")
    is_treadmill = type_key == "treadmill_running"

    # Distance — try direct fields, then nested summaryDTO
    dist_m  = activity.get("distance") or 0
    dist_km_direct = activity.get("distance_km") or 0
    if not dist_m and not dist_km_direct:
        dist_m = (summary_dto.get("summaryDTO") or {}).get("distance") or 0
    total_km = round(dist_km_direct if dist_km_direct else dist_m / 1000, 2)

    # HR — multiple field names across API versions
    avg_hr = (activity.get("averageHR") or activity.get("averageHeartRate")
              or activity.get("avg_hr"))
    max_hr = (activity.get("maxHR") or activity.get("maxHeartRate")
              or activity.get("max_hr"))
    if not avg_hr:
        avg_hr = (summary_dto.get("summaryDTO") or {}).get("averageHR")
    if not max_hr:
        max_hr = (summary_dto.get("summaryDTO") or {}).get("maxHR")
    # Sanity bounds — Garmin occasionally emits 0 or garbage values
    if avg_hr is not None and not (30 <= int(avg_hr) <= 220):
        avg_hr = None
    if max_hr is not None and not (30 <= int(max_hr) <= 220):
        max_hr = None

    laps = laps_data.get("laps", [])

    # Treadmill phases — interactive only for quality sessions
    if is_treadmill and treadmill_phases is None and interactive and session_type != "easy":
        treadmill_phases = _prompt_treadmill_phases()

    # Lap annotation — pass is_treadmill so pace inference kicks in for TM quality laps
    if interactive and session_type != "easy":
        annotated_laps = _annotate_laps_interactive(laps, session_type, is_treadmill=is_treadmill)
    else:
        annotated_laps = _auto_annotate_laps(laps, session_type, is_treadmill=is_treadmill)

    quality_laps  = [l for l in annotated_laps if l.get("role") == "quality"]
    warmup_laps   = [l for l in annotated_laps if l.get("role") == "warmup"]
    cooldown_laps = [l for l in annotated_laps if l.get("role") == "cooldown"]

    quality_km    = round(sum(l["distance_km"] for l in quality_laps), 2)
    warmup_km     = round(sum(l["distance_km"] for l in warmup_laps), 2)
    cooldown_km   = round(sum(l["distance_km"] for l in cooldown_laps), 2)

    q_hrs = [l["avg_hr"] for l in quality_laps if l.get("avg_hr")]
    quality_hr_avg = round(sum(q_hrs) / len(q_hrs)) if q_hrs else avg_hr

    # Easy sessions don't need lap-level detail — saves ~2KB per session
    if session_type == "easy":
        stored_laps = None
    else:
        stored_laps = [_slim_lap(l) for l in annotated_laps]

    return {
        "date":              date_str,
        "activity_id":       activity_id,
        "session_type":      session_type,
        "is_treadmill":      is_treadmill,
        "total_km":          total_km,
        "warmup_km":         warmup_km,
        "cooldown_km":       cooldown_km,
        "quality_km":        quality_km,
        "avg_hr":            avg_hr,
        "max_hr":            max_hr,
        "quality_hr_avg":    quality_hr_avg,
        "laps":              stored_laps,
        "treadmill_phases":  treadmill_phases,
        "bb_start":          health.get("bb_high") if health else None,
        "bb_end":            health.get("body_battery") if health else None,
        "logged_at":         datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# auto_log — called by post_session_analyzer (non-interactive for easy)
# ---------------------------------------------------------------------------

def auto_log(activity_id: int, activity_data: dict, session_result: dict, date_str: str):
    """
    Non-interactive log called automatically from post_session_analyzer.
    - Easy sessions: fully automatic (no prompts)
    - Quality sessions: interactive (prompts for type + lap annotation + treadmill phases)
    """
    master = _load_master()
    if _is_logged(activity_id, master):
        return  # already logged, silent skip

    session_type_raw = session_result.get("session_type", "Easy Run")
    is_easy = session_type_raw == "Easy Run"

    # Map post_session_analyzer type → session_logger type
    type_map = {
        "Easy Run":       "easy",
        "Marathon Pace":  "tempo",
        "Threshold (T)":  "threshold",
        "Interval (I)":   "interval",
        "Repetition (R)": "interval",
    }
    session_type = type_map.get(session_type_raw, "easy")

    # Fetch lap data
    try:
        client    = get_client()
        laps_data = get_laps_cached(client, activity_id)
        health    = get_health_cached(client, date_str)
    except Exception:
        laps_data = {"laps": []}
        health    = None

    # Enrich laps with intensityType from _activitySplits (Garmin's WU/ACTIVE/RECOVERY/CD tags)
    splits = (activity_data.get("_activitySplits") or {}).get("lapDTOs") or []
    if splits and laps_data.get("laps"):
        for i, lap in enumerate(laps_data["laps"]):
            if i < len(splits):
                lap["intensityType"] = splits[i].get("intensityType")

    if is_easy:
        # Fully automatic — no prompts
        record = _build_record(activity_data, laps_data, health,
                               session_type="easy", interactive=False,
                               date_str_fallback=date_str)
        # Required field guard — skip corrupt records silently
        if not record.get("date") or not record.get("activity_id") or not record.get("total_km"):
            print(f"⚠️  auto_log: missing required fields (date/activity_id/total_km) — skip")
            return
        master.setdefault("sessions", []).append(record)
        # Sort by activity_id (monotonic with time) not date-string — a
        # non-ISO "date" value (e.g. "custom") would otherwise sort above
        # every real date and pin a stale session at index 0 permanently.
        master["sessions"].sort(key=lambda s: s.get("activity_id", 0), reverse=True)
        _save_master(master)
        print(f"✅ Auto-logged (easy) → sessions_master.json | {record['total_km']}km")
    else:
        # Non-interactive path when rep_speeds provided (UAT/automation)
        non_interactive = bool(session_result.get("_rep_speeds_override"))
        if not non_interactive:
            print(f"\n📝 Session Master — log quality session {activity_id}")
            print("   (Enter ข้ามได้ถ้าไม่ต้องการ log ตอนนี้)")
            confirm = input("   Log to sessions_master? [Y/n]: ").strip().lower()
            if confirm == "n":
                return

            # Let user confirm/change session type
            print(f"\n🏷️  Type detected: {session_type}")
            for k, (t, desc) in SESSION_TYPES.items():
                marker = " ◀" if t == session_type else ""
                print(f"   [{k}] {t:<12} — {desc}{marker}")
            choice = input("   เลือก (Enter=keep): ").strip()
            if choice in SESSION_TYPES:
                session_type = SESSION_TYPES[choice][0]
        else:
            print(f"\n📝 Auto-logging quality session {activity_id} (rep_speeds provided)")

        treadmill_phases = None
        type_key = (activity_data.get("activityType") or {}).get("typeKey")
        if not type_key:
            type_key = (((activity_data.get("_activitySummary") or {})
                         .get("activityTypeDTO") or {}).get("typeKey"))
        is_treadmill = type_key == "treadmill_running"
        # Non-interactive path: rep_speeds passed via session_result
        rep_speeds = session_result.get("_rep_speeds_override")
        if is_treadmill and rep_speeds:
            # Auto-build treadmill phases from rep_speeds (kmh)
            # WU+CD use easy default, reps use given speeds
            treadmill_phases = [
                {"speed_kmh": kmh, "duration_min": 10}
                for kmh in rep_speeds
            ]
        elif is_treadmill:
            treadmill_phases = _prompt_treadmill_phases()

        record = _build_record(activity_data, laps_data, health,
                               session_type=session_type,
                               interactive=not non_interactive,
                               treadmill_phases=treadmill_phases,
                               date_str_fallback=date_str)
        master.setdefault("sessions", []).append(record)
        # Sort by activity_id (monotonic with time) not date-string — a
        # non-ISO "date" value (e.g. "custom") would otherwise sort above
        # every real date and pin a stale session at index 0 permanently.
        master["sessions"].sort(key=lambda s: s.get("activity_id", 0), reverse=True)
        _save_master(master)
        q_count = len([l for l in record["laps"] if l.get("role") == "quality"])
        print(f"✅ Logged → sessions_master.json | {session_type} | quality laps: {q_count}")


# ---------------------------------------------------------------------------
# Batch import easy runs
# ---------------------------------------------------------------------------

def batch_import_easy(since_date: str | None = None):
    """Import all easy sessions non-interactively."""
    master     = _load_master()
    activities = _load_activities()
    cutoff     = date.fromisoformat(since_date) if since_date else date(2026, 3, 26)

    easy_acts = []
    for a in activities:
        dt_str = a.get("startTimeLocal", "")[:10]
        try:
            dt = date.fromisoformat(dt_str)
        except ValueError:
            continue
        if dt < cutoff:
            continue
        dist_km = (a.get("distance") or 0) / 1000
        avg_hr  = a.get("averageHR") or a.get("averageHeartRate") or 999
        act_id  = a.get("activityId")
        if dist_km < MIN_DIST_KM:
            continue
        if avg_hr > EASY_HR_THRESHOLD:
            continue
        if _is_logged(act_id, master):
            continue
        easy_acts.append(a)

    easy_acts.sort(key=lambda a: a.get("startTimeLocal", ""))
    print(f"\n📦 Batch import easy runs ตั้งแต่ {cutoff} — พบ {len(easy_acts)} sessions")

    try:
        client = get_client()
    except Exception as e:
        print(f"❌ Garmin client error: {e}")
        return

    imported = 0
    for a in easy_acts:
        act_id   = a.get("activityId")
        dt_str   = a.get("startTimeLocal", "")[:10]
        dist_km  = round((a.get("distance") or 0) / 1000, 2)
        avg_hr   = a.get("averageHR") or "—"
        print(f"   {dt_str} | {act_id} | {dist_km}km | HR {avg_hr} ... ", end="", flush=True)
        try:
            laps_data = get_laps_cached(client, act_id)
            health    = get_health_cached(client, dt_str)
            record    = _build_record(a, laps_data, health,
                                      session_type="easy", interactive=False)
            master.setdefault("sessions", []).append(record)
            imported += 1
            print("✅")
        except Exception as e:
            print(f"❌ {e}")

    master["sessions"].sort(key=lambda s: s.get("activity_id", 0), reverse=True)
    _save_master(master)
    print(f"\n✅ Imported {imported}/{len(easy_acts)} easy sessions → sessions_master.json")


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_list(master: dict):
    sessions = master.get("sessions", [])
    if not sessions:
        print("ไม่มีข้อมูลใน sessions_master.json")
        return
    print(f"\n{'='*68}")
    print(f"📚 SESSION MASTER — {len(sessions)} sessions")
    print(f"{'='*68}")
    print(f"  {'date':<12} {'type':<12} {'km':>5} {'q_km':>5} {'hr_q':>5} {'laps':>5}  note")
    print(f"  {'─'*64}")
    for s in sorted(sessions, key=lambda x: x["date"], reverse=True):
        q_laps = len([l for l in (s.get("laps") or []) if l.get("role") == "quality"])
        tm     = "TM" if s.get("is_treadmill") else "  "
        print(f"  {s['date']:<12} {s['session_type']:<12} {s['total_km']:>5.1f} "
              f"{s.get('quality_km',0):>5.1f} {str(s.get('quality_hr_avg','—')):>5} "
              f"{q_laps:>5}  {tm}")
    print(f"{'='*68}\n")


def print_trend(master: dict):
    sessions = master.get("sessions", [])
    if not sessions:
        return
    by_type: dict[str, list] = {}
    for s in sessions:
        by_type.setdefault(s["session_type"], []).append(s)

    print(f"\n{'='*68}")
    print("📈 TREND BY SESSION TYPE")
    print(f"{'='*68}")
    for stype, sess in sorted(by_type.items()):
        sess_sorted = sorted(sess, key=lambda x: x["date"])
        print(f"\n  [{stype.upper()}] — {len(sess)} sessions")
        print(f"  {'date':<12} {'km':>5} {'q_km':>5} {'hr_q':>6} {'laps':>5}")
        for s in sess_sorted:
            q_laps = len([l for l in (s.get("laps") or []) if l.get("role") == "quality"])
            print(f"  {s['date']:<12} {s['total_km']:>5.1f} {s.get('quality_km',0):>5.1f} "
                  f"{str(s.get('quality_hr_avg','—')):>6} {q_laps:>5}")
    print(f"{'='*68}\n")


def print_weeks(master: dict, n_weeks: int = 12):
    """
    Weekly km aggregation — total km + quality sessions per week.
    Reads sessions_master.json (logged sessions only, not all activities).
    """
    from collections import defaultdict as _dd
    sessions = master.get("sessions", [])
    if not sessions:
        print("ไม่มีข้อมูล session")
        return

    by_week: dict[str, dict] = _dd(lambda: {"km": 0.0, "q": 0, "sessions": 0, "types": []})
    for s in sessions:
        try:
            d = date.fromisoformat(s["date"][:10])
        except (ValueError, TypeError):
            continue  # skip sessions with non-standard dates (e.g. "custom")
        ws = str(d - timedelta(days=d.weekday()))  # Monday
        by_week[ws]["km"]       += s.get("total_km", 0)
        by_week[ws]["sessions"] += 1
        by_week[ws]["types"].append(s["session_type"])
        if s["session_type"] not in ("easy",):
            by_week[ws]["q"] += 1

    # Pull weekly target from training_planner for comparison
    try:
        sys.path.insert(0, str(TOOLS_DIR))
        from training_planner import get_week_info as _gwi
        _target = _gwi().get("km_target", None)
    except Exception:
        _target = None

    sorted_weeks = sorted(by_week.keys())[-n_weeks:]

    print(f"\n{'='*68}")
    print(f"📅 WEEKLY SESSION SUMMARY — last {len(sorted_weeks)} weeks")
    if _target:
        print(f"   Current plan target: {_target} km/week")
    print(f"{'='*68}")
    print(f"  {'สัปดาห์':<12} {'km':>7} {'sessions':>9} {'quality':>8}  types")
    print(f"  {'─'*60}")

    for ws in sorted_weeks:
        w = by_week[ws]
        bar_max = 80
        km_target_ref = _target or 50
        bar_len = min(bar_max, int(w["km"] / km_target_ref * 20))
        bar = "█" * bar_len
        type_str = ", ".join(sorted(set(w["types"])))
        km_flag = " ✅" if (_target and w["km"] >= _target * 0.9) else (" 🔄" if w["km"] > 0 else "")
        print(f"  {ws:<12} {w['km']:>6.1f}{km_flag}  {w['sessions']:>7}  {w['q']:>7}Q  {type_str}")

    total_km = sum(by_week[w]["km"] for w in sorted_weeks)
    total_q  = sum(by_week[w]["q"]  for w in sorted_weeks)
    print(f"  {'─'*60}")
    print(f"  {'TOTAL':<12} {total_km:>6.1f}  {sum(by_week[w]['sessions'] for w in sorted_weeks):>7}  {total_q:>7}Q")
    print(f"{'='*68}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--latest",      action="store_true", help="Log latest activity (interactive)")
    grp.add_argument("--id",          type=int,            help="Log specific activity ID")
    grp.add_argument("--batch-easy",  action="store_true", help="Import all easy runs non-interactively")
    grp.add_argument("--list",        action="store_true", help="Show all logged sessions")
    grp.add_argument("--summary",     action="store_true", help="Trend summary by type")
    grp.add_argument("--weeks",       action="store_true", help="Weekly km aggregation table")
    parser.add_argument("--since",    default="2026-03-26", help="Start date for --batch-easy (YYYY-MM-DD)")
    parser.add_argument("--n",        type=int, default=12, help="Number of weeks to show with --weeks")
    args = parser.parse_args()

    master = _load_master()

    if args.list:
        print_list(master)
        return
    if args.summary:
        print_trend(master)
        return
    if args.weeks:
        print_weeks(master, args.n)
        return
    if args.batch_easy:
        batch_import_easy(args.since)
        return

    # --- Single activity ---
    activities = _load_activities()
    if args.latest:
        activity = sorted(activities, key=lambda a: a.get("startTimeLocal", ""), reverse=True)[0]
    else:
        candidates = [a for a in activities if a.get("activityId") == args.id]
        if not candidates:
            print(f"❌ ไม่พบ activity ID {args.id}")
            return
        activity = candidates[0]

    act_id  = activity.get("activityId")
    dt_str  = activity.get("startTimeLocal", "")[:10]
    dist_km = round((activity.get("distance") or 0) / 1000, 2)
    avg_hr  = activity.get("averageHR") or "—"

    print(f"\n📡 Activity: {act_id} | {dt_str} | {dist_km}km | HR {avg_hr}")

    if _is_logged(act_id, master):
        print(f"⚠️  Activity {act_id} ถูก log แล้ว")
        return

    # Detect type suggestion
    auto_type = "easy" if (activity.get("averageHR") or 999) <= EASY_HR_THRESHOLD else "tempo"
    print(f"\n🏷️  Type suggestion: {auto_type}")
    for k, (t, desc) in SESSION_TYPES.items():
        marker = " ◀" if t == auto_type else ""
        print(f"   [{k}] {t:<12} — {desc}{marker}")
    choice = input("   เลือก (Enter=suggested): ").strip()
    session_type = SESSION_TYPES.get(choice, (auto_type, ""))[0] if choice in SESSION_TYPES else auto_type

    try:
        client    = get_client()
        laps_data = get_laps_cached(client, act_id)
        health    = get_health_cached(client, dt_str)
    except Exception as e:
        print(f"⚠️  {e}")
        laps_data, health = {"laps": []}, None

    treadmill_phases = None
    is_treadmill = activity.get("activityType", {}).get("typeKey") == "treadmill_running"
    if is_treadmill and session_type != "easy":
        treadmill_phases = _prompt_treadmill_phases()

    record = _build_record(activity, laps_data, health,
                           session_type=session_type,
                           interactive=(session_type != "easy"),
                           treadmill_phases=treadmill_phases)

    master.setdefault("sessions", []).append(record)
    master["sessions"].sort(key=lambda s: s.get("activity_id", 0), reverse=True)
    _save_master(master)

    q_count = len([l for l in record["laps"] if l.get("role") == "quality"])
    print(f"\n✅ Logged → sessions_master.json")
    print(f"   Type: {session_type} | {dist_km}km | quality laps: {q_count}")


if __name__ == "__main__":
    main()
