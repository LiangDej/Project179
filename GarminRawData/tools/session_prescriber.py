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

from __future__ import annotations

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

from config import (ATHLETE, VDOT_PACES, HR_ZONE_BOUNDS,  # noqa: E402
                    DAY_NUTRITION, render_prescription)
from jd_rules import render_quality, render_long, fmt_pace  # noqa: E402
from readiness import (TAPER_PROFILES, taper_profile,  # noqa: E402,F401
                       active_races, active_taper, readiness_assess, post_race_recovery)
from training_planner import week_plan  # noqa: E402

# Races: races.json via race_registry — no fallback race (v1.8: the old
# fallback pointed at Bangsaen42 15 Nov 2026 and would taper a dead race).
RACE_DATES = active_races()
ALL_RACES = active_races(active_only=False)   # incl. finished ones (post-race recovery)

WEEKDAY_TH = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
DEFAULT_STRIDES = 6
MIN_EASY_KM = 3
_ROUTINES = {"strength": ATHLETE.get("strength_routine") or "Strength ตามโปรแกรม",
             "mobility": ATHLETE.get("mobility_routine") or "Mobility + Stretching"}


def _active_taper(on_date: date):
    """(name, days, profile) of the race whose tier window covers on_date."""
    return active_taper(on_date, RACE_DATES)


def _get_health_from_cache() -> dict:
    try:
        from garmin_client import _load_health_cache
        cached = _load_health_cache(date.today().strftime("%Y-%m-%d"))
        if cached:
            return cached
    except Exception:
        pass
    return {}


def _easy_text(km: float, e_ceil: int) -> str:
    e = VDOT_PACES["E"]
    return f"Easy {km:g}km | Pace {fmt_pace(e[0])}–{fmt_pace(e[1])}/km | HR < {e_ceil}"


