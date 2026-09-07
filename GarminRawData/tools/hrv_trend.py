"""
hrv_trend.py — HRV baseline + weekly trend + overtraining early warning
Reads wellness/*.json (single source of truth)

Usage:
    python3 hrv_trend.py
    python3 hrv_trend.py --days 60
    python3 hrv_trend.py --warn        # overtraining check only
    python3 hrv_trend.py --crash       # HRV crash detector + actionable advice
"""

from __future__ import annotations
import os, sys, json, argparse
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

CACHE_DIR = Path(__file__).resolve().parent.parent / "wellness"

HRV_SCORE = {"BALANCED": 2, "LOW": 1, "POOR": 0, "UNBALANCED": 0}
HRV_EMOJI = {"BALANCED": "🟢", "LOW": "🟡", "POOR": "🔴", "UNBALANCED": "🔴"}


def load_health(days: int = 90) -> list[dict]:
    today = date.today()
    cutoff = today - timedelta(days=days)
    records = []
    for f in CACHE_DIR.glob("wellness_*.json"):
        try:
            d = date.fromisoformat(f.stem.replace("wellness_", ""))
            if d < cutoff:
                continue
            with open(f) as fh:
                rec = json.load(fh)
                rec["_date"] = d
                records.append(rec)
        except Exception:
            pass
    return sorted(records, key=lambda x: x["_date"])


def rolling_avg(vals: list[float], n: int) -> list[float | None]:
    result = []
    for i in range(len(vals)):
        window = [v for v in vals[max(0, i-n+1):i+1] if v is not None]
        result.append(round(sum(window)/len(window), 1) if window else None)
    return result


