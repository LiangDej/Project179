#!/usr/bin/env python3
"""backfill_hrv_nightly.py — one-off (v1.7.2): add last-night HRV numbers
(hrv_last_night / hrv_weekly_avg / hrv_balanced_low / hrv_balanced_high) to
existing wellness/wellness_YYYY-MM-DD.json files.

New days get these at sync time (garmin_client._extract_wellness_snapshot);
this only fills older files so hrv_trend's crash streak can use them.
Only touches files that already exist and lack hrv_last_night.

Usage (from GarminRawData/tools, venv python):
  ../../.venv/bin/python3.13 ../backfill_hrv_nightly.py --days 35 --dry-run
  ../../.venv/bin/python3.13 ../backfill_hrv_nightly.py --days 35
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, str(HERE.parent / "skills" / "garmin_coach_mcp"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import garmin_client as gc

    client = None
    done = 0
    for i in range(args.days, -1, -1):
        ds = (date.today() - timedelta(days=i)).isoformat()
        master = gc._load_wellness_day(ds)
        if not master or master.get("hrv_last_night") is not None:
            continue
        client = client or gc.get_client()
        try:
            hrv = gc.garmin_get(client.get_hrv_data, ds) or {}
        except Exception as e:
            print(f"skip {ds}: {e}")
            continue
        nums = gc.hrv_numbers(hrv.get("hrvSummary") or {})
        if nums["hrv_last_night"] is None:
            print(f"skip {ds}: no nightly HRV from Garmin")
            continue
        print(f"{ds}: last night {nums['hrv_last_night']} ms | 7d {nums['hrv_weekly_avg']} | "
              f"range {nums['hrv_balanced_low']}–{nums['hrv_balanced_high']}")
        master.update(nums)
        for snap in master.get("snapshots", []):
            snap.update(nums)
        if not args.dry_run:
            gc._save_wellness_day(ds, master)
        done += 1
    print(f"{'[dry-run] ' if args.dry_run else '✅ '}{done} day(s) {'would be ' if args.dry_run else ''}updated")


if __name__ == "__main__":
    main()
