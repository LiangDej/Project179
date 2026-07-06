"""
fetch_wellness.py — Garmin wellness master data sync (thin CLI)

Data access อยู่ใน garmin_client (DA layer) — ไฟล์นี้เป็นแค่ driver:
  1. เรียก garmin_client.sync_wellness_day() → fetch + เขียน wellness master
  2. backfill bb_end ใน sessions_master.json จาก bb_low (post-run reconciliation)

SINGLE SOURCE OF TRUTH = GarminRawData/wellness/wellness_YYYY-MM-DD.json
(health_cache เดิมถูกยกเลิก — ทุก tool อ่าน wellness master ตัวเดียวผ่าน garmin_client)

Usage:
    python3 fetch_wellness.py                   # วันนี้
    python3 fetch_wellness.py --date 2026-06-21 # วันที่ระบุ
    python3 fetch_wellness.py --days 7          # ย้อนหลัง 7 วัน
"""

import sys
import json
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR        = Path(__file__).parent
TOOLS_DIR       = BASE_DIR / "tools"
SESSIONS_MASTER = BASE_DIR / "QualitySessionLog" / "sessions_master.json"
ACTS_FILE       = BASE_DIR / "running_activities_all.json"

sys.path.insert(0, str(TOOLS_DIR))
from garmin_client import get_client, sync_wellness_day  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _post_run_bb(master: dict, end_iso: str) -> int | None:
    """body_battery ของ snapshot แรกที่เก็บ ≥ เวลาวิ่งจบ (ค่า BB จริงหลังวิ่ง)

    ไม่ใช้ bb_low (day nadir) เพราะจุดต่ำสุดของวันมักเกิดตอนเย็น ไม่ใช่ตอนวิ่งจบ
    """
    end = datetime.fromisoformat(end_iso)
    post = []
    for s in master.get("snapshots", []):
        if s.get("body_battery") is None:
            continue
        try:
            if datetime.fromisoformat(s["snapshot_at"]) >= end:
                post.append((s["snapshot_at"], s["body_battery"]))
        except Exception:
            pass
    if not post:
        return None
    post.sort(key=lambda x: x[0])
    return post[0][1]  # snapshot แรกหลังวิ่งจบ


def _backfill_bb_end(date_str: str, master: dict):
    """อัปเดต bb_end ใน sessions_master.json = BB จริงหลังวิ่งจบ (post-run snapshot)

    post_session_analyzer capture bb_end ตอน runtime — Garmin อาจยังคำนวณ BB ไม่เสร็จ
    ทำให้ได้ค่าผิด. แหล่งที่ถูก (เรียงตามความแม่น):
      1. snapshot แรกหลังเวลาวิ่งจบ (จาก wellness)
      2. fallback: bb_start + differenceBodyBattery (Garmin's official per-activity BB cost)
    """
    if not SESSIONS_MASTER.exists():
        return
    try:
        sessions = json.loads(SESSIONS_MASTER.read_text())
        acts = {a.get("activityId"): a for a in json.loads(ACTS_FILE.read_text())} if ACTS_FILE.exists() else {}
        updated = 0
        for s in sessions.get("sessions", []):
            if s.get("date") != date_str:
                continue
            act = acts.get(s.get("activity_id"))
            new_end = None
            if act and act.get("startTimeLocal") and act.get("duration"):
                start = datetime.fromisoformat(act["startTimeLocal"])
                end_iso = (start + timedelta(seconds=act["duration"])).isoformat()
                new_end = _post_run_bb(master, end_iso)
            # fallback: bb_start + differenceBodyBattery
            if new_end is None and act and s.get("bb_start") is not None:
                diff = act.get("differenceBodyBattery")
                if diff is not None:
                    new_end = s["bb_start"] + diff
            if new_end is not None and new_end != s.get("bb_end"):
                s["bb_end"] = new_end
                updated += 1
        if updated:
            SESSIONS_MASTER.write_text(json.dumps(sessions, ensure_ascii=False, indent=2))
            logger.info(f"  📋 Backfilled bb_end (post-run) for {updated} session(s) on {date_str}")
    except Exception as e:
        logger.warning(f"bb_end backfill failed: {e}")


def sync_day(client, date_str: str):
    master = sync_wellness_day(client, date_str)
    _backfill_bb_end(date_str, master)
    logger.info(
        f"✅ {date_str} — BB {master.get('bb_high')}↓{master.get('bb_low')} | "
        f"now {master.get('bb_latest')} | morning={master.get('bb_morning')} | "
        f"{len(master.get('snapshots', []))} snapshot(s)"
    )


def main():
    parser = argparse.ArgumentParser(description="Garmin wellness master data sync")
    parser.add_argument("--date", help="วันที่ YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=1, help="ย้อนหลัง N วัน")
    args = parser.parse_args()

    client = get_client()

    if args.date:
        sync_day(client, args.date)
    else:
        today = datetime.now().date()
        for i in range(args.days):
            sync_day(client, (today - timedelta(days=i)).isoformat())


if __name__ == "__main__":
    main()
