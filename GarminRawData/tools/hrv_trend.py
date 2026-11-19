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


# ---------------------------------------------------------------------------
# v1.7.2 — readiness signals (single source for daily_brief / session_prescriber)
#
# Garmin's hrv_status is a verdict on the 7-DAY AVERAGE, so after a race it
# stays UNBALANCED for days after the body has recovered (2026-11-19: last
# night 94 ms inside 85–121, weekly avg 79 → "UNBALANCED"). Readiness for
# TODAY uses last night's number; the status stays as the trend signal.
# ---------------------------------------------------------------------------
SUPPRESSED = {"LOW", "POOR", "UNBALANCED"}
RHR_BASELINE_WINDOW = 28   # days before today
RHR_BASELINE_MIN    = 7    # need this many nights, else caller falls back


def night_hrv_state(rec: dict) -> str | None:
    """Last night's HRV vs Garmin's personal balanced range.
    → "LOW" (below range) / "BALANCED" / "HIGH" (above) / None (no numbers)."""
    v, lo, hi = (rec or {}).get("hrv_last_night"), (rec or {}).get("hrv_balanced_low"), \
                (rec or {}).get("hrv_balanced_high")
    if v is None or lo is None or hi is None:
        return None
    return "LOW" if v < lo else ("HIGH" if v > hi else "BALANCED")


def is_suppressed(rec: dict) -> bool:
    """Night counts as suppressed: last-night HRV below range; only when the
    numbers are missing (older records) fall back to the 7-day status."""
    st = night_hrv_state(rec)
    if st is not None:
        return st == "LOW"
    return (rec or {}).get("hrv_status", "") in SUPPRESSED


def readiness_hrv(rec: dict) -> str:
    """HRV label for readiness rules (lower-case, daily_brief vocabulary):
    'balanced' / 'unbalanced' (last night below range) / 'high' (above range),
    or the 7-day status when last night's numbers are missing."""
    st = night_hrv_state(rec)
    if st == "BALANCED":
        return "balanced"
    if st == "LOW":
        return "unbalanced"
    if st == "HIGH":
        return "high"
    return ((rec or {}).get("hrv_status") or "unknown").lower()


