#!/usr/bin/env python3
"""
training_load.py — ATL / CTL / TSB Performance Management Chart (PMC)

คำนวณ Training Load ด้วย Exponential Weighted Moving Average (TrainingPeaks model):
  CTL (Chronic Training Load, τ=42 วัน)  = Fitness
  ATL (Acute Training Load,   τ=7  วัน)  = Fatigue
  TSB (Training Stress Balance)           = Form = CTL - ATL

hrTSS formula (HR-based, ไม่ใช่ Power-based):
  Intensity Factor = (avgHR - RHR) / (T-HR_threshold - RHR)
  hrTSS = duration_hours × IF² × 100

Usage:
    python3 training_load.py               # แสดง 42 วันล่าสุด
    python3 training_load.py --days 90     # ดูย้อนหลัง 90 วัน
    python3 training_load.py --race-plan   # แสดง CTL target สำหรับ Fuji
"""

import sys
import json
import math
import argparse
from pathlib import Path
from datetime import date, timedelta

BASE_DIR = Path(__file__).parent.parent
sys.path.append(str(BASE_DIR.parent / "skills" / "garmin_coach_mcp"))
from config import ATHLETE, HR_ZONE_BOUNDS  # noqa: E402

RHR = ATHLETE["rhr"]
MHR = ATHLETE["mhr"]
T_HR = HR_ZONE_BOUNDS["Z3_T"][0]   # lower bound of T zone ≈ threshold HR

# Exponential decay constants
K_CTL = 1 - math.exp(-1 / 42)   # τ = 42 days (Fitness)
K_ATL = 1 - math.exp(-1 / 7)    # τ = 7  days (Fatigue)


def load_activities():
    """File history + live Garmin overlay (กัน stale snapshot หลอก ATL/TSB) — shared loader."""
    from activity_loader import load_activities_merged
    return load_activities_merged()


def calc_hr_tss(act):
    """Calculate HR-based TSS for a single activity."""
    avg_hr  = act.get("averageHR", 0) or 0
    dur_sec = act.get("duration", 0) or 0
    if avg_hr <= RHR or dur_sec == 0:
        return 0.0
    hrr_denom = T_HR - RHR
    if hrr_denom <= 0:
        return 0.0
    intensity = (avg_hr - RHR) / hrr_denom
    tss = (dur_sec / 3600) * (intensity ** 2) * 100
    return round(tss, 1)


def daily_tss_map(activities, start_date, end_date):
    """Build {date_str: total_tss} for every day in range."""
    tss_map = {}
    cur = start_date
    while cur <= end_date:
        ds = cur.strftime("%Y-%m-%d")
        day_acts = [a for a in activities if a.get("startTimeLocal", "")[:10] == ds]
        tss_map[ds] = round(sum(calc_hr_tss(a) for a in day_acts), 1)
        cur += timedelta(days=1)
    return tss_map


def calc_pmc(tss_map, start_date, end_date):
    """
    Calculate CTL, ATL, TSB series using PMC exponential model.
    Returns list of (date_str, tss, ctl, atl, tsb) tuples.
    """
    ctl = 0.0
    atl = 0.0
    result = []

    cur = start_date
    while cur <= end_date:
        ds  = cur.strftime("%Y-%m-%d")
        tss = tss_map.get(ds, 0.0)

        ctl = ctl * (1 - K_CTL) + tss * K_CTL
        atl = atl * (1 - K_ATL) + tss * K_ATL
        tsb = ctl - atl

        result.append((ds, tss, round(ctl, 1), round(atl, 1), round(tsb, 1)))
        cur += timedelta(days=1)

    return result


# Warmup so CTL (42-day EWMA) effectively fully converges (~5×τ) before 'today'.
# FIXED start (not stacked on display window) → every tool gets IDENTICAL CTL/ATL/TSB.
PMC_WARMUP_DAYS = 200


def compute_current_pmc(activities=None, today=None, display_days=0):
    """Canonical PMC series ending today — SINGLE SOURCE of CTL/ATL/TSB for all tools.

    The seed start is fixed at today-PMC_WARMUP_DAYS (expanded only if a caller
    needs a display window longer than that), so the CTL/ATL/TSB AT TODAY is the
    same number no matter who calls it. Returns list of (date_str, tss, ctl, atl, tsb).
    """
    if today is None:
        today = date.today()
    if activities is None:
        activities = load_activities()
    lookback = max(PMC_WARMUP_DAYS, display_days + 60)
    start = today - timedelta(days=lookback)
    return calc_pmc(daily_tss_map(activities, start, today), start, today)


def tsb_label(tsb):
    """Classify TSB (Form) into training state."""
    if tsb > 25:
        return "🏁 Race Ready — พร้อมแข่ง ร่างกายฟิตและพัก"
    elif tsb > 10:
        return "✅ Fresh — พักพอ เหมาะซ้อม Quality"
    elif tsb > 0:
        return "🟢 Neutral — สมดุล ซ้อมได้ตามปกติ"
    elif tsb > -10:
        return "🟡 Productive — Fitness กำลังสร้าง ยอมรับได้"
    elif tsb > -25:
        return "⚠️  Overreaching — เริ่มล้าสะสม ลด Volume ลง"
    else:
        return "🔴 Overtraining — พักทันที หยุดซ้อมหนัก"


