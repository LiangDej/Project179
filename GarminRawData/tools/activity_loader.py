"""
activity_loader.py — Shared activity loader (file history + live Garmin overlay)

Single source for loading activities so that NO tool ever analyzes a stale
snapshot. Reads running_activities_all.json (full history) then overlays the
most recent activities pulled live from Garmin, deduped by activityId. If
Garmin is unreachable it falls back to the file silently.

Why this exists:
  ก่อนหน้านี้ tools อ่านไฟล์ตรงๆ ถ้าไฟล์ค้าง (ยังไม่ sync) → volume ขาด + ATL
  ต่ำเกิน → TSB สูงเกินจริง (เคยโชว์ TSB +24 ทั้งที่จริง ~0).

Two views (v1.8):
  load_activities_merged()  — RUNS only: volume, pace, form, VDOT, injury km.
  load_load_activities()    — runs + CARDIO cross-training (elliptical, bike,
      swim, row…): physiological load for PMC/ACWR. hrTSS is HR-based, so a
      50-min elliptical at HR 127 is real aerobic load; leaving it out made
      ATL/ACWR too low during the knee rehab block. Strength/walking excluded
      (HR-TSS misreads them). Cross-training history is cached to
      cross_training_all.json as it is seen live (the run file has none).
The live Garmin call is made once per process (memoized).
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
ACTS_FILE = BASE_DIR / "running_activities_all.json"
CROSS_FILE = BASE_DIR / "cross_training_all.json"
RUN_TYPES = {"running", "treadmill_running", "trail_running"}
CROSS_TYPES = {"elliptical", "indoor_cardio", "cycling", "indoor_cycling", "virtual_ride",
               "road_biking", "mountain_biking", "lap_swimming", "open_water_swimming",
               "indoor_rowing", "rowing", "stair_climbing"}

_LIVE_CACHE: dict = {}


def _live(limit: int) -> list:
    if limit not in _LIVE_CACHE:
        try:
            from garmin_client import get_client, garmin_get
            _LIVE_CACHE[limit] = garmin_get(get_client().get_activities, 0, limit) or []
        except Exception:
            _LIVE_CACHE[limit] = []   # Garmin unreachable → file-only (graceful)
    return _LIVE_CACHE[limit]


def _type(a: dict) -> str:
    return (a.get("activityType") or {}).get("typeKey", "")


def _read(path: Path) -> dict:
    out = {}
    if path.exists():
        try:
            for a in json.loads(path.read_text()):
                if a.get("activityId") is not None:
                    out[a["activityId"]] = a
        except Exception:
            pass
    return out


def load_activities_merged(limit: int = 80, allow_live: bool = True) -> list:
    """RUNNING activities = file history overlaid with live Garmin data."""
    acts = _read(ACTS_FILE)
    if allow_live:
        for a in _live(limit):
            if _type(a) in RUN_TYPES and a.get("activityId") is not None:
                acts[a["activityId"]] = a
    return list(acts.values())


def load_cross_training(limit: int = 80, allow_live: bool = True) -> list:
    """Cardio cross-training (cached file + live), persisted as it is seen."""
    cross = _read(CROSS_FILE)
    if allow_live:
        new = {a["activityId"]: a for a in _live(limit)
               if _type(a) in CROSS_TYPES and a.get("activityId") is not None}
        if any(k not in cross for k in new):
            cross.update(new)
            try:
                CROSS_FILE.write_text(json.dumps(sorted(cross.values(), key=lambda a: a.get("startTimeLocal", "")),
                                                 ensure_ascii=False))
            except Exception:
                pass
        else:
            cross.update(new)
    return list(cross.values())


def load_load_activities(limit: int = 80, allow_live: bool = True) -> list:
    """Runs + cardio cross-training — for training LOAD (PMC/ACWR) only."""
    return load_activities_merged(limit, allow_live) + load_cross_training(limit, allow_live)
