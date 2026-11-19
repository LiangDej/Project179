#!/usr/bin/env python3
"""
daily_brief.py — JD Analytics Toolkit (P1)
ตรวจสอบความพร้อมประจำวัน ตัดสินใจ Go/Modify/Rest และสร้าง Action Plan

Usage:
    python3 daily_brief.py                      # live mode (ดึง Garmin)
    python3 daily_brief.py --offline            # ใช้ cache เท่านั้น (ไม่ต่อ Garmin)
    python3 daily_brief.py --pain mild
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

from config import ATHLETE, DAY_NUTRITION  # noqa: E402
# ONE readiness engine + ONE day prescription (v1.8) — re-exported for callers/tests
from readiness import (readiness_assess, readiness_decision,  # noqa: E402,F401
                       RHR_WARN_DELTA, RHR_HIGH_DELTA, active_races, active_taper)
from garmin_client import get_client, get_health_cached    # noqa: E402
from hrv_trend import readiness_hrv, night_hrv_state, rhr_baseline  # noqa: E402

RACE_DATES = active_races()


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


def get_taper_phase(on_date: date | None = None):
    """(race name, days) if on_date is inside that race's TIER taper window."""
    name, days, _ = active_taper(on_date or date.today(), RACE_DATES)
    return name, days


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pain", choices=["none", "mild", "moderate"],
                        default=ATHLETE.get("pain_status", "none"),
                        help="Overrides athlete.json → pain_status for this run only")
    parser.add_argument("--offline", action="store_true",
                        help="ใช้ health cache เท่านั้น ไม่ต่อ Garmin (ใช้เมื่อ API ไม่พร้อม)")
    parser.add_argument("--fresh", action="store_true",
                        help="บังคับดึง health data สดจาก Garmin (bypass cache 2h TTL) — "
                             "ใช้ตอนเพิ่งตื่น/เพิ่ง sync นาฬิกา ให้ได้ HRV/BB/Sleep ล่าสุด")
    args = parser.parse_args()

    today     = date.today()
    today_str = today.strftime("%Y-%m-%d")
    from session_prescriber import prescribe_day, WEEKDAY_TH
    from training_planner import get_week_info as _get_week_info
    _wi = _get_week_info()
    day_type = _wi["structure"][today.weekday()]

    SEP  = "─" * 55
    taper_race, taper_days = get_taper_phase()
    taper_tag = f" (T-{taper_days})" if taper_days is not None else ""

    # --- Header ---
    print(f"\n{'═'*55}")
    print(f"🌅 DAILY BRIEF — {today_str} ({WEEKDAY_TH[today.weekday()]}){taper_tag}")
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

    bb          = health.get("bb_high") if health.get("bb_high") is not None else health.get("body_battery")
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

    # Activity-history sync freshness — running_activities_all.json (used by
    # PMC/weekly-volume/injury-risk) can silently go stale if the watch
    # hasn't synced to the phone, the Garmin sync script hasn't been run, or
    # a token expired. Without an explicit check here, a coach/LLM reading
    # this brief has no way to know the CTL/ATL/weekly-volume numbers below
    # might be based on Wednesday's data while reading it Friday morning.
    try:
        from activity_loader import ACTS_FILE
        if ACTS_FILE.exists():
            _sync_age_days = (date.today() - date.fromtimestamp(ACTS_FILE.stat().st_mtime)).days
            if _sync_age_days >= 1:
                print(f"\n⚠️  DATA_STATUS: กิจกรรมล่าสุด sync มา {_sync_age_days} วันก่อน — "
                      f"รัน fetch_incremental.py ก่อนเชื่อ CTL/ATL/weekly volume ด้านล่าง")
    except Exception:
        pass

    src_tag = f" [{src}]" if src != "live" else ""
    print(f"\n📊 BODY STATUS{src_tag}")
    bb_now = health.get("body_battery")
    bb_now_tag = f"  (ตอนนี้ {bb_now})" if bb_now is not None and bb_now != bb else ""
    print(f"   🔋 Body Battery : {bb if bb is not None else 'N/A'} (peak เช้า){bb_now_tag}  {_icon(bb,75,50)}")
    # v1.7.2: last night's HRV decides today; Garmin's status is the 7-day trend.
    _hl, _ns = health.get("hrv_last_night"), night_hrv_state(health)
    if _ns is not None:
        _hicon = {"BALANCED": "🟢", "LOW": "🔴", "HIGH": "🟡"}[_ns]
        print(f"   💓 HRV เมื่อคืน : {_hl} ms  (ช่วงปกติ {health.get('hrv_balanced_low')}–"
              f"{health.get('hrv_balanced_high')})  {_hicon}  ← ใช้ตัดสินวันนี้")
        print(f"      HRV 7 วัน  : {health.get('hrv_weekly_avg')} ms → {hrv_status}  (แนวโน้ม — ช้ากว่าร่างกายหลายวัน)")
    else:
        print(f"   💓 HRV          : {hrv_status}  (7-day status — ไม่มีค่าเมื่อคืน)")
    # Readiness RHR baseline = 28-day median (hrv_trend.rhr_baseline), not
    # athlete.json rhr (Karvonen input ~yearly minimum). Fallback when <7 nights.
    _rb = rhr_baseline(today=today) or ATHLETE.get("rhr")
    _rd = f"  (baseline {_rb:g}, {rhr - _rb:+g})" if (rhr and _rb) else ""
    print(f"   ❤️  Resting HR   : {rhr} bpm{_rd}  {_icon(rhr, _rb + 3, _rb + 5, invert=True)}" if rhr else "   ❤️  Resting HR   : N/A")
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
                                    compute_current_pmc, PMC_WARMUP_DAYS, tsb_label,
                                    pmc_confidence)
        _acts    = load_activities()                          # file + live overlay (กัน stale)
        _start   = today - timedelta(days=PMC_WARMUP_DAYS)    # shared warmup (canonical)
        _pmc     = compute_current_pmc(_acts, today)          # matches training_load/season_summary exactly
        _, _, _pmc_warning = pmc_confidence(_acts, today)
        if _pmc_warning:
            print(f"   {_pmc_warning}")
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
                print(f"   คาด TSB แข่ง   : {_tsb_race:+.1f}  {_race_icon}  (ถ้าพักทุกวันจนถึงวันแข่ง — ค่าบน)")
    except Exception:
        print("   ⚠️  ไม่สามารถคำนวณ PMC ได้")

    # --- Weekly Volume (Mon–today vs plan target) ---
    print(f"\n{SEP}")
    print("📊 WEEKLY VOLUME (จันทร์–วันนี้)")
    try:
        from activity_loader import load_activities_merged
        _km_target   = _wi["km_target"]
        _phase       = _wi["phase"]
        _is_deload   = _wi["is_deload"]
        _week_num    = _wi["week_num"]
        _total_weeks = _wi["total_weeks"]
        _week_start = today - timedelta(days=today.weekday())   # Monday
        _done_km = sum((a.get("distance") or 0) / 1000.0 for a in load_activities_merged()
                       if _week_start.isoformat() <= (a.get("startTimeLocal") or "")[:10] <= today.isoformat())
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
                print(f"   ⚠️  {r.get('signal','')} — {r.get('detail','')}")
        else:
            print("   ไม่มีจุดที่ต้องเฝ้าระวังเป็นพิเศษตอนนี้")
    except Exception:
        print("   ⚠️  ไม่สามารถตรวจสอบได้")

    # --- Training Decision + Plan (same engine as the weekly plan) ---
    _s = prescribe_day(today, _wi, bb, readiness_hrv(health), args.pain, rhr, _rb, live=True)
    decision = _s["decision"]
    print(f"\n{SEP}")
    print(f"🎯 TODAY'S PLAN{taper_tag}")
    if _s.get("reason"):
        print(f"   {_s['reason']}")
    for _o in _s.get("others", []):
        print(f"   ↳ ร่วมด้วย: {_o}")
    if _s.get("taper_note"):
        print(f"   {_s['taper_note']}")
    if _wi.get("phase_info"):
        print(f"   🗓️  Phase: {_wi['phase_info']['name']} — {_wi['prescription'].get('focus', '')}")
    print(f"   📋 {_s.get('label', '')}")
    if _s.get("workout"):
        print(f"   ⏱️  {_s['workout']}")
    if _s.get("note"):
        print(f"   📌 {_s['note']}")
    for _n in _s.get("jd_notes") or []:
        print(f"   ✂️  {_n}")

    # Standing athlete constraints (athlete.json → coaching_constraints) —
    # printed every brief so an LLM coach reading this output can't drift
    # from preferences the athlete already stated.
    _cc = ATHLETE.get("coaching_constraints") or []
    if _cc:
        print(f"\n📌 COACHING CONSTRAINTS (athlete.json — ห้ามละเมิด)")
        for c in _cc:
            print(f"   • {c}")

    _sp = _s.get("spec") or {}
    _is_run_today = decision != "REST" and day_type not in ("strength", "rest") and _sp
    if _is_run_today:
        if _sp.get("type") in ("T", "I", "R", "M", "combo"):
            print(f"   💡 ลู่ไฟฟ้า: Incline 1.0% — Quality คุมด้วย pace/ความเร็วสายพาน (HR เป็นเพดานเท่านั้น)")
        else:
            print(f"   💡 ลู่ไฟฟ้า: Incline 1.0% — Easy/Long คุมด้วย HR (≤ E ceiling) ไม่ใช่ GPS pace")
        try:
            from bangkok_climate import morning_temp_c_for, ely_penalty_fraction
            _temp = morning_temp_c_for(today)
            _pen  = ely_penalty_fraction(_temp) * 100
            if _pen > 0:
                print(f"   💡 Outdoor: เช้านี้ราว {_temp:.0f}°C — Ely heat penalty +{_pen:.1f}% ปล่อยเพซ Easy/Long ช้าลงได้ คุมด้วย HR แทน pace")
        except Exception:
            pass

    # --- Nutrition ---
    print(f"\n{SEP}")
    print("💊 NUTRITION")
    print(f"   Pre : {_s.get('nutrition', '-')}")
    if _is_run_today:
        print(f"   Post: {DAY_NUTRITION['post_run']}")

    # --- Taper Monitor ---
    if taper_days is not None:
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
            _, _, _prof = active_taper(today, RACE_DATES)
            _taper   = analyze_taper(taper_days, bb, _mileage, _peak, _race_name, _race_date, _prof)
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
