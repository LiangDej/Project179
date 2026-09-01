#!/usr/bin/env python3
"""
taper_monitor.py — Race Week Taper Status Monitor

ตรวจสอบว่าช่วง Taper ทำถูกต้องไหม:
  - Volume ลดตามเกณฑ์ JD (30% @ 7-14 วัน, 50% @ ≤7 วัน)
  - Body Battery กำลัง charge ขึ้นตามแผน
  - ไม่มี workout หนักเกินที่กำหนด
  - คาดการณ์ BB วันแข่ง

Usage:
    python3 taper_monitor.py                      # auto-detect จาก race dates + cache
    python3 taper_monitor.py --bb 65             # override BB วันนี้
    python3 taper_monitor.py --mileage 28        # override mileage สัปดาห์นี้
    python3 taper_monitor.py --peak-km 55.5      # override peak week km
    python3 taper_monitor.py --race sponsor21    # เลือก race (bangsaen หรือ sponsor21)
"""

import sys
import json
import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR  = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
COACH_MCP = BASE_DIR.parent / "skills" / "garmin_coach_mcp"
DATA_FILE = BASE_DIR / "running_activities_all.json"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from config import ATHLETE, TRAINING_PHASES, BASELINES  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Race Registry (sync with daily_brief.py)
# ---------------------------------------------------------------------------
# SINGLE SOURCE: races.json via race_registry (no hardcoded Fuji/dates).
try:
    from race_registry import load_races
    RACE_REGISTRY = {
        k: {"name": f"{r['name']} ({r['dist_km']:.0f}km)", "date": date.fromisoformat(r["date"])}
        for k, r in load_races().items() if r.get("active", True)
    }
except Exception:
    RACE_REGISTRY = {"atm": {"name": "ATM Bangkok Marathon (42km)", "date": date(2026, 11, 29)}}

# Taper volume targets (% of peak week to RETAIN — not cut)
TAPER_VOLUME = {
    "far":    {"days_min": 15, "days_max": 999, "retain_pct": 0.80, "label": "Pre-Taper"},
    "early":  {"days_min": 8,  "days_max": 14,  "retain_pct": 0.70, "label": "Early Taper (-30%)"},
    "late":   {"days_min": 4,  "days_max": 7,   "retain_pct": 0.50, "label": "Race Week (-50%)"},
    "final":  {"days_min": 0,  "days_max": 3,   "retain_pct": 0.30, "label": "Final Days (-70%)"},
}

# BB targets for taper
BB_TARGETS = {
    "race_day_min":    75,  # BB เป้าหมายวันแข่ง
    "taper_daily_gain": 5,  # BB ควรขึ้นประมาณนี้ต่อวันที่พัก
}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def _get_nearest_race(race_key: str | None) -> tuple[str, date] | tuple[None, None]:
    """คืน (race_name, race_date) ของ race ที่ใกล้ที่สุดในอนาคต"""
    today = date.today()
    if race_key:
        r = RACE_REGISTRY.get(race_key.lower())
        if r:
            return r["name"], r["date"]
    # Auto-select nearest upcoming
    upcoming = [(v["name"], v["date"]) for v in RACE_REGISTRY.values()
                if v["date"] >= today]
    if not upcoming:
        return None, None
    return min(upcoming, key=lambda x: x[1])


def _get_week_mileage() -> float:
    """คำนวณ mileage สัปดาห์นี้ (จันทร์–วันนี้) จาก local JSON cache"""
    if not DATA_FILE.exists():
        return 0.0
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            activities = json.load(f)
    except Exception:
        return 0.0

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    total = 0.0
    for a in activities:
        dt_str = a.get("startTimeLocal", "")[:10]
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if monday <= dt <= today:
            total += (a.get("distance") or 0) / 1000
    return round(total, 1)


