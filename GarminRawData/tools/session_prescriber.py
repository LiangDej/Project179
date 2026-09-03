#!/usr/bin/env python3
"""
session_prescriber.py — Weekly Training Plan Generator

สร้างแผนซ้อมทั้งสัปดาห์ทีเดียว (จันทร์–อาทิตย์) โดยรวม:
  - Training Phase ปัจจุบัน (จาก config.py)
  - BB + HRV วันนี้ (จาก health cache)
  - Taper status (ถ้าใกล้แข่ง)
  - Hard constraints (ห้ามศุกร์, ห้าม quality ติดกัน)
  - Pain level (optional)

Output: 7-day plan แบบ structured — ใช้ LLM ครั้งเดียว ลด token usage

Usage:
    python3 session_prescriber.py                    # สร้างแผนสัปดาห์หน้า (จันทร์ถัดไป)
    python3 session_prescriber.py --week current     # สัปดาห์ปัจจุบัน
    python3 session_prescriber.py --bb 70 --hrv balanced
    python3 session_prescriber.py --pain mild
    python3 session_prescriber.py --json             # output JSON สำหรับ LLM
"""

import sys
import json
import argparse
from datetime import date, timedelta
from pathlib import Path

BASE_DIR  = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
COACH_MCP = BASE_DIR.parent / "skills" / "garmin_coach_mcp"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from config import (ATHLETE, VDOT_PACES, PHASE_PRESCRIPTIONS,  # noqa: E402
                    HR_ZONE_BOUNDS, get_current_phase)
try:
    from training_planner import get_week_info  # noqa: E402
except ImportError:
    get_week_info = None  # graceful fallback if training_planner unavailable

# ---------------------------------------------------------------------------
# Race Registry (sync with taper_monitor.py)
# ---------------------------------------------------------------------------
# SINGLE SOURCE: races.json via race_registry (active races only).
try:
    from race_registry import list_races
    RACE_DATES = [{"name": r["name"], "date": date.fromisoformat(r["date"])}
                  for _, r in list_races(active_only=True)]
except Exception:
    RACE_DATES = [{"name": "🏁 Bangsaen42 Chonburi Marathon", "date": date(2026, 11, 15)}]

# Hard constraints
WEEKLY_STRUCTURE = {
    0: {"day": "จันทร์",    "type": "strength",     "fixed": True},
    1: {"day": "อังคาร",   "type": "quality1",     "fixed": False},
    2: {"day": "พุธ",      "type": "easy",          "fixed": False},
    3: {"day": "พฤหัสบดี", "type": "quality2",     "fixed": False},
    4: {"day": "ศุกร์",    "type": "rest",          "fixed": True},   # HARD — ห้ามซ้อม
    5: {"day": "เสาร์",    "type": "easy+strides",  "fixed": False},
    6: {"day": "อาทิตย์",  "type": "long",          "fixed": False},
}

# Nutrition protocols — loaded from athlete.json (single source of truth)
try:
    import json as _json_sp
    _aj_sp = _json_sp.loads((BASE_DIR / "athlete.json").read_text(encoding="utf-8"))
    _q  = _aj_sp.get("quality_nutrition", {})
    _l  = _aj_sp.get("long_run_nutrition", {}).get("outdoor", {})
    _rd = _aj_sp.get("race_day_nutrition", {})
    _q_pre60  = _q.get("pre_60min",   "Palatinose 20g")
    _q_pre15  = _q.get("pre_15min",   "Prevo 1 cap")
    _l_pre60  = _l.get("pre_60min",   "Palatinose 30g")
    _l_pre15  = _l.get("pre_15min",   "Prevo 1 cap")
    _rd_pre150 = _rd.get("pre_150min", "Palatinose 25g")
    _rd_pre30  = _rd.get("pre_30min",  "Gel + Prevo 2 แคป")
    _rd_in     = _rd.get("in_race",    "ตาม km plan")
except Exception:
    _q_pre60, _q_pre15   = "Palatinose 20g", "Prevo 1 cap"
    _l_pre60, _l_pre15   = "Palatinose 30g", "Prevo 1 cap"
    _rd_pre150, _rd_pre30, _rd_in = "Palatinose 25g", "Gel + Prevo 2 แคป", "ตาม km plan"

