#!/usr/bin/env python3
"""
weekly_load_report.py — JD Analytics Toolkit (P2)
สรุป Training Load รายสัปดาห์: Volume, Easy/Quality Ratio, TSS
Usage:
    python3 weekly_load_report.py
    python3 weekly_load_report.py --weeks 4
"""

import json
import argparse
import sys
import logging
from pathlib import Path
from datetime import date, timedelta

BASE_DIR = Path(__file__).parent.parent
sys.path.append(str(BASE_DIR.parent / "skills" / "garmin_coach_mcp"))
sys.path.append(str(Path(__file__).parent))   # tools dir — for shared training_load import

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("weekly-load-report")

try:
    from garmin_client import get_client as get_garmin_client
except ImportError:
    get_garmin_client = None
    logger.warning("Could not import get_garmin_client from garmin_client.py — BB data unavailable")

from config import ATHLETE, ZONE_PCT, HR_ZONE_BOUNDS  # noqa: E402
from training_load import calc_hr_tss  # noqa: E402  — single source of truth for TSS

QUALITY_LOG  = BASE_DIR / "QualitySessionLog" / "sessions.json"
HEALTH_CACHE = Path.home() / ".config" / "garmin-coach" / "health_cache"


def load_activities():
    """File history + live Garmin overlay (กัน volume ขาดตอนไฟล์ยังไม่ sync) — shared loader."""
    from activity_loader import load_activities_merged
    return load_activities_merged()


def load_quality_map() -> dict:
    """คืน dict {date_str: zone} จาก sessions.json เพื่อ override HR classification"""
    if not QUALITY_LOG.exists():
        return {}
    try:
        with open(QUALITY_LOG) as f:
            raw = json.load(f)
        sessions = raw.get("sessions", []) if isinstance(raw, dict) else raw
        result = {}
        for s in sessions:
            if not isinstance(s, dict):
                continue
            d = s.get("date", "")[:10]
            stype = s.get("session_type", "").lower()
            if any(k in stype for k in ("threshold", "(t)")):
                result[d] = "T"
            elif any(k in stype for k in ("interval", "(i)", "repetition", "(r)")):
                result[d] = "I"
            elif any(k in stype for k in ("marathon", "(m)")):
                result[d] = "M"
            elif "easy" in stype or "long" in stype:
                result[d] = "E"
        return result
    except Exception:
        return {}


def get_zone(pct):
    if pct < ZONE_PCT["E"]: return "E"
    elif pct < ZONE_PCT["M"]: return "M"
    elif pct < ZONE_PCT["T"]: return "T"
    else: return "I"


def classify_activity(act, quality_map: dict | None = None):
    """ใช้ session_type จาก quality log ก่อน (แม่นกว่า avg HR) แล้วค่อย fallback ไป HR"""
    act_date = act.get("startTimeLocal", "")[:10]
    if quality_map and act_date in quality_map:
        return quality_map[act_date]
    avg_hr = act.get("averageHR", 0) or 0
    rhr = ATHLETE["rhr"]
    hrr = ATHLETE["hrr"]
    if avg_hr == 0:
        return "E"
    pct = (avg_hr - rhr) / hrr * 100
    return get_zone(pct)

# NOTE: TSS uses the shared calc_hr_tss() from training_load.py (TrainingPeaks
# threshold-based hrTSS) so weekly numbers match the PMC engine in daily_brief.

def analyze_week(acts, start, end, quality_map: dict | None = None):
    week_acts = [a for a in acts if start <= a.get("startTimeLocal", "")[:10] <= end]
    total_dist = sum(a.get("distance", 0) / 1000 for a in week_acts)
    total_dur  = sum(a.get("duration", 0) / 3600 for a in week_acts)
    total_tss  = sum(calc_hr_tss(a) for a in week_acts)

    zones = {"E": 0, "M": 0, "T": 0, "I": 0}
    for a in week_acts:
        z = classify_activity(a, quality_map)
        zones[z] += a.get("distance", 0) / 1000

    q_sessions = len([a for a in week_acts if classify_activity(a, quality_map) in ["T", "I"]])
    
    long_run = max(week_acts, key=lambda x: x.get("distance", 0), default=None)
    lr_avg_hr = long_run.get("averageHR", 0) if long_run else 0
    lr_max_hr = long_run.get("maxHR", 0) if long_run else 0
    lr_dist = long_run.get("distance", 0)/1000 if long_run else 0

    n = len(week_acts)
    return {
        "start": start, "end": end,
        "n_sessions": n,
        "total_km": round(total_dist, 1),
        "total_hours": round(total_dur, 1),
        "tss": round(total_tss, 0),
        "zones": zones,
        "q_sessions": q_sessions,
        "lr_dist": round(lr_dist, 1),
        "lr_avg_hr": int(lr_avg_hr),
        "lr_max_hr": int(lr_max_hr)
    }