def _get_peak_week_km() -> float:
    """
    คำนวณ peak week km จาก local JSON (สัปดาห์ที่มี volume สูงสุด ใน 8 สัปดาห์ล่าสุด)
    Fallback: 55.5 km (W18 Peak Week ที่บันทึกไว้)
    """
    FALLBACK = 55.5
    if not DATA_FILE.exists():
        return FALLBACK
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            activities = json.load(f)
    except Exception:
        return FALLBACK

    today = date.today()
    cutoff = today - timedelta(weeks=8)
    weekly: dict[date, float] = {}

    for a in activities:
        dt_str = a.get("startTimeLocal", "")[:10]
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dt < cutoff:
            continue
        monday = dt - timedelta(days=dt.weekday())
        weekly[monday] = weekly.get(monday, 0) + (a.get("distance") or 0) / 1000

    if not weekly:
        return FALLBACK
    peak = max(weekly.values())
    return round(peak, 1) if peak > 0 else FALLBACK


def _get_bb_from_cache() -> int | None:
    """ดึง Body Battery วันนี้จาก health cache"""
    try:
        from garmin_client import _load_health_cache
        today_str = date.today().strftime("%Y-%m-%d")
        cached = _load_health_cache(today_str)
        if cached:
            return cached.get("body_battery")
    except Exception:
        pass
    return None


def _get_taper_window(days_to_race: int) -> dict:
    for key, tw in TAPER_VOLUME.items():
        if tw["days_min"] <= days_to_race <= tw["days_max"]:
            return {**tw, "key": key}
    return {"key": "far", "retain_pct": 0.80, "label": "Pre-Taper",
            "days_min": 15, "days_max": 999}


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def analyze_taper(days_to_race: int, bb: int | None, mileage_this_week: float,
                  peak_km: float, race_name: str, race_date: date) -> dict:
    """คำนวณ taper compliance + BB projection + warnings"""

    window = _get_taper_window(days_to_race)
    target_km  = round(peak_km * window["retain_pct"], 1)
    volume_ok  = mileage_this_week <= target_km * 1.10  # ±10% tolerance
    volume_pct = round(mileage_this_week / peak_km * 100, 1) if peak_km > 0 else 0

    warnings = []
    positives = []

    # --- Volume check ---
    if days_to_race <= 0:
        warnings.append("🏁 วันแข่งถึงแล้ว! Good luck!")
    elif not volume_ok:
        over_by = round(mileage_this_week - target_km, 1)
        warnings.append(
            f"⚠️  Volume เกิน {over_by} km — เป้า ≤{target_km} km แต่วิ่งไปแล้ว {mileage_this_week} km สัปดาห์นี้"
        )
    else:
        positives.append(f"✅ Volume {mileage_this_week}/{target_km} km — taper compliance ดี")

    # --- BB analysis ---
    bb_race_projection = None
    if bb is not None:
        daily_gain = BB_TARGETS["taper_daily_gain"]
        projected_bb = min(100, bb + days_to_race * daily_gain)
        bb_race_projection = round(projected_bb)

        if bb < 40 and days_to_race > 3:
            warnings.append(
                f"🔴 BB {bb} ต่ำเกินไปสำหรับช่วง taper — อาจหมายถึงพักไม่เพียงพอ หรือ stress สะสม"
            )
        elif bb < 60 and days_to_race <= 3:
            warnings.append(
                f"🟡 BB {bb} — ควรถึง 70+ ก่อนแข่ง ลองนอนเพิ่ม 1-2 ชั่วโมง"
            )
        elif bb >= 70:
            positives.append(f"✅ BB {bb} — ร่างกายกำลัง charge ดี")
        else:
            positives.append(f"🟡 BB {bb} — ยังพอไปได้ ควรขึ้นอีกก่อนแข่ง")

        if bb_race_projection < BB_TARGETS["race_day_min"]:
            warnings.append(
                f"⚠️  BB คาดการณ์วันแข่ง: {bb_race_projection} "
                f"(เป้า ≥{BB_TARGETS['race_day_min']}) — นอนให้มากขึ้นช่วงนี้"
            )
        else:
            positives.append(
                f"✅ BB คาดการณ์วันแข่ง: {bb_race_projection} — อยู่ในเกณฑ์ดี"
            )
    else:
        warnings.append("⚠️  ไม่พบข้อมูล Body Battery — รัน daily_brief.py หรือดูจากนาฬิกาโดยตรง")

    # --- Taper-specific rules ---
    if days_to_race <= 7:
        warnings.append("🚫 ห้าม Quality Session ทุกชนิด — Easy Run + REST เท่านั้น")
        warnings.append("🚫 ห้ามเพิ่ม volume แม้รู้สึกว่าขายังสดเกินไป (taper madness!)")

    if days_to_race == 1:
        positives.append("💡 พรุ่งนี้แข่ง: Shakeout 3km เบาๆ + Drills — ไม่ต้องวอร์มนาน")
        positives.append("💡 กินคาร์บวันนี้ให้เพียงพอ: ข้าว + Palatinose ก่อนนอน")

    if days_to_race == 2:
        positives.append("💡 พรุ่งนี้: Easy 4km + 4×Strides — ตื่นขา ไม่สร้าง fatigue")

    # --- Overall status ---
    if len(warnings) == 0:
        status = "✅ ON TRACK"
        status_icon = "✅"
    elif any("🔴" in w for w in warnings):
        status = "🔴 AT RISK"
        status_icon = "🔴"
    else:
        status = "🟡 CAUTION"
        status_icon = "🟡"

    return {
        "race_name":           race_name,
        "race_date":           race_date.isoformat(),
        "days_to_race":        days_to_race,
        "taper_window":        window["label"],
        "peak_km":             peak_km,
        "target_km_this_week": target_km,
        "actual_km_this_week": mileage_this_week,
        "volume_pct_of_peak":  volume_pct,
        "bb_today":            bb,
        "bb_race_projection":  bb_race_projection,
        "status":              status,
        "status_icon":         status_icon,
        "warnings":            warnings,
        "positives":           positives,
    }


