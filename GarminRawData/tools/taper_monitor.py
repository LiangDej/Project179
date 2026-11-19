#!/usr/bin/env python3
"""
taper_monitor.py — Race Week Taper Status Monitor

ตรวจสอบว่าช่วง Taper ทำถูกต้องไหม:
  - Volume ลดตาม taper profile ของ tier สนาม (readiness.TAPER_PROFILES — Bosquet 2007)
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

from __future__ import annotations

import sys
import json
import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR  = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
COACH_MCP = BASE_DIR.parent / "skills" / "garmin_coach_mcp"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from config import ATHLETE, DAY_NUTRITION  # noqa: E402
from readiness import TAPER_PROFILES, taper_profile  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Race Registry (sync with daily_brief.py)
# ---------------------------------------------------------------------------
# SINGLE SOURCE: races.json via race_registry (no hardcoded Fuji/dates).
try:
    from race_registry import load_races
    RACE_REGISTRY = {
        k: {"name": f"{r['name']} ({r['dist_km']:.0f}km)", "date": date.fromisoformat(r["date"]),
            "tier": r.get("tier", "A"), "taper_days": r.get("taper_days")}
        for k, r in load_races().items() if r.get("active", True)
    }
except Exception:
    print("❌ race_registry unavailable — check GarminRawData/races.json exists and is valid")
    print("   Fix: cp GarminRawData/races.example.json GarminRawData/races.json")
    sys.exit(1)

# BB targets for taper
BB_TARGETS = {
    "race_day_min":    75,  # BB เป้าหมายวันแข่ง
    "taper_daily_gain": 5,  # BB ควรขึ้นประมาณนี้ต่อวันที่พัก
}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def _get_nearest_race(race_key: str | None) -> tuple:
    """(race_name, race_date, taper profile) of the chosen / nearest upcoming race."""
    today = date.today()
    if race_key and RACE_REGISTRY.get(race_key.lower()):
        r = RACE_REGISTRY[race_key.lower()]
        return r["name"], r["date"], taper_profile(r)
    upcoming = [v for v in RACE_REGISTRY.values() if v["date"] >= today]
    if not upcoming:
        return None, None, None
    r = min(upcoming, key=lambda v: v["date"])
    return r["name"], r["date"], taper_profile(r)


def _weekly_km(weeks: int) -> dict:
    from activity_loader import load_activities_merged
    today = date.today()
    cutoff = today - timedelta(weeks=weeks)
    weekly: dict[date, float] = {}
    for a in load_activities_merged():
        try:
            dt = datetime.strptime((a.get("startTimeLocal") or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if cutoff <= dt <= today:
            mon = dt - timedelta(days=dt.weekday())
            weekly[mon] = weekly.get(mon, 0) + (a.get("distance") or 0) / 1000
    return weekly


def _get_week_mileage() -> float:
    """km this week (Mon–today) — shared activity loader (file + live)."""
    today = date.today()
    return round(_weekly_km(1).get(today - timedelta(days=today.weekday()), 0.0), 1)


def _get_peak_week_km() -> float | None:
    """Peak week km in the last 8 weeks; None if no data (no invented fallback)."""
    weekly = _weekly_km(8)
    peak = max(weekly.values()) if weekly else 0
    return round(peak, 1) if peak > 0 else None


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


def _get_taper_window(days_to_race: int, prof: dict) -> dict:
    """Volume window from the race's taper profile (same rules as the planner)."""
    if days_to_race > prof["window"]:
        return {"key": "far", "retain_pct": 1.0, "label": "Build Phase (not tapering yet)", "is_taper": False}
    if days_to_race <= prof["race_week"]:
        cut = float(prof["vol_race"].rstrip("%")) / 100
        return {"key": "race_week", "retain_pct": 1 - cut, "label": f"Race Week (-{prof['vol_race']})", "is_taper": True}
    cut = float(prof["vol_early"].rstrip("%")) / 100
    return {"key": "early", "retain_pct": 1 - cut, "label": f"Early Taper (-{prof['vol_early']})", "is_taper": True}


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def analyze_taper(days_to_race: int, bb: int | None, mileage_this_week: float,
                  peak_km: float | None, race_name: str, race_date: date,
                  prof: dict | None = None) -> dict:
    """คำนวณ taper compliance + BB projection + warnings"""
    prof = prof or TAPER_PROFILES["A"]
    window = _get_taper_window(days_to_race, prof)
    peak_km = peak_km or 0
    target_km  = round(peak_km * window["retain_pct"], 1)
    volume_ok  = mileage_this_week <= target_km * 1.10  # ±10% tolerance
    volume_pct = round(mileage_this_week / peak_km * 100, 1) if peak_km > 0 else 0

    warnings = []
    positives = []

    # --- Volume check — only meaningful during an actual taper window.
    # "far" (>15 days out) is a build phase; training_planner.py deliberately
    # ramps volume UP toward peak_km there, so no ceiling/compliance verdict
    # applies — just report where volume stands, without judging it.
    if days_to_race <= 0:
        warnings.append("🏁 วันแข่งถึงแล้ว! Good luck!")
    elif not peak_km:
        positives.append("ℹ️  ไม่มีข้อมูล peak week — ข้าม volume check")
    elif not window.get("is_taper", True):
        positives.append(f"ℹ️  Volume {mileage_this_week} km ({volume_pct}% ของ peak {peak_km} km) — ยังอยู่ช่วง build ไม่ใช่ taper")
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
    # Research taper (Bosquet 2007): keep intensity, cut volume. Quality is
    # dropped only inside the profile's no_quality window.
    if 0 < days_to_race <= prof["no_quality"]:
        positives.append("ℹ️  ช่วงท้าย taper — Easy + strides สั้นๆ เท่านั้น ไม่มี Quality")
    elif window.get("is_taper"):
        positives.append("ℹ️  Taper: คง Quality สั้นๆ (ความหนักเท่าเดิม) แต่ลดปริมาณ — ไม่เพิ่ม volume")

    if days_to_race == 1:
        positives.append("💡 พรุ่งนี้แข่ง: Shakeout 3km เบาๆ + Drills — ไม่ต้องวอร์มนาน")
        positives.append(f"💡 กินคาร์บวันนี้ให้เพียงพอ: {DAY_NUTRITION['carb_load']}")

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

    race_name, race_date, prof = _get_nearest_race(args.race)
    if race_date is None:
        print("❌ ไม่พบ upcoming race ใน RACE_REGISTRY")
        sys.exit(1)

    today          = date.today()
    days_to_race   = (race_date - today).days
    bb             = args.bb if args.bb is not None else _get_bb_from_cache()
    mileage        = args.mileage if args.mileage is not None else _get_week_mileage()
    peak_km        = args.peak_km if args.peak_km is not None else _get_peak_week_km()

    result = analyze_taper(days_to_race, bb, mileage, peak_km, race_name, race_date, prof)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
