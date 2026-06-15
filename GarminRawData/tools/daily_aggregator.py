#!/usr/bin/env python3
"""
daily_aggregator.py — Multi-Session Daily & Weekly Volume Aggregator

รวมการวิ่งหลาย session ในวันเดียว (เช้า + เย็น, outdoor + treadmill) เป็น
"daily total" ที่ถูกต้อง และนับ weekly volume ให้ครบทุก session
(แก้ปัญหา tool อื่นที่นับ volume ขาดเมื่อวิ่ง 2 รอบ/วัน).

────────────────────────────────────────────────────────────────────────────
METHODOLOGY (data-analytics correctness)
────────────────────────────────────────────────────────────────────────────
• Distance / time = ผลรวมตรงทุก session ในวันเดียวกัน (sum).
• Avg HR ของวัน = ค่าเฉลี่ยถ่วงน้ำหนักด้วย "เวลา" ของแต่ละ session
  (duration-weighted mean) — ไม่ใช่เฉลี่ยธรรมดา เพราะ session สั้น/ยาว
  ไม่ควรมีน้ำหนักเท่ากัน. นี่คือวิธีถูกต้องทางสถิติของการรวม rate metric.
• Combined pace = total_distance / total_moving_time (harmonic-correct,
  ไม่ใช่เฉลี่ย pace ของแต่ละ session ซึ่งจะ bias).
• Weekly volume = sum ของทุก session ในกรอบ (rolling N วัน หรือ ISO week).

ดึงข้อมูลจาก Garmin live (get_activities) overlay บน running_activities_all.json
เพื่อให้รวม run ล่าสุดที่ยังไม่ sync ลงไฟล์ (เป็นต้นเหตุที่ volume ขาด).

Usage:
    python3 daily_aggregator.py                 # 7 วันล่าสุด (daily totals)
    python3 daily_aggregator.py --days 14
    python3 daily_aggregator.py --week          # ISO week ปัจจุบัน + per-day
    python3 daily_aggregator.py --date 2026-06-07   # เจาะวันเดียว
"""

import sys
import json
import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
ACTS_FILE = BASE_DIR / "running_activities_all.json"
RUN_TYPES = {"running", "treadmill_running", "trail_running"}


def load_activities(limit=60):
    acts = {}
    if ACTS_FILE.exists():
        try:
            for a in json.loads(ACTS_FILE.read_text()):
                acts[a.get("activityId")] = a
        except Exception:
            pass
    try:
        from garmin_client import get_client, garmin_get
        client = get_client()
        for a in garmin_get(client.get_activities, 0, limit):
            acts[a.get("activityId")] = a
    except Exception as e:
        print(f"⚠️  live fetch ไม่ได้ ({e}) — ใช้ไฟล์ history อย่างเดียว")
    return list(acts.values())


def parse_runs(acts, cutoff):
    runs = []
    for a in acts:
        tk = (a.get("activityType") or {}).get("typeKey", "")
        if tk not in RUN_TYPES:
            continue
        ts = a.get("startTimeLocal", "")
        try:
            dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if dt < cutoff:
            continue
        runs.append({
            "dt": dt,
            "date": ts[:10],
            "tk": tk,
            "is_tm": tk == "treadmill_running",
            "km": (a.get("distance") or 0) / 1000,
            "moving_s": a.get("movingDuration") or a.get("duration") or 0,
            "hr": a.get("averageHR"),
            "id": a.get("activityId"),
        })
    runs.sort(key=lambda r: r["dt"])
    return runs


def fmt_dur(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    return f"{h}:{m:02d}" if h else f"{m}min"


def fmt_pace(km, sec):
    if km <= 0:
        return "-"
    p = (sec / 60) / km
    return f"{int(p)}:{int((p - int(p)) * 60):02d}/km"


def aggregate_day(runs):
    """Duration-weighted daily aggregate of same-day sessions."""
    total_km = sum(r["km"] for r in runs)
    total_s = sum(r["moving_s"] for r in runs)
    hr_num = sum((r["hr"] or 0) * r["moving_s"] for r in runs if r["hr"])
    hr_den = sum(r["moving_s"] for r in runs if r["hr"])
    wavg_hr = round(hr_num / hr_den) if hr_den else None
    return {
        "n": len(runs),
        "km": total_km,
        "moving_s": total_s,
        "wavg_hr": wavg_hr,
        "pace": fmt_pace(total_km, total_s),
    }


def print_day(date_str, runs, agg):
    multi = " 🔗 MULTI" if agg["n"] > 1 else ""
    print(f"\n📅 {date_str}  ({agg['n']} session{'s' if agg['n'] > 1 else ''}){multi}")
    if agg["n"] > 1:
        for r in runs:
            tag = "🏃‍♂️TM" if r["is_tm"] else "🏃outdoor"
            hr = f"HR {r['hr']:.0f}" if r["hr"] else "HR -"
            t = r["dt"].strftime("%H:%M")
            print(f"     {t} {tag:<10} {r['km']:.2f}km  {fmt_dur(r['moving_s'])}  {hr}")
        print(f"     {'─'*40}")
    hr = f"avgHR {agg['wavg_hr']}" if agg["wavg_hr"] else ""
    label = "  รวม" if agg["n"] > 1 else "     "
    print(f"   {label}: {agg['km']:.2f} km | {fmt_dur(agg['moving_s'])} | {agg['pace']} | {hr}")


def main():
    p = argparse.ArgumentParser(description="Multi-session daily & weekly aggregator")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--week", action="store_true", help="ISO week ปัจจุบัน")
    p.add_argument("--date", type=str, default=None, help="เจาะวันเดียว YYYY-MM-DD")
    args = p.parse_args()

    now = datetime.now()
    if args.week:
        # back to Monday of current ISO week
        monday = now - timedelta(days=now.weekday())
        cutoff = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        title = f"ISO WEEK {now.isocalendar()[1]} (จันทร์ {cutoff.date()} →)"
    elif args.date:
        cutoff = datetime.strptime(args.date, "%Y-%m-%d")
        title = f"DATE {args.date}"
    else:
        cutoff = now - timedelta(days=args.days)
        title = f"{args.days} วันล่าสุด"

    acts = load_activities()
    runs = parse_runs(acts, cutoff)
    if args.date:
        runs = [r for r in runs if r["date"] == args.date]

    by_day = defaultdict(list)
    for r in runs:
        by_day[r["date"]].append(r)

    print(f"\n{'='*56}")
    print(f"📊 DAILY AGGREGATOR — {title}")
    print(f"{'='*56}")
    if not runs:
        print("   (ไม่มีการวิ่งในช่วงนี้)")
        return

    total_km = 0.0
    total_s = 0
    multi_days = 0
    for date_str in sorted(by_day.keys()):
        day_runs = sorted(by_day[date_str], key=lambda r: r["dt"])
        agg = aggregate_day(day_runs)
        print_day(date_str, day_runs, agg)
        total_km += agg["km"]
        total_s += agg["moving_s"]
        if agg["n"] > 1:
            multi_days += 1

    print(f"\n{'─'*56}")
    print(f"🧮 TOTAL: {total_km:.1f} km | {fmt_dur(total_s)} | "
          f"{len(by_day)} วันที่วิ่ง | {len(runs)} sessions")
    if multi_days:
        print(f"   🔗 {multi_days} วันที่วิ่ง 2+ รอบ (รวมครบแล้ว — ไม่ตกหล่น)")
    print(f"{'='*56}")


if __name__ == "__main__":
    main()