def rhr_baseline(records: list[dict] | None = None, today: date | None = None,
                 window: int = RHR_BASELINE_WINDOW) -> float | None:
    """Readiness RHR baseline = median morning RHR of the `window` days BEFORE
    today (today excluded so a bad night can't hide itself). None when fewer
    than RHR_BASELINE_MIN nights — caller falls back to athlete.json rhr.

    athlete.json "rhr" (44, near the yearly minimum) stays the Karvonen input
    for HR zones; it is NOT a typical morning (median ~48–50), so using it for
    readiness flagged normal mornings as +4/+5."""
    today = today or date.today()
    if records is None:
        records = load_health(window + 1)
    vals = sorted(r["resting_hr"] for r in records
                  if r.get("resting_hr") and today - timedelta(days=window) <= r["_date"] < today)
    if len(vals) < RHR_BASELINE_MIN:
        return None
    n = len(vals)
    return float(vals[n // 2]) if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def rolling_avg(vals: list[float], n: int) -> list[float | None]:
    result = []
    for i in range(len(vals)):
        window = [v for v in vals[max(0, i-n+1):i+1] if v is not None]
        result.append(round(sum(window)/len(window), 1) if window else None)
    return result


def crash_report(records: list[dict]) -> dict:
    """
    HRV crash detector — returns severity + actionable prescription.
    Crash = 3+ consecutive nights with last-night HRV below the balanced
    range (v1.7.2; was 7-day status, which counted one race 3–5 times).
    """

    # Count current streak of suppressed HRV (most recent first)
    streak = 0
    for r in reversed(records):
        if is_suppressed(r):
            streak += 1
        else:
            break

    # Find longest crash streak in full window
    max_streak = 0
    cur = 0
    for r in records:
        if is_suppressed(r):
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 0

    # Severity classification
    if streak == 0:
        severity   = "none"
        emoji      = "✅"
        headline   = "HRV เมื่อคืนอยู่ในช่วงปกติ — ไม่มีสัญญาณ crash"
        advice     = []
    elif streak < 3:
        severity   = "watch"
        emoji      = "🟡"
        headline   = f"HRV เมื่อคืนต่ำกว่าช่วงปกติ {streak} คืนติด — watch mode"
        advice     = [
            "ลด intensity ลง 20% หากซ้อม quality",
            "นอนก่อน 22:00 คืนนี้",
            "ติดตามต่อ 1–2 วัน ก่อนตัดสินใจ",
        ]
    elif streak < 5:
        severity   = "crash"
        emoji      = "🔴"
        headline   = f"HRV CRASH — เมื่อคืนต่ำกว่าช่วงปกติ {streak} คืนติด"
        advice     = [
            "ยกเลิก Quality session ทั้งหมดจนกว่า HRV กลับ BALANCED",
            "วิ่ง Easy ≤ 6km HR < 140 หรือพักเต็ม วันเว้นวัน",
            "นอน ≥ 8h | หลีกเลี่ยงแอลกอฮอล์และคาเฟอีนบ่าย",
            "ตรวจ RHR เช้า — ถ้า >baseline+5 → REST เต็มวัน",
        ]
    else:
        severity   = "overreach"
        emoji      = "🚨"
        headline   = f"OVERREACHING ALARM — HRV ต่ำกว่าช่วงปกติ {streak} คืนติด (>5)"
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


rhr_baseline_fn = rhr_baseline   # main() shadows the name with a local


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

    rhr_baseline = rhr_baseline_fn()   # own 28-day window, independent of --days
    rhr_recent7  = [r.get("resting_hr") for r in records[-7:] if r.get("resting_hr")]
    rhr_recent7_avg = round(sum(rhr_recent7)/len(rhr_recent7), 1) if rhr_recent7 else None

    balanced_count = sum(1 for r in records if r.get("hrv_status") == "BALANCED")
    hrv_pct = round(balanced_count / len(records) * 100, 1) if records else 0

    print(f"📊 Baselines ({args.days}d):")
    print(f"   RHR baseline       : {rhr_baseline} bpm  (median {RHR_BASELINE_WINDOW} วันก่อนวันนี้ — ใช้ตัวเดียวกับ daily_brief)")
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
        if is_suppressed(r):
            unbalanced_streak += 1
        else:
            break

    rhr_elevated_days = sum(1 for r in last14
                            if r.get("resting_hr") and rhr_baseline
                            and r["resting_hr"] > rhr_baseline + 3)

    warn_flags = []
    if unbalanced_streak >= 3:
        warn_flags.append(f"🔴 HRV เมื่อคืนต่ำกว่าช่วงปกติ {unbalanced_streak} คืนติด → overreaching signal")
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
    print(f"   {'Date':<12} {'HRV 7d':<12} {'คืนนั้น':>8} {'ช่วงปกติ':>9} {'RHR':>5} {'BB↑':>5} {'Sleep':>6}")
    print("   " + "-" * 64)
    for r in records[-21:]:
        hrv = r.get("hrv_status") or "?"
        emoji = HRV_EMOJI.get(hrv, "❓")
        rhr = str(r.get("resting_hr") if r.get("resting_hr") is not None else "?")
        bb = str(r.get("bb_high") if r.get("bb_high") is not None else "?")
        sleep = str(r.get("sleep_score") if r.get("sleep_score") is not None else "?")
        ln = r.get("hrv_last_night")
        ns = night_hrv_state(r)
        ln_s = f"{ln}{'🟢' if ns == 'BALANCED' else '🔴' if ns == 'LOW' else '🟡'}" if ln is not None else "?"
        rng = f"{r.get('hrv_balanced_low')}–{r.get('hrv_balanced_high')}" if r.get("hrv_balanced_low") else "?"
        print(f"   {str(r['_date']):<12} {emoji}{hrv:<11} {ln_s:>8} {rng:>9} {rhr:>5} {bb:>5} {sleep:>6}")
    print("=" * 60)


if __name__ == "__main__":
    main()