def print_report(r: dict):
    print(f"\n{'='*57}")
    print(f"⏱️  TAPER MONITOR — {r['race_name']}")
    print(f"   Race: {r['race_date']}  |  T-{r['days_to_race']} วัน")
    print(f"   Window: {r['taper_window']}")
    print(f"{'='*57}")

    print(f"\n📊 สถานะ: {r['status']}")

    print(f"\n📏 Volume:")
    print(f"   Peak Week Ref : {r['peak_km']} km")
    print(f"   Target (สัปดาห์นี้): ≤ {r['target_km_this_week']} km")
    print(f"   Actual (สัปดาห์นี้): {r['actual_km_this_week']} km  ({r['volume_pct_of_peak']}% of peak)")

    if r['bb_today'] is not None:
        print(f"\n🔋 Body Battery:")
        print(f"   วันนี้    : {r['bb_today']}")
        if r['bb_race_projection']:
            print(f"   คาดวันแข่ง: {r['bb_race_projection']}  (เป้า ≥{BB_TARGETS['race_day_min']})")

    if r['positives']:
        print(f"\n✨ Positives:")
        for p in r['positives']:
            print(f"   {p}")

    if r['warnings']:
        print(f"\n⚠️  Warnings / Action Items:")
        for w in r['warnings']:
            print(f"   {w}")

    print(f"\n{'='*57}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Taper Monitor — Race Week Compliance Check")
    parser.add_argument("--race",     choices=list(RACE_REGISTRY.keys()),
                        help="Race target (default: nearest upcoming)")
    parser.add_argument("--bb",       type=int, help="Override Body Battery วันนี้")
    parser.add_argument("--mileage",  type=float, help="Override mileage สัปดาห์นี้ (km)")
    parser.add_argument("--peak-km",  type=float, help="Override peak week km (default: auto-detect)")
    parser.add_argument("--json",     action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    race_name, race_date = _get_nearest_race(args.race)
    if race_date is None:
        print("❌ ไม่พบ upcoming race ใน RACE_REGISTRY")
        sys.exit(1)

    today          = date.today()
    days_to_race   = (race_date - today).days
    bb             = args.bb if args.bb is not None else _get_bb_from_cache()
    mileage        = args.mileage if args.mileage is not None else _get_week_mileage()
    peak_km        = args.peak_km if args.peak_km is not None else _get_peak_week_km()

    result = analyze_taper(days_to_race, bb, mileage, peak_km, race_name, race_date)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