NUTRITION = {
    "quality1":     f"☕ กาแฟดำ + {_q_pre60} (T-60) → 💊 {_q_pre15} + น้ำ 150ml (T-15)",
    "quality2":     f"☕ กาแฟดำ + {_q_pre60} (T-60) → 💊 {_q_pre15} + น้ำ 150ml (T-15)",
    "easy":         "☕ กาแฟดำ เท่านั้น",
    "easy+strides": "☕ กาแฟดำ เท่านั้น",
    "long":         f"☕ กาแฟดำ + {_l_pre60} (T-60) → 💊 {_l_pre15} + น้ำ 150ml (T-15)",
    "strength":     "โปรตีน 20-30g หลังซ้อม",
    "rest":         "น้ำเปล่า 2.5L ตลอดวัน",
}
_RACE_DAY_NUT = f"{_rd_pre150} (T-2.5hr) → {_rd_pre30} (T-30min) → {_rd_in}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fmt_pace(sec_km: int) -> str:
    m, s = divmod(sec_km, 60)
    return f"{m}:{s:02d}/km"


def _progressive_long_km(phase_info: dict, monday: date) -> int:
    """
    Linear interpolation: long_km_start → long_km (peak) across the phase.
    Deload weeks (every 4th week within a phase) get ~75% of that week's value.
    Taper phase counts DOWN (start=high → peak=low) to reflect volume reduction.
    Returns nearest even integer (clean distances: 16, 18, 20…).
    """
    if not phase_info:
        return PHASE_PRESCRIPTIONS.get("quality", {}).get("long_km", 20)

    prescription = PHASE_PRESCRIPTIONS.get(phase_info["phase"], {})
    long_start   = prescription.get("long_km_start")
    long_peak    = prescription.get("long_km", 16)

    # No progression defined → use fixed peak
    if long_start is None or long_start == long_peak:
        return long_peak

    phase_start  = phase_info["start"]
    phase_end    = phase_info["end"]
    total_weeks  = max(1, round((phase_end - phase_start).days / 7))
    week_num     = max(0, (monday - phase_start).days // 7)  # 0-indexed

    # Linear interpolation t: 0.0 (week 1) → 1.0 (last week) — clamped to [0,1]
    t = min(1.0, week_num / max(1, total_weeks - 1))
    raw = long_start + (long_peak - long_start) * t

    # Deload every 4th week within the phase (week_num 3, 7, 11…) → 75%
    # Skip deload logic for taper phase — volume reduction is already built-in
    is_deload = (week_num + 1) % 4 == 0 and phase_info["phase"] != "taper"
    if is_deload:
        raw *= 0.75

    # Round to nearest 2km for clean prescription
    return max(8, round(raw / 2) * 2)


def _days_to_nearest_race(from_date: date) -> tuple[str | None, int | None]:
    future = [(r["name"], (r["date"] - from_date).days)
              for r in RACE_DATES if r["date"] >= from_date]
    if not future:
        return None, None
    name, days = min(future, key=lambda x: x[1])
    return name, days


def _get_health_from_cache() -> dict:
    try:
        from garmin_client import _load_health_cache
        today_str = date.today().strftime("%Y-%m-%d")
        cached = _load_health_cache(today_str)
        if cached:
            return cached
    except Exception:
        pass
    return {}


def _prescribe_day(weekday: int, day_type: str, phase_key: str,
                   bb: int | None, hrv: str, pain: str,
                   taper_days: int | None, taper_name: str | None,
                   target_date: date,
                   long_km_override: int | None = None,
                   easy_km_override: int | None = None) -> dict:
    """สร้าง prescription สำหรับ 1 วัน"""

    prescription = PHASE_PRESCRIPTIONS.get(phase_key, PHASE_PRESCRIPTIONS["quality"])
    ep_lo = _fmt_pace(VDOT_PACES["E"][0])
    ep_hi = _fmt_pace(VDOT_PACES["E"][1])
    e_ceil = HR_ZONE_BOUNDS["Z1_E"][1]   # Easy HR ceiling — single source (config, LTHR-anchored)

    # --- Overrides based on readiness ---
    decision = "GO"
    override_type = None

    if day_type == "rest":
        decision = "REST"
    elif day_type == "strength":
        if bb is not None and bb < 30:
            decision = "MODIFY"
            override_type = "mobility_only"
    elif day_type in ("quality1", "quality2"):
        # Taper: no quality ≤10 days
        if taper_days is not None and taper_days <= 10:
            decision = "MODIFY"
            override_type = "easy"
        elif hrv.lower() == "unbalanced":
            decision = "MODIFY"
            override_type = "easy"
        elif pain in ("mild", "moderate"):
            decision = "MODIFY"
            override_type = "easy"
        elif bb is not None and bb < 40:
            decision = "MODIFY"
            override_type = "easy"
    elif day_type == "long":
        if pain in ("mild", "moderate"):
            decision = "MODIFY"
            override_type = "easy"

    # --- Build session detail ---
    session = {
        "date":    target_date.isoformat(),
        "weekday": WEEKLY_STRUCTURE[weekday]["day"],
        "type":    day_type,
        "decision": decision,
    }

    if decision == "REST":
        session["label"]      = "🛌 REST DAY"
        session["workout"]    = "พักผ่อน 100% ห้ามซ้อมเด็ดขาด"
        session["nutrition"]  = NUTRITION["rest"]

    elif day_type == "strength":
        if override_type == "mobility_only":
            session["label"]   = "🟡 Mobility Only (BB ต่ำ)"
            session["workout"] = "Foam Roll 10min + Hip Mobility + Stretching"
        else:
            session["label"]   = "🏋️ Strength & Conditioning"
            session["workout"] = "Squat 3×10 | RDL 3×10 | TKE 3×15 | Hip Thrust 3×12"
            session["note"]    = "เน้น Form ป้องกัน ACL — ไม่ใช่ Heavy Weight"
        session["nutrition"]   = NUTRITION["strength"]

    elif day_type in ("quality1", "quality2") and override_type == "easy":
        reason = ""
        if taper_days is not None and taper_days <= 10:
            reason = f"(Taper T-{taper_days})"
        elif hrv.lower() == "unbalanced":
            reason = "(HRV Unbalanced)"
        elif pain in ("mild", "moderate"):
            reason = f"(Pain: {pain})"
        elif bb is not None and bb < 40:
            reason = f"(BB ต่ำ: {bb})"

        session["label"]      = f"🟡 Easy Run แทน Quality {reason}"
        session["workout"]    = f"Easy 6km | Pace {ep_lo}–{ep_hi} | HR < {e_ceil}"
        session["nutrition"]  = NUTRITION["easy"]

    elif day_type in ("quality1", "quality2"):
        p = prescription.get(day_type, prescription.get("quality1", {}))
        session["label"]      = f"🔥 Quality Run — {p.get('type', 'T')}"
        session["workout"]    = p.get("workout", "")
        session["note"]       = p.get("note", "")
        session["nutrition"]  = NUTRITION[day_type]

    elif day_type == "easy":
        if taper_days is not None and taper_days <= 14:
            dist = 6
        else:
            dist = easy_km_override if easy_km_override else 8
        session["label"]      = "🟢 Easy Run"
        session["workout"]    = f"Easy {dist}km | Pace {ep_lo}–{ep_hi} | HR < {e_ceil}"
        session["nutrition"]  = NUTRITION["easy"]

    elif day_type == "easy+strides":
        if taper_days is not None and taper_days <= 14:
            dist = 6
        else:
            dist = easy_km_override if easy_km_override else 8
        session["label"]      = "🟢 Easy + Strides"
        session["workout"]    = f"{dist}km @ {ep_lo}–{ep_hi} Easy + 6×Strides (เร่ง 20วิ R-effort)"
        session["note"]       = "Strides: ค่อยๆ เร่ง 15วิ แล้วผ่อน — ไม่ใช่ sprint"
        session["nutrition"]  = NUTRITION["easy+strides"]

    elif day_type == "long":
        if override_type == "easy":
            session["label"]   = f"🟡 Easy Run แทน Long Run (Pain: {pain})"
            session["workout"] = f"Easy 8km | Pace {ep_lo}–{ep_hi} | HR < {e_ceil}"
            session["nutrition"] = NUTRITION["easy"]
        else:
            p         = prescription
            long_note = p.get("long_note", f"E-Pace | HR < {e_ceil}")
            # Progressive long run — use override computed per-week, fall back to config peak
            long_km = long_km_override if long_km_override else p.get("long_km", 16)
            # Taper: hard cap within 14 days of race
            if taper_days is not None and taper_days <= 14:
                long_km = min(long_km, 12)
            session["label"]      = "📏 Long Run"
            session["workout"]    = f"{long_km}km — {long_note}"
            session["nutrition"]  = NUTRITION["long"]

    # Taper note
    if taper_days is not None and taper_days <= 14 and day_type not in ("rest", "strength"):
        session["taper_note"] = (
            f"⚡ TAPER T-{taper_days} ({'Race Week' if taper_days <= 7 else 'Early Taper'}) — "
            f"ลด volume {'50%' if taper_days <= 7 else '30%'} ของ peak week"
        )

    return session


# ---------------------------------------------------------------------------
# Main planner
# ---------------------------------------------------------------------------
def generate_week_plan(week: str, bb: int | None, hrv: str,
                       pain: str, verbose: bool,
                       target_monday: date | None = None) -> dict:
    today = date.today()

    if target_monday is not None:
        monday = target_monday  # explicit override — used by multi-week views
    elif week == "current":
        monday = today - timedelta(days=today.weekday())
    else:  # "next" (default)
        days_ahead = 7 - today.weekday()
        monday = today + timedelta(days=days_ahead)

    sunday = monday + timedelta(days=6)
    phase_info = get_current_phase(monday)
    phase_key  = phase_info["phase"] if phase_info else "quality"

    taper_name, taper_days = _days_to_nearest_race(monday)
    # Only show taper if within 14 days of a race
    if taper_days is not None and taper_days > 14:
        taper_name_display = None
        taper_days_display = None
    else:
        taper_name_display = taper_name
        taper_days_display = taper_days

    # ── Link to 30-week long-term plan ────────────────────────────────────────
    week_info = get_week_info(monday) if get_week_info else {}
    km_target        = week_info.get("km_target", 0)
    is_deload        = week_info.get("is_deload", False)
    week_num         = week_info.get("week_num", "?")
    total_weeks      = week_info.get("total_weeks", 30)
    days_to_race     = week_info.get("days_to_race", None)
    easy_km_for_week = week_info.get("easy_km", 8)

    # Progressive long run — computed from config phase progression
    long_km_this_week = _progressive_long_km(phase_info, monday)

    sessions = []
    for wd in range(7):
        target_date = monday + timedelta(days=wd)
        day_type    = WEEKLY_STRUCTURE[wd]["type"]

        # Race day override — แสดง RACE DAY แทน workout ปกติ
        race_today = next((r for r in RACE_DATES if r["date"] == target_date), None)
        if race_today:
            sessions.append({
                "date":      target_date.isoformat(),
                "weekday":   WEEKLY_STRUCTURE[wd]["day"],
                "type":      "race",
                "decision":  "RACE",
                "label":     f"🏁 RACE DAY — {race_today['name']}",
                "workout":   "ออกตาม race plan — ไม่มีซ้อม",
                "note":      "Warm-up 10 นาที + strides 2–3 ครั้ง ก่อน gun",
                "nutrition": _RACE_DAY_NUT,
            })
            continue

        session = _prescribe_day(
            wd, day_type, phase_key,
            bb, hrv, pain,
            taper_days_display, taper_name_display,
            target_date,
            long_km_override=long_km_this_week,
            easy_km_override=easy_km_for_week,
        )
        sessions.append(session)

    return {
        "generated_for": f"{monday.isoformat()} – {sunday.isoformat()}",
        "week":          week,
        "phase":         phase_info["name"] if phase_info else "Unknown",
        "phase_key":     phase_key,
        "focus":         PHASE_PRESCRIPTIONS.get(phase_key, {}).get("focus", ""),
        "readiness": {
            "bb":    bb,
            "hrv":   hrv,
            "pain":  pain,
        },
        "taper": {
            "active":      taper_days_display is not None,
            "race_name":   taper_name_display,
            "days_to_race": taper_days_display,
        },
        # ── Long-term plan context (linked from training_planner) ──
        "season": {
            "week_num":     week_num,
            "total_weeks":  total_weeks,
            "km_target":    km_target,
            "is_deload":    is_deload,
            "days_to_race": days_to_race,
            "long_run_km":  long_km_this_week,
        },
        "sessions": sessions,
    }


def print_plan(plan: dict):
    print(f"\n{'='*57}")
    print(f"📅 WEEKLY PLAN — {plan['generated_for']}")
    print(f"   Phase: {plan['phase']}")
    if plan['focus']:
        print(f"   Focus: {plan['focus']}")

    # ── Season context (linked from training_planner) ──────────────────────
    s = plan.get("season", {})
    if s.get("week_num"):
        deload_tag = "  🔄 DELOAD" if s.get("is_deload") else ""
        fuji_tag   = f"  |  🇹🇭 T-{s['days_to_race']}d Thai Race" if s.get("days_to_race") else ""
        print(f"   Season: Week {s['week_num']}/{s['total_weeks']} "
              f"| Target {s.get('km_target','?')}km "
              f"| Long {s.get('long_run_km','?')}km"
              f"{deload_tag}{fuji_tag}")

    if plan['taper']['active']:
        print(f"   ⚡ TAPER: T-{plan['taper']['days_to_race']} ก่อน {plan['taper']['race_name']}")
    r = plan['readiness']
    bb_str = str(r['bb']) if r['bb'] is not None else "N/A"
    print(f"   Readiness: BB={bb_str} | HRV={r['hrv']} | Pain={r['pain']}")
    print(f"{'='*57}")

    for s in plan['sessions']:
        print(f"\n📌 {s['date']} ({s['weekday']})")
        print(f"   {s.get('label', s['type'])}")
        if s.get('workout'):
            print(f"   🏃 {s['workout']}")
        if s.get('note'):
            print(f"   📝 {s['note']}")
        if s.get('taper_note'):
            print(f"   {s['taper_note']}")
        if s.get('nutrition'):
            print(f"   💊 {s['nutrition']}")

    pain = plan['readiness'].get('pain', 'none')
    if pain and pain != "none":
        print(f"\n{'='*57}")
        print(f"⚕️  Injury Check ทุกวัน: pain_status = {pain} (athlete.json) — เช็คจุดที่เจ็บก่อนวิ่งทุกครั้ง")
        print(f"{'='*57}\n")


def main():
    parser = argparse.ArgumentParser(description="Weekly Training Plan Generator")
    parser.add_argument("--week",  choices=["current", "next"], default="next",
                        help="สัปดาห์ที่ต้องการวางแผน (default: next)")
    parser.add_argument("--bb",    type=int, help="Body Battery วันนี้")
    parser.add_argument("--hrv",   default="unknown",
                        choices=["balanced", "unbalanced", "unknown"],
                        help="HRV status วันนี้")
    parser.add_argument("--pain",  default=ATHLETE.get("pain_status", "none"),
                        choices=["none", "mild", "moderate"],
                        help="Pain level — defaults to athlete.json → pain_status")
    parser.add_argument("--json",  action="store_true", help="Output JSON")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Auto-load BB/HRV from cache if not provided
    bb  = args.bb
    hrv = args.hrv
    if bb is None or hrv == "unknown":
        cached = _get_health_from_cache()
        if bb is None:
            # Morning peak (bb_high), not the current/drifted body_battery —
            # readiness decisions must use the day's peak, same fix as
            # daily_brief.py. Otherwise running this tool in the evening
            # (BB already drained from the day) wrongly reads as low-BB and
            # downgrades tomorrow's plan off a stale, already-spent number.
            bb = cached.get("bb_high") if cached.get("bb_high") is not None else cached.get("body_battery")
        if hrv == "unknown":
            hrv = cached.get("hrv_status", "unknown").lower()

    plan = generate_week_plan(args.week, bb, hrv, args.pain, args.verbose)

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print_plan(plan)


if __name__ == "__main__":
    main()