def print_pmc_table(pmc_series, lookback=14):
    """Print the last N days of PMC data."""
    recent = pmc_series[-lookback:] if len(pmc_series) > lookback else pmc_series
    print(f"\n{'─'*65}")
    print(f"  {'Date':<12} {'TSS':>6} {'CTL':>7} {'ATL':>7} {'TSB':>7}  State")
    print(f"{'─'*65}")
    for ds, tss, ctl, atl, tsb in recent:
        state = "→ Today" if ds == date.today().strftime("%Y-%m-%d") else ""
        tsb_color = "+" if tsb >= 0 else ""
        print(f"  {ds:<12} {tss:>6.1f} {ctl:>7.1f} {atl:>7.1f} {tsb_color}{tsb:>6.1f}  {state}")
    print(f"{'─'*65}")


def race_plan_targets():
    """Display CTL targets needed for Fuji Sub 4:00."""
    print(f"""
{'='*65}
🗻 FUJI MARATHON RACE PLAN — CTL Targets (Sub 4:00)
{'='*65}
  Phase              | Target CTL | Justification
  ─────────────────────────────────────────────────
  Base Building      | 55–65      | Aerobic foundation
  Quality Phase      | 65–75      | Lactate threshold work
  Race Specific      | 70–80      | Peak load before taper
  Taper (10d before) | 55–65      | TSB +10 to +25 = Race Ready
  Race Day           | 60–70      | CTL maintained, ATL drops
  ─────────────────────────────────────────────────
  Notes:
  • สร้าง CTL ไม่เกิน +3–5 ต่อสัปดาห์ (10% rule applies)
  • ถ้า TSB ต่ำกว่า -30 ต่อเนื่อง 2 สัปดาห์ → ลด Volume ทันที
  • Target Race Day TSB: +10 ถึง +25 (Fresh แต่ไม่ sluggish)
{'='*65}""")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",      type=int, default=42,
                        help="จำนวนวันที่แสดงใน PMC table (default: 42)")
    parser.add_argument("--race-plan", action="store_true",
                        help="แสดง CTL target สำหรับ Fuji Marathon")
    args = parser.parse_args()

    activities = load_activities()
    today      = date.today()

    # Canonical PMC (shared warmup → matches season_summary/daily_brief exactly)
    display_days = args.days
    pmc_series   = compute_current_pmc(activities, today, display_days=display_days)

    # Latest values
    _, tss_today, ctl, atl, tsb = pmc_series[-1]
    state = tsb_label(tsb)

    print(f"\n{'='*65}")
    print(f"📈 TRAINING LOAD — PMC Report ({today})")
    print(f"{'='*65}")
    print(f"\n  🏋️  CTL (Fitness)  : {ctl:6.1f}")
    print(f"  ⚡ ATL (Fatigue)  : {atl:6.1f}")
    print(f"  🎯 TSB (Form)     : {tsb:+6.1f}   {state}")
    print(f"  📊 TSS Today      : {tss_today:6.1f}")

    # Trend: CTL change over last 7 days
    if len(pmc_series) >= 8:
        ctl_7d_ago = pmc_series[-8][2]
        ctl_delta  = ctl - ctl_7d_ago
        arrow = "↑" if ctl_delta > 0 else "↓"
        print(f"  📈 CTL 7-day Δ   : {arrow}{abs(ctl_delta):.1f}  ", end="")
        if abs(ctl_delta) > 5:
            print("⚠️  เพิ่มเร็วเกินไป — เสี่ยง Overreaching")
        elif ctl_delta > 0:
            print("✅ กำลังสร้าง Fitness อย่างปลอดภัย")
        else:
            print("🔵 CTL ลดลง (Taper หรือ Recovery สัปดาห์นี้)")

    print_pmc_table(pmc_series, lookback=min(args.days, len(pmc_series)))

    # Next race countdown
    from datetime import datetime as dt
    try:
        from race_registry import list_races
        races = [(r["name"], date.fromisoformat(r["date"]))
                 for _, r in list_races(active_only=True)]
    except Exception:
        races = [("🏁 ATM Bangkok Marathon", date(2026, 11, 29))]
    print("\n  🏁 Race Countdown:")
    for name, rdate in races:
        delta = (rdate - today).days
        if delta >= 0:
            # Predict CTL on race day (rough: assume daily TSS continues)
            recent_tss = [row[1] for row in pmc_series[-7:]]
            avg_7d_tss = sum(recent_tss) / len(recent_tss) if recent_tss else 0
            # Simple forward projection (flat load)
            proj_ctl = ctl
            proj_atl = atl
            for _ in range(delta):
                proj_ctl = proj_ctl * (1 - K_CTL) + avg_7d_tss * K_CTL
                proj_atl = proj_atl * (1 - K_ATL) + avg_7d_tss * K_ATL
            proj_tsb = proj_ctl - proj_atl
            print(f"     {name}: {delta} วัน | proj CTL {proj_ctl:.1f} | TSB {proj_tsb:+.1f} {tsb_label(proj_tsb)[:20]}")

    if args.race_plan:
        race_plan_targets()

    print(f"\n{'='*65}\n")


if __name__ == "__main__":
    main()
