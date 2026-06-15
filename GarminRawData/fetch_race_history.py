"""
fetch_race_history.py — ดึง full activity details สำหรับ race activities

ใช้ shared garmin_client (rate limiting + retry + secure credentials)
"""

import json
import sys
import logging
from pathlib import Path

# --- Paths ---
BASE_DIR   = Path(__file__).parent
TOOLS_DIR  = BASE_DIR / "tools"
OUTPUT_DIR = BASE_DIR / "RaceHistory"
OUTPUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(TOOLS_DIR))
from garmin_client import get_client, garmin_get  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# --- Target Race Activities ---
TARGET_ACTIVITIES = {
    "HM_Nov2025":       21125780952,
    "HM_PB_Dec2025":    21309788694,
    "FM_Osaka_Feb2026": 21944583478,
}


def fetch_race_details():
    client = get_client()

    for name, act_id in TARGET_ACTIVITIES.items():
        file_path = OUTPUT_DIR / f"{name}_{act_id}.json"

        if file_path.exists():
            logger.info(f"⏭️  Already cached: {name}")
            continue

        logger.info(f"🚀 Fetching: {name} (ID: {act_id})")
        try:
            details = garmin_get(client.get_activity_details, act_id)
            tmp = file_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(details, indent=2, ensure_ascii=False))
            tmp.rename(file_path)
            logger.info(f"✅ Saved: {file_path.name}")
        except Exception as e:
            logger.error(f"❌ Failed {name}: {e}")


if __name__ == "__main__":
    fetch_race_details()
