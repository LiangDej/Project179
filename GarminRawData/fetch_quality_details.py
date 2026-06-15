"""
fetch_quality_details.py — ดึง time-series metrics สำหรับ Quality Run sessions

ใช้ shared garmin_client (rate limiting + retry + secure credentials)
"""

import json
import sys
import logging
from pathlib import Path

# --- Paths ---
BASE_DIR   = Path(__file__).parent
TOOLS_DIR  = BASE_DIR / "tools"
DATA_FILE  = BASE_DIR / "running_activities_all.json"
OUTPUT_DIR = BASE_DIR / "QualityDetails"
OUTPUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(TOOLS_DIR))
from garmin_client import get_client, garmin_get  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# --- Target Dates (Quality Runs) ---
TARGET_DATES = [
    "2026-03-01", "2026-03-17", "2026-03-19", "2026-03-25",
    "2026-04-01", "2026-04-10", "2026-04-16", "2026-04-22",
    "2026-05-07", "2026-05-10",
]


def fetch_quality_details():
    # 1. Load activities to find IDs
    if not DATA_FILE.exists():
        logger.error(f"{DATA_FILE} not found — รัน fetch_incremental.py ก่อน")
        return

    with open(DATA_FILE, "r") as f:
        activities = json.load(f)

    date_to_id = {
        act["startTimeLocal"][:10]: act["activityId"]
        for act in activities
        if act.get("startTimeLocal", "")[:10] in TARGET_DATES
    }
    logger.info(f"🔍 Found {len(date_to_id)}/{len(TARGET_DATES)} target activities")

    # 2. Get shared client (tokenstore + secure credentials)
    client = get_client()

    # 3. Fetch details one by one — rate limiter ใน garmin_get จัดการ delay ให้
    for date_str, act_id in sorted(date_to_id.items()):
        file_path = OUTPUT_DIR / f"quality_{date_str}_{act_id}.json"

        if file_path.exists():
            logger.info(f"⏭️  Already cached: {date_str} ({act_id})")
            continue

        logger.info(f"🚀 Fetching: {date_str} (ID: {act_id})")
        try:
            details = garmin_get(client.get_activity_details, act_id)
            # atomic write
            tmp = file_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(details, indent=2, ensure_ascii=False))
            tmp.rename(file_path)
            logger.info(f"✅ Saved: {file_path.name}")
        except Exception as e:
            logger.error(f"❌ Failed {date_str}: {e}")


if __name__ == "__main__":
    fetch_quality_details()
