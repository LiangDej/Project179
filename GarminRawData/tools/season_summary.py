#!/usr/bin/env python3
"""
season_summary.py — One-stop season status dashboard
Combines: training_planner + PMC + HRV trend + weekly volume + injury risk

Usage:
    python3 season_summary.py
    python3 season_summary.py --race sponsor21
"""
import sys, os, json, argparse
from datetime import date, timedelta
from pathlib import Path

TOOLS_DIR = Path(__file__).parent
BASE_DIR  = TOOLS_DIR.parent
COACH_MCP = BASE_DIR.parent / "skills" / "garmin_coach_mcp"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from config import ATHLETE, VDOT_PACES  # noqa: E402


def fmt_pace(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}/km"


def main():
    parser = argparse.ArgumentParser()
    try:
        from race_registry import race_choices, active_race_key
        _race_default, _race_choices = active_race_key(), race_choices()
    except Exception:
        _race_default, _race_choices = "atm", ["hm", "atm", "fuji"]
    parser.add_argument("--race", default=_race_default, choices=_race_choices)
    args = parser.parse_args()

    today    = date.today()
    SEP      = "─" * 60
    WIDE_SEP = "═" * 60

    print(f"\n{WIDE_SEP}")
    print(f"🗻 SEASON SUMMARY — {today}")
    print(f"   VDOT {ATHLETE['vdot']} | Target: {args.race.upper()}")
    print(f"{WIDE_SEP}")

    # ─────────────────────────────────────────────────────────
    # 1. Season plan context
    # ─────────────────────────────────────────────────────────
    print(f"\n📅 SEASON PLAN")
    print(SEP)
    try:
        from training_planner import get_week_info
        wi = get_week_info()
        phase       = wi["phase"].replace("_", " ").title()
        week_num    = wi["week_num"]
        total_weeks = wi["total_weeks"]
        km_target   = wi["km_target"]
        days_to_race = wi["days_to_race"]
        is_deload   = wi["is_deload"]
        easy_km     = wi["easy_km"]
        race_date   = wi["race_date"]

        deload_tag = " 🔄 deload week" if is_deload else ""
        pct_done   = round((week_num - 1) / total_weeks * 100)

        print(f"   Phase       : {phase}{deload_tag}")
        print(f"   Progress    : Week {week_num}/{total_weeks}  ({pct_done}% of season done)")
        print(f"   Race        : {wi['race_name']} on {race_date}  ({days_to_race}d away)")
        print(f"   km target   : {km_target} km/week  |  easy run: ~{easy_km} km")
    except Exception as e:
        print(f"   ⚠️  training_planner error: {e}")

    # ─────────────────────────────────────────────────────────
    # 2. VDOT + paces
    # ─────────────────────────────────────────────────────────
    print(f"\n📐 FITNESS (VDOT {ATHLETE['vdot']})")
    print(SEP)
    try:
        from vdot_math import predict_race_time
        vdot = ATHLETE["vdot"]
        hm_min  = predict_race_time(vdot, 21097)
        fm_min  = predict_race_time(vdot, 42195)
        hm_h, hm_m = divmod(int(hm_min), 60)
        fm_h, fm_m = divmod(int(fm_min), 60)
        print(f"   HM predicted : {hm_h}h {hm_m:02d}min")
        print(f"   FM predicted : {fm_h}h {fm_m:02d}min")
    except Exception:
        pass
    print(f"   E  (Easy)    : {fmt_pace(VDOT_PACES['E'][0])}–{fmt_pace(VDOT_PACES['E'][1])}")
    print(f"   M  (Marathon): {fmt_pace(VDOT_PACES['M'][0])}–{fmt_pace(VDOT_PACES['M'][1])}")
    print(f"   T  (Threshold): {fmt_pace(VDOT_PACES['T'][0])}–{fmt_pace(VDOT_PACES['T'][1])}")
    print(f"   I  (Interval): {fmt_pace(VDOT_PACES['I'][0])}–{fmt_pace(VDOT_PACES['I'][1])}")

    # ─────────────────────────────────────────────────────────
    # 3. PMC (CTL / ATL / TSB)
    # ─────────────────────────────────────────────────────────
    print(f"\n📈 TRAINING LOAD (PMC)")
    print(SEP)
    try:
        from training_load import compute_current_pmc, tsb_label
        pmc    = compute_current_pmc()   # canonical — matches training_load CLI exactly
        if pmc:
            _, _, ctl, atl, tsb = pmc[-1]
            lbl = tsb_label(tsb)
            ctl_trend = round(ctl - pmc[-8][2], 1) if len(pmc) >= 8 else 0
            trend_dir = "↑" if ctl_trend >= 0 else "↓"
            print(f"   CTL (Fitness) : {ctl}  ({trend_dir}{abs(ctl_trend):+.1f} vs 7d ago)")
            print(f"   ATL (Fatigue) : {atl}")
            print(f"   TSB (Form)    : {tsb:+.1f}  {lbl}")

            # 4-week CTL progression
            print(f"   CTL history   : ", end="")
            steps = []
            for i in [28, 21, 14, 7, 0]:
                idx = -(i + 1)
                if abs(idx) <= len(pmc):
                    steps.append(f"{pmc[idx][2]:.0f}")
            print("  →  ".join(steps) + "  (4w ago → now)")
    except Exception as e:
        print(f"   ⚠️  PMC error: {e}")

    # ─────────────────────────────────────────────────────────
    # 4. HRV + Recovery
    # ─────────────────────────────────────────────────────────
    print(f"\n💓 HRV & RECOVERY")
    print(SEP)
    try:
        from hrv_trend import load_health, crash_report
        records = load_health(30)
        if records:
            crash = crash_report(records)
            last  = records[-1]
            rhr_recent = [r.get("resting_hr") for r in records[-7:] if r.get("resting_hr")]
            bb_recent  = [r.get("bb_high")    for r in records[-7:] if r.get("bb_high")]
            rhr_avg = round(sum(rhr_recent)/len(rhr_recent), 1) if rhr_recent else None
            bb_avg  = round(sum(bb_recent)/len(bb_recent), 1)   if bb_recent  else None
            bal_pct = round(sum(1 for r in records[-7:] if r.get("hrv_status") == "BALANCED") / 7 * 100)

            print(f"   HRV today     : {last.get('hrv_status','?')}  (BALANCED {bal_pct}% last 7d)")
            print(f"   RHR 7d avg    : {rhr_avg} bpm")
            print(f"   BB overnight  : {bb_avg} (7d avg)")
            print(f"   Crash status  : {crash['emoji']} {crash['headline']}")
    except Exception as e:
        print(f"   ⚠️  HRV error: {e}")

    # ─────────────────────────────────────────────────────────
    # 5. Weekly volume
    # ─────────────────────────────────────────────────────────
    print(f"\n📊 VOLUME (last 4 weeks)")
    print(SEP)
    try:
        from activity_loader import load_activities_merged
        acts_all  = load_activities_merged()   # file + live overlay (กัน stale)
        by_week: dict[str, float] = {}
        for a in acts_all:
            dist_km = (a.get("distance") or 0) / 1000
            ts = a.get("startTimeLocal", "")[:10]
            if ts:
                d  = date.fromisoformat(ts)
                ws = str(d - timedelta(days=d.weekday()))
                by_week[ws] = by_week.get(ws, 0) + dist_km

        recent_weeks = sorted(by_week.keys())[-4:]
        for ws in recent_weeks:
            bar_len = int(by_week[ws] / 3)
            bar     = "█" * bar_len
            current = "◄ current" if ws == str(today - timedelta(days=today.weekday())) else ""
            print(f"   {ws}  {by_week[ws]:>5.1f} km  {bar} {current}")
    except Exception as e:
        print(f"   ⚠️  Volume error: {e}")

    # ─────────────────────────────────────────────────────────
    # 6. Injury risk
    # ─────────────────────────────────────────────────────────
    print(f"\n⚕️  INJURY RISK")
    print(SEP)
    try:
        from injury_risk_detector import analyze as injury_analyze
        risk    = injury_analyze(lookback_days=14)
        overall = risk.get("overall_risk", "")
        risks   = risk.get("risks", [])
        print(f"   {overall}")
        for r in risks[:3]:
            print(f"   ⚠️  {r.get('site','')} — {r.get('reason','')}")
        if not risks:
            print("   Watch: Popliteus + Hamstring ขวา")
    except Exception as e:
        print(f"   ⚠️  Injury risk error: {e}")

    # ─────────────────────────────────────────────────────────
    # 7. Stamina backfill status
    # ─────────────────────────────────────────────────────────
    print(f"\n🔋 STAMINA DATA")
    print(SEP)
    try:
        from stamina_patcher import MASTER_JSON
        master   = json.loads(MASTER_JSON.read_text()) if MASTER_JSON.exists() else {}
        sessions = master.get("sessions", [])
        patched  = sum(1 for s in sessions if s.get("stamina_drain_pct") is not None)
        pending  = sum(1 for s in sessions
                       if s.get("stamina_drain_pct") is None and not s.get("stamina_unavailable"))
        print(f"   Patched : {patched}/{len(sessions)} sessions")
        if pending:
            print(f"   Pending : {pending} sessions (run `stamina_patcher.py --backfill`)")
        else:
            print("   ✅ All sessions accounted for")
    except Exception as e:
        print(f"   ⚠️  Stamina error: {e}")

    print(f"\n{WIDE_SEP}\n")


if __name__ == "__main__":
    main()
