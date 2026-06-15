#!/usr/bin/env python3
"""
daily_brief.py — JD Analytics Toolkit (P1)
ตรวจสอบความพร้อมประจำวัน ตัดสินใจ Go/Modify/Rest และสร้าง Action Plan

Usage:
    python3 daily_brief.py                      # live mode (ดึง Garmin)
    python3 daily_brief.py --offline            # ใช้ cache เท่านั้น (ไม่ต่อ Garmin)
    python3 daily_brief.py --pain mild
    python3 daily_brief.py --type I             # override quality type
"""

import sys
from pathlib import Path
from datetime import date, timedelta

# --- Paths ---
BASE_DIR  = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
COACH_MCP = BASE_DIR.parent / "skills" / "garmin_coach_mcp"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from config import (ATHLETE, VDOT_PACES, HR_ZONE_BOUNDS,  # noqa: E402
                    PHASE_PRESCRIPTIONS, get_current_phase)
from garmin_client import get_client, get_health_cached    # noqa: E402

# SINGLE SOURCE: races.json via race_registry (active races only).
try:
    from race_registry import list_races
    RACE_DATES = [{"name": r["name"], "date": date.fromisoformat(r["date"])}
                  for _, r in list_races(active_only=True)]
except Exception:
    RACE_DATES = [{"name": "🏁 ATM Bangkok Marathon", "date": date(2026, 11, 29)}]

WEEKLY_PLAN = {
    0: {"day": "จันทร์",   "type": "strength",     "label": "🏋️ Strength & Conditioning",   "note": "งดวิ่ง เสริมกล้ามเนื้อ Functional"},
    1: {"day": "อังคาร",  "type": "quality1",     "label": "🔥 Quality Run #1 หรือ Easy",  "note": "ประเมินจาก BB/HRV ก่อน", "default_quality": "T"},
    2: {"day": "พุธ",     "type": "easy",         "label": "🟢 Easy Run",                   "note": f"ฟื้นฟู HR < {HR_ZONE_BOUNDS['Z1_E'][1]}"},
    3: {"day": "พฤหัสบดี","type": "quality2",     "label": "🔥 Quality Run #2 หรือ Easy",  "note": "ประเมินจาก BB/HRV ก่อน", "default_quality": "I"},
    4: {"day": "ศุกร์",   "type": "rest",         "label": "🛌 REST DAY",                   "note": "พักผ่อน 100% ห้ามซ้อม"},
    5: {"day": "เสาร์",   "type": "easy+strides", "label": "🟢 Easy + Strides",            "note": f"8km E-Pace (HR < {HR_ZONE_BOUNDS['Z1_E'][1]}) + 6×Strides"},
    6: {"day": "อาทิตย์", "type": "long",         "label": "📏 Long Run",                   "note": "เน้น E-Pace หรือผสม M-Pace"},
}