def prescribe_day(target_date: date, wi: dict | None = None, bb=None, hrv: str = "unknown",
                  pain: str = "none", rhr=None, rhr_baseline=None, live: bool = False) -> dict:
    """The ONE day prescription (weekly plan AND daily brief use it).

    wi    : week_plan() of the week (phase, volume, JD-capped long/quality).
    live  : True only for TODAY — today's BB/HRV/RHR then vote; future days
            are planned from pain + taper rules alone.
    """
    monday = target_date - timedelta(days=target_date.weekday())
    wi = wi or week_plan(monday)
    wd = target_date.weekday()
    day_type = wi["structure"][wd]
    e_ceil = HR_ZONE_BOUNDS["Z1_E"][1]
    base = {"date": target_date.isoformat(), "weekday": WEEKDAY_TH[wd], "type": day_type}

    race_today = next((r for r in RACE_DATES if r["date"] == target_date), None)
    if race_today:
        return {**base, "type": "race", "decision": "RACE", "reason": "",
                "label": f"🏁 RACE DAY — {race_today['name']}",
                "workout": "ออกตาม race plan — ไม่มีซ้อม",
                "note": "Warm-up 10 นาที + strides 2–3 ครั้ง ก่อน gun",
                "nutrition": DAY_NUTRITION["race"], "others": []}

    t_name, t_days, tp = _active_taper(target_date)
    in_taper = t_days is not None
    no_q = in_taper and t_days <= tp["no_quality"]
    ra = readiness_assess(bb, hrv, day_type, pain, t_days, rhr, rhr_baseline,
                          no_quality=no_q, planning=not live,
                          recovery=post_race_recovery(target_date, ALL_RACES))
    s = {**base, "decision": ra["decision"], "reason": ra["reason"], "others": ra["others"]}
    presc = wi["prescription"]
    weekly = wi["km_target"]
    cut = 0.0
    # Taper cut only when the race window sits inside a TRAINING phase; a
    # taper phase's weekly_km in training_plan.json is already the cut volume.
    if in_taper and wi["phase"] != "taper":
        cut = float((tp["vol_race"] if t_days <= tp["race_week"] else tp["vol_early"]).rstrip("%")) / 100
    easy_km = max(MIN_EASY_KM, round(wi["easy_km"] * (1 - cut)))

    if ra["decision"] == "REST":
        s.update(label="🛌 REST" if day_type == "rest" else "🔴 REST (readiness)",
                 workout="พักผ่อน 100% ห้ามซ้อมเด็ดขาด", nutrition=DAY_NUTRITION["rest"])
    elif day_type == "strength":
        if ra["decision"] == "MODIFY":
            s.update(label="🟡 Mobility Only (BB ต่ำ)", workout=_ROUTINES["mobility"])
        else:
            s.update(label="🏋️ Strength & Conditioning", workout=_ROUTINES["strength"],
                     note=(f"เน้น Form ป้องกันบาดเจ็บ (pain_status: {pain}) — ไม่ใช่ Heavy Weight"
                           if pain != "none" else "เน้น Form ที่ถูกต้อง — ไม่ใช่ Heavy Weight"))
        s["nutrition"] = DAY_NUTRITION["strength"]
    elif ra["decision"] == "MODIFY":
        hi = min(ra["cap_km"] or easy_km, easy_km if day_type != "long" else max(easy_km, 8))
        km = max(MIN_EASY_KM, hi)
        src = {"quality1": "Quality", "quality2": "Quality", "long": "Long Run"}.get(day_type, "Easy")
        s.update(label=f"🟡 Easy Run แทน {src}", workout=_easy_text(km, e_ceil),
                 spec={"type": "easy", "km": km}, downgraded=True, nutrition=DAY_NUTRITION["easy"])
    elif day_type in ("quality1", "quality2"):
        spec = (presc.get(day_type) or {}).get("spec")
        if in_taper:
            # Taper keeps intensity (Bosquet 2007): Q1 = short maintenance T, Q2 = easy.
            tps = render_prescription("taper", weekly_km=weekly)
            spec = (tps.get("quality1") or {}).get("spec") if day_type == "quality1" else {"type": "easy", "km": easy_km}
        if not spec:
            spec = {"type": "easy", "km": easy_km}
        r = render_quality(spec, weekly, VDOT_PACES, HR_ZONE_BOUNDS)
        final = dict(spec)
        if r["capped"] and spec.get("reps") and spec.get("type") != "combo":
            final["reps"] = int(r["workout"].split("×")[0])
        s.update(label=("🔥 Quality — " + r["type"]) if r["type"] not in ("easy",) else "🟢 Easy Run",
                 workout=r["workout"], note=r.get("note", ""), spec=final,
                 jd_notes=r["capped"], quality_km=r["quality_km"],
                 nutrition=DAY_NUTRITION[day_type if r["type"] != "easy" else "easy"])
    elif day_type == "easy":
        s.update(label="🟢 Easy Run", workout=_easy_text(easy_km, e_ceil),
                 spec={"type": "easy", "km": easy_km}, nutrition=DAY_NUTRITION["easy"])
    elif day_type == "easy+strides":
        r = render_quality({"type": "E+strides", "km": easy_km, "strides": DEFAULT_STRIDES},
                           weekly, VDOT_PACES, HR_ZONE_BOUNDS)
        s.update(label="🟢 Easy + Strides", workout=r["workout"],
                 note="Strides: ค่อยๆ เร่ง 15วิ แล้วผ่อน — ไม่ใช่ sprint",
                 spec={"type": "E+strides", "km": easy_km, "strides": DEFAULT_STRIDES},
                 nutrition=DAY_NUTRITION["easy+strides"])
    elif day_type == "long":
        km = wi["long_km"]
        if in_taper:
            km = min(km, tp["long_cap"])
        s.update(label="📏 Long Run", workout=render_long(presc.get("long_spec", {}), km, VDOT_PACES, HR_ZONE_BOUNDS),
                 spec={"type": "long", "km": km,
                       "m_finish_km": (presc.get("long_spec") or {}).get("m_finish_km", 0)},
                 jd_notes=[wi["long_capped"]] if wi.get("long_capped") and not in_taper else [],
                 nutrition=DAY_NUTRITION["long"])

    if in_taper and day_type not in ("rest", "strength"):
        race_wk = t_days <= tp["race_week"]
        s["taper_note"] = (f"⚡ TAPER T-{t_days} ({'Race Week' if race_wk else 'Early Taper'}) — "
                           f"ลด volume {tp['vol_race'] if race_wk else tp['vol_early']} ของ peak week, คงความหนัก")
    return s