def fetch_weekly_bb(client, start_date, end_date):
    """Read BB average for the week from health_cache (offline-first).
    Falls back to live Garmin API only if cache has no data for the week.
    """
    from datetime import datetime as _dt
    vals = []
    try:
        d = _dt.strptime(start_date, "%Y-%m-%d").date()
        end = _dt.strptime(end_date, "%Y-%m-%d").date()
        while d <= end:
            cache_file = HEALTH_CACHE / f"health_{d.isoformat()}.json"
            if cache_file.exists():
                try:
                    data = json.loads(cache_file.read_text())
                    bb = data.get("body_battery") or data.get("bb_high")
                    if bb:
                        vals.append(int(bb))
                except Exception:
                    pass
            d = d + timedelta(days=1)
    except Exception as e:
        logger.warning(f"health_cache read error: {e}")

    if vals:
        return int(sum(vals) / len(vals))

    # Fallback: live Garmin API (requires active connection)
    if client:
        try:
            bb_data = client.get_body_battery(start_date, end_date)
            if bb_data:
                api_vals = []
                for entry in bb_data:
                    for v in entry.get("bodyBatteryValuesArray", []):
                        api_vals.append(v[1])
                if api_vals:
                    return int(sum(api_vals) / len(api_vals))
        except Exception as e:
            logger.warning(f"Garmin API BB fallback failed: {e}")

    return "N/A"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=4, help="Number of weeks to show (default: 4)")
    args = parser.parse_args()

    acts = load_activities()
    quality_map = load_quality_map()
    today = date.today()

    client = None
    if get_garmin_client is not None:
        try:
            client = get_garmin_client()
        except Exception as e:
            logger.warning(f"Could not connect to Garmin — BB data will be skipped: {e}")

    print(f"\n{'='*75}")
    print(f"📆 WEEKLY LOAD REPORT — {today}")
    print(f"{'='*75}")

    weeks_data = []
    for w in range(args.weeks):
        # Monday of each week
        mon = today - timedelta(days=today.weekday()) - timedelta(weeks=w)
        sun = mon + timedelta(days=6)
        mon_str = mon.strftime("%Y-%m-%d")
        sun_str = sun.strftime("%Y-%m-%d")
        wd = analyze_week(acts, mon_str, sun_str, quality_map)
        weeks_data.append(wd)

        total = wd['total_km']
        z = wd['zones']
        z_dist = ""
        if total > 0:
            z_dist = f"E:{round(z['E']/total*100)}% M:{round(z['M']/total*100)}% T:{round(z['T']/total*100)}% I:{round(z['I']/total*100)}%"

        week_label = f"Week {mon.strftime('%d/%m')}–{sun.strftime('%d/%m')}"
        marker = " [CURRENT]" if w == 0 else ""
        
        bb_avg = "N/A"
        if w == 0 and client:
            bb_avg = fetch_weekly_bb(client, mon_str, sun_str)
            
        print(f"\n{week_label}{marker}")
        print(f"   🏃‍♂️ Volume: {wd['total_km']} km | {wd['total_hours']} hrs | TSS: {wd['tss']}")
        print(f"   📊 Zones:  {z_dist}")
        print(f"   🔥 Qual:   {wd['q_sessions']} sessions")
        print(f"   🏃‍♂️ Long:   {wd['lr_dist']} km (Avg HR {wd['lr_avg_hr']} / Max {wd['lr_max_hr']})")
        if w == 0:
            print(f"   🔋 BB Avg: {bb_avg}")

    # Trend summary
    if len(weeks_data) >= 2:
        cur, prev = weeks_data[0], weeks_data[1]
        delta = cur['total_km'] - prev['total_km']
        pct   = (delta / prev['total_km'] * 100) if prev['total_km'] > 0 else 0
        arrow = "↑" if delta > 0 else "↓"
        print(f"\n{'─'*75}")
        print(f"📈 Volume Change vs Last Week: {arrow}{abs(delta):.1f} km ({pct:+.1f}%)")

        # 10% rule check
        if pct > 10:
            print(f"⚠️  Volume เพิ่มขึ้น > 10% — เสี่ยง Overtraining! ลดลง")
        elif pct < -30:
            print(f"✅ Taper Volume ลดลงดีมาก ({pct:.1f}%)")
        else:
            print(f"✅ Volume เพิ่มขึ้นอยู่ในเกณฑ์ปลอดภัย")

        # Quality check
        if cur['q_sessions'] > 2:
            print(f"⚠️  Quality Sessions = {cur['q_sessions']} (เกิน 2 ครั้ง/สัปดาห์ — เสี่ยงบาดเจ็บ)")
        elif cur['q_sessions'] == 2:
            print(f"✅ Quality Sessions = 2 (เหมาะสม แต่ห้ามจัดติดกัน 2 วัน)")
            
        # Long Run HR Check — Z1 Easy ceiling from config (single source)
        if cur['lr_avg_hr'] > HR_ZONE_BOUNDS["Z1_E"][1]:
            print(f"⚠️  Long Run HR สูงเกินไป (Avg {cur['lr_avg_hr']} bpm) — ควรลด Pace ให้อยู่ใน Zone 1-2")
        elif cur['lr_avg_hr'] > 0:
            print(f"✅ Long Run HR อยู่ในเกณฑ์ที่ปลอดภัย")

    print(f"{'='*75}\n")

if __name__ == "__main__":
    main()