NUTRITION = {
    "quality1":     "☕ กาแฟดำ + Palatinose 20g (T-60) → 💊 iRun 1 เม็ด + น้ำ 150ml (T-15)",
    "quality2":     "☕ กาแฟดำ + Palatinose 20g (T-60) → 💊 iRun 1 เม็ด + น้ำ 150ml (T-15)",
    "easy":         "☕ กาแฟดำ เท่านั้น (ไม่ต้องเติม Palatinose)",
    "easy+strides": "☕ กาแฟดำ เท่านั้น (ไม่ต้องเติม Palatinose)",
    "long":         "☕ กาแฟดำ + Palatinose 20g (T-60) → 💊 iRun 1 เม็ด + น้ำ 150ml (T-15)",
    "strength":     "โปรตีน 20-30g หลังซ้อม + น้ำ 300ml",
    "rest":         "น้ำเปล่า 2.5L ตลอดวัน ไม่ต้องทำอะไรพิเศษ",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def format_pace(sec_km):
    m = int(sec_km // 60)
    s = int(sec_km % 60)
    return f"{m}:{s:02d}/km"


def get_race_countdown():
    today = date.today()
    return [
        f"   {r['name']}: {(r['date'] - today).days} วัน"
        for r in RACE_DATES if (r["date"] - today).days >= 0
    ]


def get_taper_phase():
    today = date.today()
    for r in RACE_DATES:
        delta = (r["date"] - today).days
        if 0 <= delta <= 10:
            return r["name"], delta
    return None, None


def readiness_decision(bb, hrv_status, day_type, pain="none", taper_days=None):
    if day_type == "rest":
        return "REST", "🛌 วันพักตามตาราง ห้ามซ้อมเด็ดขาด"
    if day_type == "strength":
        if bb is not None and bb < 30:
            return "MODIFY", "🟡 BB ต่ำมาก — ลด Weight Training เหลือ Mobility เท่านั้น"
        return "GO", "✅ ทำ Strength ตามแผน"

    if bb is None:
        return "CAUTION", "⚠️ ไม่พบข้อมูล Body Battery — ใช้ความรู้สึกตัวเองตัดสิน"

    hrv_unbalanced = hrv_status.lower() == "unbalanced"

    if taper_days is not None and taper_days <= 10 and day_type in ("quality1", "quality2"):
        return "MODIFY", "🟡 ใกล้วันแข่ง (≤10 วัน) — ยกเลิก Quality Session ปรับเป็น Easy อัตโนมัติ"

    if pain in ("mild", "moderate") and day_type in ("quality1", "quality2", "long"):
        return "MODIFY", f"🟡 มีอาการบาดเจ็บ ({pain}) — ลดระดับเป็น Easy Run เท่านั้น"

    if hrv_unbalanced:
        if bb < 30:
            return "REST", "🔴 HRV Unbalanced + BB ต่ำมาก — ร่างกายล้าสะสม พักผ่อน 100%"
        return "MODIFY", "🟡 HRV Unbalanced — ระบบประสาทไม่พร้อม ปรับลดความหนักลง"

    hrv_ok = hrv_status.lower() == "balanced"
    if bb >= 60 and hrv_ok:
        if day_type in ("quality1", "quality2"):
            return "GO", "✅ พร้อมเต็มที่ — วิ่ง Quality ตามแผน"
        return "GO", "✅ พร้อม — วิ่งตามแผน"
    elif bb >= 40:
        if day_type in ("quality1", "quality2"):
            return "MODIFY", "🟡 BB ปานกลาง — ลด Quality → Easy Run แทน"
        return "GO", "✅ วิ่ง Easy ตามปกติได้"
    elif bb >= 20:
        return "MODIFY", "🟡 BB ต่ำ — Easy 6km HR < 140 เท่านั้น"
    else:
        return "REST", "🔴 BB ต่ำมาก — พักผ่อน อย่าฝืน"


def build_action_plan(day_info, decision, day_type, taper_race, taper_days):
    plan = []

    if decision == "REST":
        plan.append(f"   📋 {day_info['label']}")
        plan.append(f"   📌 {day_info['note']}")
        return plan

    if taper_race and day_type not in ("rest", "strength"):
        plan.append(f"   ⚡ TAPER PHASE — {taper_days} วันก่อน {taper_race}")
        plan.append(f"   📏 ลด Volume ลง {'30%' if taper_days > 7 else '50%'} ของ Peak Week")

    phase_info   = get_current_phase()
    phase_key    = phase_info["phase"] if phase_info else "quality"
    prescription = PHASE_PRESCRIPTIONS.get(phase_key, PHASE_PRESCRIPTIONS["quality"])

    if phase_info and day_type not in ("rest", "strength"):
        plan.append(f"   🗓️  Phase: {phase_info['name']} — {prescription['focus']}")

    if decision == "MODIFY" and day_type in ("quality1", "quality2"):
        pace_lo = format_pace(VDOT_PACES["E"][0])
        pace_hi = format_pace(VDOT_PACES["E"][1])
        plan.append("   📋 Easy Run (ลดระดับจาก Quality เพื่อลดความเสี่ยง)")
        plan.append(f"   ⏱️  ระยะ: 6.0–8.0 km | Pace: {pace_lo}–{pace_hi} | HR: < {HR_ZONE_BOUNDS['Z1_E'][1]} bpm")
    elif day_type in ("quality1", "quality2"):
        q_override = day_info.get("quality_override")
        if q_override:
            q_type = q_override
            p = {
                "type": q_type,
                "workout": (f"5×1km @ {format_pace(VDOT_PACES['I'][0])}–{format_pace(VDOT_PACES['I'][1])} | HR {HR_ZONE_BOUNDS['Z4_I'][0]}–{HR_ZONE_BOUNDS['Z4_I'][1]}"
                            if q_type == "I" else
                            f"4–6×8min @ {format_pace(VDOT_PACES['T'][0])}–{format_pace(VDOT_PACES['T'][1])} | HR {HR_ZONE_BOUNDS['Z3_T'][0]}–{HR_ZONE_BOUNDS['Z3_T'][1]}"),
                "note": "(--type override)",
            }
        else:
            p      = prescription.get(day_type, prescription.get("quality1"))
            q_type = p.get("type", "T")

        if q_type == "easy":
            plan.append(f"   📋 Easy Run ({p.get('note','')})")
            plan.append(f"   ⏱️  6km | HR < {HR_ZONE_BOUNDS['Z1_E'][1]}")
        elif q_type in ("E+strides", "e+strides"):
            plan.append(f"   📋 {p['workout']}")
            plan.append(f"   📌 {p.get('note','')}")
        else:
            plan.append(f"   📋 Quality — {p['workout']}")
            plan.append(f"   📌 {p.get('note','')}")
    elif day_type == "easy":
        pace_lo = format_pace(VDOT_PACES["E"][0])
        pace_hi = format_pace(VDOT_PACES["E"][1])
        dist = 6 if taper_race else 8
        plan.append("   📋 Easy Run")
        plan.append(f"   ⏱️  ระยะ: {dist}.0 km | Pace: {pace_lo}–{pace_hi} | HR: < {HR_ZONE_BOUNDS['Z1_E'][1]} bpm")
    elif day_type == "easy+strides":
        dist = 6 if taper_race else 8
        plan.append("   📋 Easy Run + Strides")
        pace_lo = format_pace(VDOT_PACES["E"][0])
        pace_hi = format_pace(VDOT_PACES["E"][1])
        plan.append(f"   ⏱️  {dist}.0 km @ {pace_lo}–{pace_hi} | HR < {HR_ZONE_BOUNDS['Z1_E'][1]} + 6×Strides")
        plan.append("   📌 Strides: เร่ง 15-20วิ (R-effort, ไม่ใช่ sprint) แล้วค่อยๆ ผ่อน")
    elif day_type == "long":
        long_km   = prescription.get("long_km", 16) if not taper_race else 12
        long_note = prescription.get("long_note", f"E-Pace | HR < {HR_ZONE_BOUNDS['Z1_E'][1]}")
        plan.append("   📋 Long Run")
        plan.append(f"   ⏱️  {long_km}.0 km | {long_note}")
    elif day_type == "strength":
        plan.append("   📋 Strength & Conditioning")
        plan.append("   💪 Squat 3×10 | RDL 3×10 | TKE 3×15 | Hip Thrust 3×12")
        plan.append("   📌 เน้น Form เพื่อ protect ACL ไม่ใช่ Heavy Weight")

    return plan


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pain", choices=["none", "mild", "moderate"], default="none")
    parser.add_argument("--type", choices=["T", "I"], default=None, dest="quality_type",
                        help="Override quality session type")
    parser.add_argument("--offline", action="store_true",
                        help="ใช้ health cache เท่านั้น ไม่ต่อ Garmin (ใช้เมื่อ API ไม่พร้อม)")
    parser.add_argument("--fresh", action="store_true",
                        help="บังคับดึง health data สดจาก Garmin (bypass cache 2h TTL) — "
                             "ใช้ตอนเพิ่งตื่น/เพิ่ง sync นาฬิกา ให้ได้ HRV/BB/Sleep ล่าสุด")
    args = parser.parse_args()

    today     = date.today()
    today_str = today.strftime("%Y-%m-%d")
    weekday   = today.weekday()
    day_info  = WEEKLY_PLAN[weekday].copy()
    day_type  = day_info["type"]

    if args.quality_type and day_type in ("quality1", "quality2"):
        day_info["quality_override"] = args.quality_type

    SEP  = "─" * 55
    taper_race, taper_days = get_taper_phase()
    taper_tag = f" (T-{taper_days})" if taper_days is not None else ""

    # --- Header ---
    print(f"\n{'═'*55}")
    print(f"🌅 DAILY BRIEF — {today_str} ({day_info['day']}){taper_tag}")
    if args.offline:
        print("   📦 OFFLINE MODE")
    if args.pain != "none":
        print(f"   🌡️  Pain: {args.pain.upper()}")
    print(f"{'═'*55}")

    # --- Race Countdown ---
    countdowns = get_race_countdown()
    if countdowns:
        print("🏁 " + "  |  ".join(c.strip() for c in countdowns))

    # --- Health Data ---
    print(f"\n{SEP}")
    print(f"{'📦 cache' if args.offline else '📡 Garmin live'}...")
    try:
        if args.offline:
            from garmin_client import _load_health_cache
            health = _load_health_cache(today_str) or {
                "body_battery": None, "hrv_status": "Unknown",
                "resting_hr": None, "_source": "unavailable",
            }
            health["_source"] = health.get("_source", "cache")
        else:
            client = get_client()
            health = get_health_cached(client, today_str, force_refresh=args.fresh)
            if args.fresh:
                print("   🔄 FRESH — ดึงข้อมูลสด (bypass cache)")
    except Exception as e:
        print(f"⚠️  ดึงข้อมูลไม่ได้: {e}")
        health = {"body_battery": None, "hrv_status": "Unknown",
                  "resting_hr": None, "_source": "unavailable"}

    bb          = health.get("body_battery")
    hrv_status  = health.get("hrv_status", "Unknown")
    rhr         = health.get("resting_hr")
    sleep_score = health.get("sleep_score")
    stress_avg  = health.get("stress_avg")
    src         = health.get("_source", "?")

    def _icon(v, hi, lo, invert=False):
        if v is None: return "⚪"
        good = v >= hi if not invert else v <= hi
        warn = v >= lo if not invert else v <= lo
        return "🟢" if good else ("🟡" if warn else "🔴")

    src_tag = f" [{src}]" if src != "live" else ""
    print(f"\n📊 BODY STATUS{src_tag}")
    print(f"   🔋 Body Battery : {bb if bb is not None else 'N/A'}  {_icon(bb,75,50)}")
    print(f"   💓 HRV          : {hrv_status}")
    print(f"   ❤️  Resting HR   : {rhr} bpm  {_icon(rhr,50,58,invert=True)}" if rhr else "   ❤️  Resting HR   : N/A")
    sleep_icon = _icon(sleep_score, 80, 60)
    print(f"   😴 Sleep Score  : {sleep_score}/100  {sleep_icon}" if sleep_score else "   😴 Sleep Score  : N/A")
    if stress_avg is not None:
        stress_icon = "🟢" if stress_avg < 25 else ("🟡" if stress_avg < 50 else "🔴")
        print(f"   🧠 Stress Avg   : {stress_avg}  {stress_icon}")
    if src == "unavailable":
        print("   ⚠️  ไม่มีข้อมูล — ประเมินจากความรู้สึกตัวเอง")

    # --- Sleep Quality Breakdown (Plan A Tier 1) ---
    sleep_deep  = health.get("sleep_deep_min")
    sleep_rem   = health.get("sleep_rem_min")
    sleep_light = health.get("sleep_light_min")
    sleep_awake = health.get("sleep_awake_min")
    if sleep_deep is not None or sleep_rem is not None:
        print(f"\n💤 SLEEP QUALITY")
        total = sum(x for x in [sleep_deep, sleep_rem, sleep_light] if x) or 0
        if sleep_deep is not None:
            deep_icon = "🟢" if sleep_deep >= 75 else ("🟡" if sleep_deep >= 45 else "🔴")
            print(f"   Deep   : {sleep_deep:>5.0f} min  {deep_icon}  (target ≥75 — physical recovery)")
        if sleep_rem is not None:
            rem_icon  = "🟢" if sleep_rem >= 90 else ("🟡" if sleep_rem >= 60 else "🔴")
            print(f"   REM    : {sleep_rem:>5.0f} min  {rem_icon}  (target ≥90 — mental recovery)")
        if sleep_light is not None:
            print(f"   Light  : {sleep_light:>5.0f} min")
        if sleep_awake is not None:
            wake_icon = "🟢" if sleep_awake < 30 else ("🟡" if sleep_awake < 60 else "🔴")
            print(f"   Awake  : {sleep_awake:>5.0f} min  {wake_icon}  (fragmentation)")
        if total:
            print(f"   Total  : {total:>5.0f} min  ({total/60:.1f}h)")

    # --- Recovery Signals: Respiratory + SpO2 (Plan A Tier 1) ---
    resp_avg = health.get("respiratory_avg")
    spo2_avg = health.get("spo2_avg")
    spo2_low = health.get("spo2_low")
    if resp_avg is not None or spo2_avg is not None:
        print(f"\n🫁 RECOVERY SIGNALS")
        if resp_avg is not None:
            # Baseline 12-16. Spike +2 = HRV crash warning (predictive 24-48h ahead)
            resp_icon = "🟢" if resp_avg <= 15 else ("🟡" if resp_avg <= 17 else "🔴")
            warn = "  ⚠️ HRV crash risk in 24-48h" if resp_avg > 16 else ""
            print(f"   Respiratory : {resp_avg:>4.1f} br/min  {resp_icon}{warn}")
        if spo2_avg is not None:
            # Baseline ≥95% sea level. <94% = poor sleep oxygen
            spo2_icon = "🟢" if spo2_avg >= 95 else ("🟡" if spo2_avg >= 93 else "🔴")
            print(f"   SpO2 avg    : {spo2_avg:>4.0f}%      {spo2_icon}  (Sea level baseline)")
        if spo2_low is not None:
            spo2_low_icon = "🟢" if spo2_low >= 92 else ("🟡" if spo2_low >= 88 else "🔴")
            print(f"   SpO2 low    : {spo2_low:>4.0f}%      {spo2_low_icon}")

    # --- Training Load (PMC) ---
    print(f"\n{SEP}")
    print("📈 TRAINING LOAD (PMC)")
    try:
        from training_load import (load_activities, daily_tss_map, calc_pmc,
                                    compute_current_pmc, PMC_WARMUP_DAYS, tsb_label)
        _acts    = load_activities()                          # file + live overlay (กัน stale)
        _start   = today - timedelta(days=PMC_WARMUP_DAYS)    # shared warmup (canonical)
        _pmc     = compute_current_pmc(_acts, today)          # matches training_load/season_summary exactly
        if _pmc:
            _, _, _ctl, _atl, _tsb = _pmc[-1]
            _ctl7     = round(_ctl - _pmc[-8][2], 1) if len(_pmc) >= 8 else 0
            _ctl7_str = f"({'↑' if _ctl7>=0 else '↓'}{abs(_ctl7):+.1f} vs 7d)"
            _lbl      = tsb_label(_tsb)
            # TSB race day projection
            _tsb_race = None
            if taper_days is not None:
                _future_start = today + timedelta(days=1)
                _future_end   = today + timedelta(days=taper_days)
                _fut_map      = daily_tss_map(_acts, _start, _future_end)
                _fut_pmc      = calc_pmc(_fut_map, _start, _future_end)
                if _fut_pmc:
                    _tsb_race = _fut_pmc[-1][4]
            print(f"   CTL (Fitness)  : {_ctl}  {_ctl7_str}")
            print(f"   ATL (Fatigue)  : {_atl}")
            print(f"   TSB (Form)     : {_tsb:+.1f}  {_lbl}")
            if _tsb_race is not None:
                _race_icon = "✅" if _tsb_race >= 0 else "🟡"
                print(f"   คาด TSB แข่ง   : {_tsb_race:+.1f}  {_race_icon}")
    except Exception:
        print("   ⚠️  ไม่สามารถคำนวณ PMC ได้")

    # --- Weekly Volume (7-day rolling vs plan target) ---
    print(f"\n{SEP}")
    print("📊 WEEKLY VOLUME (7-day rolling)")
    try:
        import json as _json
        from training_planner import get_week_info as _get_week_info

        _wi          = _get_week_info()
        _km_target   = _wi["km_target"]
        _phase       = _wi["phase"]
        _is_deload   = _wi["is_deload"]
        _week_num    = _wi["week_num"]
        _total_weeks = _wi["total_weeks"]

        _acts_path = BASE_DIR / "running_activities_all.json"
        _week_start = today - timedelta(days=today.weekday())   # Monday
        _done_km = 0.0
        if _acts_path.exists():
            for a in _json.loads(_acts_path.read_text()):
                _d = (a.get("distance") or 0) / 1000.0
                _ts = a.get("startTimeLocal", "")[:10]
                if _ts and _ts >= _week_start.isoformat() and _ts <= today.isoformat():
                    _done_km += _d
        _done_km = round(_done_km, 1)
        _remain  = max(0.0, _km_target - _done_km)
        _days_left = max(0, 6 - today.weekday())
        _per_day = round(_remain / _days_left, 1) if _days_left > 0 else 0.0
        _pct = round(_done_km / _km_target * 100) if _km_target > 0 else 0

        _deload_tag = " 🔄 deload" if _is_deload else ""
        print(f"   Week {_week_num}/{_total_weeks} — Phase: {_phase.replace('_',' ').title()}{_deload_tag}")
        print(f"   Done so far : {_done_km} km  ({_pct}% ของเป้า)")
        print(f"   Target      : {_km_target} km")
        if _days_left > 0:
            print(f"   Remaining   : {round(_remain,1)} km ใน {_days_left} วัน → ~{_per_day} km/day")
        else:
            _delta = round(_done_km - _km_target, 1)
            _mark  = "✅ ครบเป้า" if _delta >= 0 else f"⚠️  ขาด {abs(_delta)} km"
            print(f"   สรุปสัปดาห์ : {_mark}")
    except Exception as _e:
        print(f"   ⚠️  ไม่สามารถคำนวณ weekly volume: {_e}")

    # --- Injury Risk ---
    print(f"\n{SEP}")
    print("⚕️  INJURY RISK")
    try:
        from injury_risk_detector import analyze as _injury_analyze
        _risk    = _injury_analyze(lookback_days=14)
        _overall = _risk.get("overall_risk", "")
        _risks   = _risk.get("risks", [])
        print(f"   {_overall}")
        if _risks:
            for r in _risks[:3]:
                print(f"   ⚠️  {r.get('site','')} — {r.get('reason','')}")
        else:
            print("   Watch: Popliteus + Hamstring ขวา")
    except Exception:
        print("   ⚠️  ไม่สามารถตรวจสอบได้ | Watch: Popliteus + Hamstring ขวา")

    # --- Training Decision + Plan ---
    decision, reason = readiness_decision(bb, hrv_status, day_type, args.pain, taper_days)
    print(f"\n{SEP}")
    print(f"🎯 TODAY'S PLAN{taper_tag}")
    print(f"   {reason}")
    plan = build_action_plan(day_info, decision, day_type, taper_race, taper_days)
    for line in plan:
        print(line)

    # --- Nutrition ---
    nutrition_key = day_type if decision != "MODIFY" else "easy"
    print(f"\n{SEP}")
    print("💊 NUTRITION")
    print(f"   Pre : {NUTRITION.get(nutrition_key, 'N/A')}")
    if day_type in ("quality1", "quality2", "long") and decision == "GO":
        print("   Post: iRun 1 เม็ด + BAAM ISO 1 scoop + น้ำ 200ml")
    else:
        print("   Post: BAAM ISO 1 scoop + น้ำ 200ml")

    # --- Taper Monitor ---
    if taper_days is not None and taper_days <= 10:
        print(f"\n{SEP}")
        print(f"⏱️  TAPER MONITOR (T-{taper_days} วัน)")
        try:
            from taper_monitor import analyze_taper, _get_week_mileage, _get_peak_week_km
            for r in RACE_DATES:
                if (r["date"] - today).days == taper_days:
                    _race_name, _race_date = r["name"], r["date"]
                    break
            else:
                _race_name = taper_race or "Race"
                _race_date = today + timedelta(days=taper_days)
            _mileage = _get_week_mileage()
            _peak    = _get_peak_week_km()
            _taper   = analyze_taper(taper_days, bb, _mileage, _peak, _race_name, _race_date)
            print(f"   Status : {_taper['status']}")
            print(f"   Volume : {_taper['actual_km_this_week']}/{_taper['target_km_this_week']} km สัปดาห์นี้")
            if _taper.get("bb_race_projection"):
                print(f"   BB แข่ง: {_taper['bb_race_projection']} (เป้า ≥75)")
            for w in _taper.get("warnings", []):
                print(f"   {w}")
        except Exception as e:
            print(f"   ⚠️  taper_monitor error: {e}")

    print(f"\n{'═'*55}\n")


if __name__ == "__main__":
    main()