# ---------------------------------------------------------------------------
# Main planner
# ---------------------------------------------------------------------------
def generate_week_plan(week: str, bb: int | None, hrv: str,
                       pain: str, verbose: bool = False,
                       target_monday: date | None = None,
                       rhr=None, rhr_baseline=None) -> dict:
    today = date.today()
    if target_monday is not None:
        monday = target_monday
    elif week == "current":
        monday = today - timedelta(days=today.weekday())
    else:
        monday = today + timedelta(days=7 - today.weekday())
    sunday = monday + timedelta(days=6)

    wi = week_plan(monday)
    phase_info = wi["phase_info"]

    taper_name_display = taper_days_display = None
    for _d in range(7):
        _n, _days, _p = _active_taper(monday + timedelta(days=_d))
        if _n:
            taper_name_display, taper_days_display = _n, _days + _d
            break

    sessions = [prescribe_day(monday + timedelta(days=d), wi, bb, hrv, pain, rhr, rhr_baseline,
                              live=(monday + timedelta(days=d) == today)) for d in range(7)]

    return {
        "generated_for": f"{monday.isoformat()} – {sunday.isoformat()}",
        "week":          week,
        "phase":         phase_info["name"] if phase_info else wi["phase"],
        "phase_key":     wi["phase"],
        "focus":         wi["prescription"].get("focus", ""),
        "readiness":     {"bb": bb, "hrv": hrv, "pain": pain},
        "taper": {
            "active":       taper_days_display is not None,
            "race_name":    taper_name_display,
            "days_to_race": taper_days_display,
        },
        "season": {
            "week_num":     wi["week_num"],
            "total_weeks":  wi["total_weeks"],
            "km_target":    wi["km_target"],
            "is_deload":    wi["is_deload"],
            "days_to_race": wi["days_to_race"],
            "race_name":    wi.get("race_name"),
            "long_run_km":  wi["long_km"],
            "long_capped":  wi.get("long_capped"),
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
        race_tag   = f"  |  🏁 T-{s['days_to_race']}d {s.get('race_name','A-race')}" if s.get("days_to_race") else ""
        print(f"   Season: Week {s['week_num']}/{s['total_weeks']} "
              f"| Target {s.get('km_target','?')}km "
              f"| Long {s.get('long_run_km','?')}km{' (JD cap)' if s.get('long_capped') else ''}"
              f"{deload_tag}{race_tag}")

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
        if s.get('decision') == "MODIFY" and s.get('reason'):
            print(f"   ↳ {s['reason']}")
        for n in s.get('jd_notes') or []:
            print(f"   ✂️  {n}")
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
                        choices=["balanced", "unbalanced", "high", "unknown"],
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
            # v1.7.2: last night's HRV vs range, not the lagging 7-day status
            from hrv_trend import readiness_hrv
            hrv = readiness_hrv(cached)

    rhr = rhr_b = None
    try:
        from hrv_trend import rhr_baseline as _rhr_baseline
        rhr = _get_health_from_cache().get("resting_hr")
        rhr_b = _rhr_baseline() or ATHLETE.get("rhr")
    except Exception:
        pass
    plan = generate_week_plan(args.week, bb, hrv, args.pain, args.verbose, rhr=rhr, rhr_baseline=rhr_b)

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print_plan(plan)


if __name__ == "__main__":
    main()