def crash_report(records: list[dict]) -> dict:
    """
    HRV crash detector — returns severity + actionable prescription.
    Crash = 3+ consecutive days with HRV status NOT BALANCED.
    """
    SUPPRESSED = {"LOW", "POOR", "UNBALANCED"}

    # Count current streak of suppressed HRV (most recent first)
    streak = 0
    for r in reversed(records):
        if r.get("hrv_status", "") in SUPPRESSED:
            streak += 1
        else:
            break

    # Find longest crash streak in full window
    max_streak = 0
    cur = 0
    for r in records:
        if r.get("hrv_status", "") in SUPPRESSED:
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 0

    # Severity classification
    if streak == 0:
        severity   = "none"
        emoji      = "✅"
        headline   = "HRV ปกติ — ไม่มีสัญญาณ crash"
        advice     = []
    elif streak < 3:
        severity   = "watch"
        emoji      = "🟡"
        headline   = f"HRV suppressed {streak} วันติด — watch mode"
        advice     = [
            "ลด intensity ลง 20% หากซ้อม quality",
            "นอนก่อน 22:00 คืนนี้",
            "ติดตามต่อ 1–2 วัน ก่อนตัดสินใจ",
        ]
    elif streak < 5:
        severity   = "crash"
        emoji      = "🔴"
        headline   = f"HRV CRASH — {streak} วันติด suppressed"
        advice     = [
            "ยกเลิก Quality session ทั้งหมดจนกว่า HRV กลับ BALANCED",
            "วิ่ง Easy ≤ 6km HR < 140 หรือพักเต็ม วันเว้นวัน",
            "นอน ≥ 8h | หลีกเลี่ยงแอลกอฮอล์และคาเฟอีนบ่าย",
            "ตรวจ RHR เช้า — ถ้า >baseline+5 → REST เต็มวัน",
        ]
    else:
        severity   = "overreach"
        emoji      = "🚨"
        headline   = f"OVERREACHING ALARM — {streak} วันติด suppressed (>5 วัน)"
        advice     = [
            "หยุดซ้อม 3–5 วัน recovery block ทันที",
            "Easy walk หรือ yoga เท่านั้น — ห้ามวิ่ง",
            "ตรวจร่างกาย: ปัสสาวะสีเหลืองเข้ม, น้ำหนักลด, หัวใจเต้นเร็วตอนตื่น?",
            "พิจารณาปรึกษาแพทย์ถ้า streak ยาวนานกว่านี้",
        ]

    return {
        "severity":    severity,
        "streak":      streak,
        "max_streak":  max_streak,
        "emoji":       emoji,
        "headline":    headline,
        "advice":      advice,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",  type=int, default=60)
    parser.add_argument("--warn",  action="store_true")
    parser.add_argument("--crash", action="store_true",
                        help="HRV crash detector with actionable advice")
    args = parser.parse_args()

    records = load_health(args.days)
    if not records:
        print("❌ ไม่พบ health cache — รัน daily_brief.py ก่อน")
        return

    print("=" * 60)
    print(f"💓 HRV TREND ANALYZER — {args.days} วันย้อนหลัง")
    print(f"   {len(records)} data points | today: {date.today()}")
    print("=" * 60)
    print()

    # ---------------------------------------------------------------------------
    # Baselines
    # ---------------------------------------------------------------------------
    rhrs = [r.get("resting_hr") for r in records if r.get("resting_hr")]
    hrv_scores = [HRV_SCORE.get(r.get("hrv_status", ""), None) for r in records]
    hrv_scores_clean = [s for s in hrv_scores if s is not None]
    bb_highs = [r.get("bb_high") for r in records if r.get("bb_high")]

    rhr_baseline = round(sum(rhrs)/len(rhrs), 1) if rhrs else None
    rhr_recent7  = [r.get("resting_hr") for r in records[-7:] if r.get("resting_hr")]
    rhr_recent7_avg = round(sum(rhr_recent7)/len(rhr_recent7), 1) if rhr_recent7 else None

    balanced_count = sum(1 for r in records if r.get("hrv_status") == "BALANCED")
    hrv_pct = round(balanced_count / len(records) * 100, 1) if records else 0

    print(f"📊 Baselines ({args.days}d):")
    print(f"   RHR baseline       : {rhr_baseline} bpm")
    print(f"   RHR last 7d avg    : {rhr_recent7_avg} bpm  "
          f"{'⚠️ +' + str(round(rhr_recent7_avg - rhr_baseline, 1)) + ' vs baseline' if rhr_recent7_avg and rhr_baseline and rhr_recent7_avg > rhr_baseline + 2 else '✅'}")
    print(f"   HRV BALANCED rate  : {hrv_pct}%  "
          f"{'🟢 ดี' if hrv_pct >= 70 else '🟡 ปานกลาง' if hrv_pct >= 50 else '🔴 ต่ำ'}")
    if bb_highs:
        bb_avg = round(sum(bb_highs)/len(bb_highs), 1)
        bb_recent = [r.get("bb_high") for r in records[-7:] if r.get("bb_high")]
        bb_recent_avg = round(sum(bb_recent)/len(bb_recent), 1) if bb_recent else None
        print(f"   BB overnight avg   : {bb_avg}  |  last 7d: {bb_recent_avg}")
    print()

    # ---------------------------------------------------------------------------
    # Overtraining detection
    # ---------------------------------------------------------------------------
    last14 = records[-14:]
    unbalanced_streak = 0
    for r in reversed(last14):
        if r.get("hrv_status") in ("LOW", "POOR", "UNBALANCED"):
            unbalanced_streak += 1
        else:
            break

    rhr_elevated_days = sum(1 for r in last14
                            if r.get("resting_hr") and rhr_baseline
                            and r["resting_hr"] > rhr_baseline + 3)

    warn_flags = []
    if unbalanced_streak >= 3:
        warn_flags.append(f"🔴 HRV unbalanced {unbalanced_streak} วันติด → overreaching signal")
    if rhr_elevated_days >= 5:
        warn_flags.append(f"🔴 RHR สูงกว่า baseline 14 วัน ({rhr_elevated_days}/14 วัน) → systemic fatigue")
    if rhr_recent7_avg and rhr_baseline and rhr_recent7_avg > rhr_baseline + 4:
        warn_flags.append(f"🟡 RHR 7d avg สูงกว่า baseline {round(rhr_recent7_avg-rhr_baseline,1)} bpm")

    print("⚕️  Overtraining Check:")
    if warn_flags:
        for w in warn_flags:
            print(f"   {w}")
    else:
        print(f"   ✅ LOW risk — ไม่มีสัญญาณ overtraining")
    print()

    # HRV Crash detector (--crash or embedded in --warn output)
    crash = crash_report(records)
    print(f"{crash['emoji']}  HRV Crash Detector:")
    print(f"   {crash['headline']}")
    if crash["advice"]:
        print("   📋 Actions:")
        for a in crash["advice"]:
            print(f"      • {a}")
    if crash["max_streak"] > 0 and crash["streak"] != crash["max_streak"]:
        print(f"   (max streak in window: {crash['max_streak']} วัน)")
    print()

    if args.warn or args.crash:
        return

    # ---------------------------------------------------------------------------
    # Weekly summary table
    # ---------------------------------------------------------------------------
    by_week: dict[str, list] = defaultdict(list)
    for r in records:
        d = r["_date"]
        week_start = d - timedelta(days=d.weekday())
        by_week[str(week_start)].append(r)

    print(f"📅 Weekly HRV + RHR Summary:")
    print(f"   {'สัปดาห์':<12} {'days':>5} {'BALANCED%':>10} {'avg RHR':>8} {'avg BB↑':>8}")
    print("   " + "-" * 46)
    for ws in sorted(by_week.keys())[-8:]:
        week = by_week[ws]
        bal_pct = round(sum(1 for r in week if r.get("hrv_status") == "BALANCED") / len(week) * 100)
        avg_rhr = round(sum(r["resting_hr"] for r in week if r.get("resting_hr")) /
                        max(1, sum(1 for r in week if r.get("resting_hr"))), 1)
        avg_bb = round(sum(r["bb_high"] for r in week if r.get("bb_high")) /
                       max(1, sum(1 for r in week if r.get("bb_high"))), 1)
        bal_str = f"{bal_pct}%" + ("🟢" if bal_pct >= 70 else "🟡" if bal_pct >= 50 else "🔴")
        print(f"   {ws:<12} {len(week):>5} {bal_str:>10} {avg_rhr:>8} {avg_bb:>8}")
    print()

    # ---------------------------------------------------------------------------
    # Daily log (last 21 days)
    # ---------------------------------------------------------------------------
    print(f"📋 Daily Log (21 วันล่าสุด):")
    print(f"   {'Date':<12} {'HRV':<12} {'RHR':>5} {'BB↑':>5} {'Sleep':>6}")
    print("   " + "-" * 44)
    for r in records[-21:]:
        hrv = r.get("hrv_status") or "?"
        emoji = HRV_EMOJI.get(hrv, "❓")
        rhr = str(r.get("resting_hr") if r.get("resting_hr") is not None else "?")
        bb = str(r.get("bb_high") if r.get("bb_high") is not None else "?")
        sleep = str(r.get("sleep_score") if r.get("sleep_score") is not None else "?")
        print(f"   {str(r['_date']):<12} {emoji}{hrv:<11} {rhr:>5} {bb:>5} {sleep:>6}")
    print("=" * 60)


if __name__ == "__main__":
    main()
