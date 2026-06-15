"""
activity_loader.py — Shared activity loader (file history + live Garmin overlay)

Single source for loading running activities so that NO tool ever analyzes a
stale snapshot again. Reads running_activities_all.json (full history) then
overlays the most recent activities pulled live from Garmin, deduped by
activityId. If Garmin is unreachable it falls back to the file silently.

Why this exists:
  ก่อนหน้านี้ training_load / weekly_load_report / vdot_estimator อ่านไฟล์ตรงๆ
  ถ้าไฟล์ค้าง (ยังไม่ sync) → volume ขาด + ATL ต่ำเกิน → TSB สูงเกินจริง
  (เคยทำ daily brief โชว์ TSB +24 ทั้งที่จริง ~0). ตัวนี้กันปัญหานั้นถาวร.

Running-only: live overlay กรองเฉพาะ run types ให้ตรงกับไฟล์ (ไม่เอา cycling ฯลฯ
มาปน TSS).
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
ACTS_FILE = BASE_DIR / "running_activities_all.json"
RUN_TYPES = {"running", "treadmill_running", "trail_running"}


def load_activities_merged(limit: int = 80, allow_live: bool = True) -> list:
    """Return running activities = file history overlaid with live Garmin data.

    Args:
        limit:      จำนวน activity ล่าสุดที่ดึง live มา overlay (พอครอบ gap ตั้งแต่ sync ล่าสุด)
        allow_live: False = อ่านไฟล์อย่างเดียว (offline mode)
    """
    acts: dict = {}

    # 1. File history (full)
    if ACTS_FILE.exists():
        try:
            for a in json.loads(ACTS_FILE.read_text()):
                aid = a.get("activityId")
                if aid is not None:
                    acts[aid] = a
        except Exception:
            pass

    # 2. Live overlay (recent runs not yet synced to file) — running-only
    if allow_live:
        try:
            from garmin_client import get_client, garmin_get
            client = get_client()
            for a in garmin_get(client.get_activities, 0, limit):
                tk = (a.get("activityType") or {}).get("typeKey", "")
                if tk in RUN_TYPES:
                    aid = a.get("activityId")
                    if aid is not None:
                        acts[aid] = a
        except Exception:
            # Garmin unreachable → file-only (graceful)
            pass

    return list(acts.values())
