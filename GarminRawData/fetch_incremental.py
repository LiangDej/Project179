"""
fetch_incremental.py — Incremental Garmin activity sync

ดึงเฉพาะ activities ใหม่/อัพเดตตั้งแต่ last sync
ใช้ shared garmin_client (rate limiting + retry + secure credentials)

Usage:
    python3 fetch_incremental.py               # ย้อนหลัง 7 วัน (default)
    python3 fetch_incremental.py --lookback-days 30
"""

import sys
import os
import json
import hashlib
import tempfile
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR      = Path(__file__).parent
TOOLS_DIR     = BASE_DIR / "tools"
DATA_FILE     = str(BASE_DIR / "running_activities_all.json")
CHECKSUM_FILE = DATA_FILE + ".sha256"

sys.path.insert(0, str(TOOLS_DIR))
from garmin_client import get_client, garmin_get  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File integrity helpers (keep as-is — these are good)
# ---------------------------------------------------------------------------
def _compute_checksum(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _save_checksum(filepath: str):
    checksum = _compute_checksum(filepath)
    with open(filepath + ".sha256", "w") as f:
        f.write(checksum)


def _verify_checksum(filepath: str) -> bool:
    sidecar = filepath + ".sha256"
    if not os.path.exists(sidecar):
        return True
    try:
        expected = Path(sidecar).read_text().strip()
        actual   = _compute_checksum(filepath)
        if actual != expected:
            logger.warning(
                f"⚠️  Checksum mismatch: {filepath}\n"
                f"   Expected: {expected}\n"
                f"   Actual:   {actual}\n"
                "   ไฟล์อาจถูกแก้ไขจากภายนอก"
            )
            return False
        return True
    except Exception as e:
        logger.warning(f"ตรวจสอบ checksum ไม่ได้: {e}")
        return True


def load_existing_data() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    _verify_checksum(DATA_FILE)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data_atomic(activities: list):
    """Atomic write + checksum update"""
    activities.sort(key=lambda x: x.get("startTimeLocal", ""), reverse=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(BASE_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(activities, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, DATA_FILE)
        _save_checksum(DATA_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------
def fetch_and_sync(lookback_days: int):
    existing      = load_existing_data()
    existing_dict = {a.get("activityId"): a for a in existing}

    today      = datetime.now().date()
    start_date = today - timedelta(days=lookback_days)

    logger.info(f"📂 Loaded {len(existing)} existing activities")
    logger.info(f"🔄 Syncing {start_date} → {today} ({lookback_days} days lookback)")

    # shared client — credentials + session managed centrally
    client = get_client()

    new_activities = garmin_get(
        client.get_activities_by_date,
        start_date.isoformat(), today.isoformat(), "running"
    )

    if not new_activities:
        logger.info("✅ No new activities in range")
        return

    truly_new = updated = 0
    for a in new_activities:
        act_id = a.get("activityId")
        if act_id not in existing_dict:
            truly_new += 1
        else:
            updated += 1
        existing_dict[act_id] = a

    logger.info(f"🏃 {len(new_activities)} activities in range — New: {truly_new}, Updated: {updated}")

    if truly_new == 0 and updated == 0:
        logger.info("✅ Nothing changed")
        return

    merged = list(existing_dict.values())
    save_data_atomic(merged)
    logger.info(f"✅ Saved {truly_new} new activities | Total: {len(merged)} → {DATA_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Incremental Garmin activity sync")
    parser.add_argument("--lookback-days", type=int, default=7,
                        help="Number of days to look back (default: 7)")
    args = parser.parse_args()
    fetch_and_sync(args.lookback_days)
