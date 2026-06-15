"""
garmin_client.py — Shared Garmin Connect Client Factory

Single source of truth สำหรับทุก script ที่ต้องการดึงข้อมูลจาก Garmin:
  - fetch_incremental.py
  - fetch_quality_details.py
  - fetch_race_history.py
  - server.py (MCP)
  - post_session_analyzer.py

Features:
  ✅ Secure credential loading (ไม่ fallback ไป project dir เด็ดขาด)
  ✅ Consistent tokenstore directory approach (garth OAuth)
  ✅ Exponential backoff + jitter สำหรับ HTTP 429
  ✅ Rate limiter — minimum delay ระหว่าง API calls
  ✅ Session auto-refresh เมื่อ token หมดอายุ
  ✅ Fail-fast พร้อม human-readable error message
  ✅ Health Data Cache — BB/HRV/RHR cached per-date, TTL 2 ชั่วโมง
  ✅ Offline mode — ใช้ cache แทน live API เมื่อ Garmin ไม่พร้อม

Usage:
    from tools.garmin_client import get_client, garmin_get, get_health_cached

    client = get_client()
    result = garmin_get(client.get_user_summary, "2026-05-09")

    # Offline-safe health data
    health = get_health_cached(client, "2026-05-10")
"""

import os
import sys
import json
import time
import logging
import random
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SECURE_ENV_PATH = Path.home() / ".config" / "garmin-coach" / ".env"
SESSION_DIR     = Path.home() / ".config" / "garmin-coach"

# Rate limiting
_MIN_CALL_INTERVAL = 1.0      # วินาที — ขั้นต่ำระหว่าง API calls
_last_call_time: float = 0.0  # module-level timestamp ของ call ล่าสุด

# Retry / backoff
_MAX_RETRIES   = 4
_BASE_DELAY    = 2.0   # วินาที (จะ double ทุก retry)
_MAX_DELAY     = 60.0  # วินาที cap

# Cached client (module-level singleton)
_client = None

# Health Data Cache
HEALTH_CACHE_DIR = SESSION_DIR / "health_cache"
HEALTH_CACHE_TTL = 2 * 3600  # 2 ชั่วโมง (วินาที)


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------
def _load_credentials() -> tuple[str, str]:
    """
    โหลด GARMIN_USERNAME / GARMIN_PASSWORD จาก secure path เท่านั้น
    ไม่มี fallback ไป project directory (ป้องกัน credential leak ใน Google Drive sync)

    Raises:
        FileNotFoundError: ถ้าไม่พบ env file ที่ secure path
        ValueError: ถ้า credentials ว่างเปล่า
    """
    if not SECURE_ENV_PATH.exists():
        raise FileNotFoundError(
            f"\n❌ Credential file not found: {SECURE_ENV_PATH}\n\n"
            "  Setup:\n"
            "    mkdir -p ~/.config/garmin-coach\n"
            "    cp AntiGravity/skills/garmin_coach_mcp/.env.example ~/.config/garmin-coach/.env\n"
            "    # แล้วใส่ username/password ใน ~/.config/garmin-coach/.env\n"
        )

    load_dotenv(dotenv_path=SECURE_ENV_PATH, override=False)

    username = os.environ.get("GARMIN_USERNAME", "").strip()
    password = os.environ.get("GARMIN_PASSWORD", "").strip()

    if not username or not password:
        raise ValueError(
            f"❌ GARMIN_USERNAME หรือ GARMIN_PASSWORD ว่างเปล่าใน {SECURE_ENV_PATH}\n"
            "  กรุณาตรวจสอบไฟล์ credential"
        )

    return username, password


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
def _rate_limit():
    """Sleep ถ้า call ถี่เกินไป (< _MIN_CALL_INTERVAL วินาทีจาก call ก่อน)"""
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < _MIN_CALL_INTERVAL:
        sleep_for = _MIN_CALL_INTERVAL - elapsed
        logger.debug(f"Rate limit: sleeping {sleep_for:.2f}s")
        time.sleep(sleep_for)
    _last_call_time = time.monotonic()


# ---------------------------------------------------------------------------
# Retry wrapper with exponential backoff
# ---------------------------------------------------------------------------
def garmin_get(fn, *args, **kwargs):
    """
    เรียก Garmin API function พร้อม retry + exponential backoff สำหรับ 429

    Args:
        fn: method ของ Garmin client (เช่น client.get_user_summary)
        *args, **kwargs: arguments สำหรับ fn

    Returns:
        ผลลัพธ์จาก fn

    Raises:
        Exception: ถ้า retry ครบแล้วยังล้มเหลว
    """
    global _client

    for attempt in range(_MAX_RETRIES + 1):
        try:
            _rate_limit()
            return fn(*args, **kwargs)

        except Exception as e:
            err_str = str(e).lower()

            # 429 Too Many Requests
            if "429" in err_str or "too many requests" in err_str or "rate limit" in err_str:
                if attempt >= _MAX_RETRIES:
                    logger.error(f"429 Rate limit: ล้มเหลวหลัง {_MAX_RETRIES} retries")
                    raise

                delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                jitter = random.uniform(0, delay * 0.2)  # ±20% jitter
                wait   = delay + jitter

                logger.warning(
                    f"⚠️  Garmin 429 Rate Limit (attempt {attempt+1}/{_MAX_RETRIES}). "
                    f"Waiting {wait:.1f}s before retry..."
                )
                time.sleep(wait)
                continue

            # 401 / session expired — ลอง re-login ครั้งเดียว
            if ("401" in err_str or "unauthorized" in err_str or
                    "session" in err_str or "token" in err_str):
                if attempt == 0:
                    logger.warning("🔄 Session หมดอายุ — กำลัง re-login...")
                    _client = None  # force fresh login
                    _client = _make_client()
                    fn = getattr(_client, fn.__name__)  # rebind method
                    continue

            # Error อื่นๆ — ไม่ retry
            raise


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------
def _make_client():
    """สร้าง Garmin client ใหม่พร้อม tokenstore directory approach"""
    try:
        from garminconnect import Garmin
    except ImportError:
        raise ImportError(
            "❌ garminconnect library ไม่พบ\n"
            "  pip install garminconnect --break-system-packages"
        )

    username, password = _load_credentials()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    client = Garmin(username, password)

    # พยายาม restore session token ก่อน (ลด login calls)
    try:
        client.login(tokenstore=str(SESSION_DIR))
        logger.info("✅ Garmin session restored from tokenstore")
        return client
    except Exception as e:
        logger.info(f"Session restore ไม่ได้ ({e}) — กำลัง fresh login...")

    # Fresh login
    try:
        client.login()
        logger.info("✅ Garmin fresh login สำเร็จ")
    except Exception as e:
        raise RuntimeError(
            f"❌ Garmin login ล้มเหลว: {e}\n"
            "  ตรวจสอบ username/password ใน ~/.config/garmin-coach/.env"
        )

    # บันทึก token ลง tokenstore directory
    try:
        client.garth.dump(str(SESSION_DIR))
        logger.info(f"💾 Session token บันทึกที่ {SESSION_DIR}")
    except Exception as e:
        logger.warning(f"⚠️  บันทึก session token ไม่ได้: {e}")

    return client


def get_client():
    """
    คืน singleton Garmin client (สร้างใหม่ถ้ายังไม่มี หรือถ้าถูก invalidate)

    Returns:
        Garmin client instance ที่พร้อมใช้งาน
    """
    global _client
    if _client is None:
        _client = _make_client()
    return _client


def invalidate_session():
    """Force re-login ครั้งถัดไป (เรียกเมื่อพบ 401 หรือ session error)"""
    global _client
    _client = None
    logger.info("Session invalidated — จะ re-login ในครั้งถัดไป")


# ---------------------------------------------------------------------------
# Health Data Cache  (BB / HRV / RHR / Sleep)
# ---------------------------------------------------------------------------
def _health_cache_path(date_str: str) -> Path:
    HEALTH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return HEALTH_CACHE_DIR / f"health_{date_str}.json"


def _cache_is_fresh(path: Path) -> bool:
    """True ถ้า cache file มีอายุน้อยกว่า TTL"""
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < HEALTH_CACHE_TTL


def _save_health_cache(date_str: str, data: dict):
    path = _health_cache_path(date_str)
    # Atomic write — prevents corruption on crash mid-write
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, path)
    logger.debug(f"💾 Health cache saved: {path.name}")


def _load_health_cache(date_str: str) -> dict | None:
    path = _health_cache_path(date_str)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def get_health_cached(client, date_str: str, force_refresh: bool = False) -> dict:
    """
    ดึง BB / HRV / RHR / Sleep สำหรับวันที่กำหนด
    — ใช้ cache ถ้ายังสด (TTL 2 ชั่วโมง) เพื่อลด API calls
    — ถ้า API ไม่พร้อม (429/network error) ใช้ cache เก่าแทนโดยอัตโนมัติ

    Args:
        client:        Garmin client จาก get_client()
        date_str:      "YYYY-MM-DD"
        force_refresh: True = bypass cache, เรียก API ใหม่เสมอ

    Returns:
        dict ที่มี keys: date, body_battery, hrv_status, resting_hr,
                         sleep_score, stress_avg, _source ("live" | "cache" | "unavailable")
    """
    path = _health_cache_path(date_str)

    # ใช้ cache ถ้าสดพอและไม่ได้ force refresh
    if not force_refresh and _cache_is_fresh(path):
        data = _load_health_cache(date_str)
        if data:
            data["_source"] = "cache"
            logger.debug(f"📦 Health cache HIT: {date_str}")
            return data

    # พยายามดึงจาก API
    try:
        summary = garmin_get(client.get_user_summary, date_str)
        hrv_raw = None
        try:
            hrv_raw = garmin_get(client.get_hrv_data, date_str)
        except Exception as e:
            logger.warning(f"HRV unavailable: {e}")

        sleep_raw = None
        try:
            sleep_raw = garmin_get(client.get_sleep_data, date_str)
        except Exception as e:
            logger.warning(f"Sleep data unavailable: {e}")

        hrv_status = "Unknown"
        if hrv_raw:
            try:
                hrv_status = hrv_raw.get("hrvSummary", {}).get("status", "Unknown")
            except Exception:
                pass

        sleep_score = None
        sleep_deep_min = sleep_rem_min = sleep_light_min = sleep_awake_min = None
        resp_avg = resp_low = resp_high = None
        spo2_avg = spo2_low = None
        if sleep_raw:
            try:
                dto = sleep_raw.get("dailySleepDTO", {}) or {}
                sleep_score = dto.get("sleepScores", {}).get("overall", {}).get("value")
                # Sleep stages (seconds → minutes)
                if dto.get("deepSleepSeconds") is not None:
                    sleep_deep_min  = round(dto["deepSleepSeconds"] / 60, 1)
                if dto.get("remSleepSeconds") is not None:
                    sleep_rem_min   = round(dto["remSleepSeconds"] / 60, 1)
                if dto.get("lightSleepSeconds") is not None:
                    sleep_light_min = round(dto["lightSleepSeconds"] / 60, 1)
                if dto.get("awakeSleepSeconds") is not None:
                    sleep_awake_min = round(dto["awakeSleepSeconds"] / 60, 1)
                # Respiration (breaths/min) — HRV crash predictor
                resp_avg  = dto.get("averageRespirationValue")
                resp_low  = dto.get("lowestRespirationValue")
                resp_high = dto.get("highestRespirationValue")
                # SpO2 (%) — altitude prep baseline
                spo2_avg  = dto.get("averageSpO2Value")
                spo2_low  = dto.get("lowestSpO2Value")
            except Exception as e:
                logger.debug(f"Sleep extras parse error: {e}")

        data = {
            "date":          date_str,
            "body_battery":  summary.get("bodyBatteryMostRecentValue"),
            "bb_high":       summary.get("bodyBatteryHighestValue"),
            "bb_low":        summary.get("bodyBatteryLowestValue"),
            "hrv_status":    hrv_status,
            "resting_hr":    summary.get("restingHeartRate"),
            "stress_avg":    summary.get("averageStressLevel"),
            "sleep_score":   sleep_score,
            # Sleep stages (minutes) — Plan A Tier 1
            "sleep_deep_min":  sleep_deep_min,
            "sleep_rem_min":   sleep_rem_min,
            "sleep_light_min": sleep_light_min,
            "sleep_awake_min": sleep_awake_min,
            # Respiratory rate (breaths/min) — HRV crash early warning
            "respiratory_avg":  resp_avg,
            "respiratory_low":  resp_low,
            "respiratory_high": resp_high,
            # SpO2 (%) — Fuji altitude baseline
            "spo2_avg": spo2_avg,
            "spo2_low": spo2_low,
            "fetched_at":    datetime.now().isoformat(),
            "_source":       "live",
        }
        _save_health_cache(date_str, data)
        logger.info(f"✅ Health data fetched live: {date_str}")
        return data

    except Exception as e:
        logger.warning(f"⚠️  API ไม่พร้อม ({e}) — ลอง fallback cache...")

        # Fallback: ใช้ cache เก่า แม้จะหมด TTL แล้ว
        stale = _load_health_cache(date_str)
        if stale:
            age_h = (time.time() - path.stat().st_mtime) / 3600
            stale["_source"] = f"cache_stale ({age_h:.1f}h ago)"
            logger.warning(f"📦 Using stale cache ({age_h:.1f}h old): {date_str}")
            return stale

        # ไม่มี cache เลย — คืน skeleton ที่บอกว่าไม่มีข้อมูล
        logger.error(f"❌ ไม่มีข้อมูล health สำหรับ {date_str} ทั้ง live และ cache")
        return {
            "date":        date_str,
            "body_battery": None,
            "hrv_status":  "Unknown",
            "resting_hr":  None,
            "stress_avg":  None,
            "sleep_score": None,
            "_source":     "unavailable",
        }


# ---------------------------------------------------------------------------
# Lap / Split Cache  (per activityId, TTL 7 วัน — historical data ไม่เปลี่ยน)
# ---------------------------------------------------------------------------
LAP_CACHE_DIR = SESSION_DIR / "lap_cache"
LAP_CACHE_TTL = 7 * 24 * 3600  # 7 วัน


def _lap_cache_path(activity_id: int) -> Path:
    LAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return LAP_CACHE_DIR / f"laps_{activity_id}.json"


def _lap_cache_is_fresh(activity_id: int) -> bool:
    path = _lap_cache_path(activity_id)
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < LAP_CACHE_TTL


def _save_lap_cache(activity_id: int, data: dict):
    path = _lap_cache_path(activity_id)
    # Atomic write — prevents corruption on crash mid-write
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, path)
    logger.debug(f"💾 Lap cache saved: {path.name}")


def _load_lap_cache(activity_id: int) -> dict | None:
    path = _lap_cache_path(activity_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def get_laps_cached(client, activity_id: int, force_refresh: bool = False) -> dict:
    """
    ดึง lap / split data สำหรับ activity ที่กำหนด
    — ใช้ cache TTL 7 วัน (historical data ไม่เปลี่ยน)
    — ถ้า API ไม่พร้อม ใช้ cache เก่า (stale) แทน
    — ถ้าไม่มี cache เลย คืน skeleton พร้อม _source="unavailable"

    Args:
        client:        Garmin client จาก get_client()
        activity_id:   Garmin activity ID (int)
        force_refresh: True = bypass cache

    Returns:
        dict ที่มี keys: activity_id, laps (list), _source
        แต่ละ lap: lapIndex, distance_m, duration_s, avg_hr, max_hr,
                   avg_speed_ms, avg_pace_sec_km, is_manual_lap
    """
    if not force_refresh and _lap_cache_is_fresh(activity_id):
        data = _load_lap_cache(activity_id)
        if data:
            data["_source"] = "cache"
            logger.debug(f"📦 Lap cache HIT: activity {activity_id}")
            return data

    # พยายามดึงจาก API
    try:
        raw = garmin_get(client.get_activity_splits, activity_id)

        laps_raw = []
        if isinstance(raw, dict):
            laps_raw = raw.get("lapDTOs", []) or raw.get("laps", [])
        elif isinstance(raw, list):
            laps_raw = raw

        laps = []
        for i, lap in enumerate(laps_raw):
            dist_m    = lap.get("distance") or 0
            dur_s     = lap.get("duration") or lap.get("elapsedDuration") or 0
            speed_ms  = lap.get("averageSpeed") or 0
            avg_hr    = lap.get("averageHR") or lap.get("averageHeartRate")
            max_hr    = lap.get("maxHR") or lap.get("maxHeartRate")
            # pace ใน sec/km
            pace_sec_km = round(1000 / speed_ms) if speed_ms > 0 else None

            laps.append({
                "lapIndex":        lap.get("lapIndex", i),
                "distance_m":      round(dist_m, 1),
                "duration_s":      round(dur_s, 1),
                "avg_hr":          avg_hr,
                "max_hr":          max_hr,
                "avg_speed_ms":    round(speed_ms, 3) if speed_ms else None,
                "avg_pace_sec_km": pace_sec_km,
                "is_manual_lap":   lap.get("lapTrigger", "") == "Manual",
            })

        data = {
            "activity_id": activity_id,
            "lap_count":   len(laps),
            "laps":        laps,
            "_source":     "live",
            "fetched_at":  datetime.now().isoformat(),
        }
        _save_lap_cache(activity_id, data)
        logger.info(f"✅ Lap data fetched live: activity {activity_id} ({len(laps)} laps)")
        return data

    except Exception as e:
        logger.warning(f"⚠️  API ไม่พร้อม ({e}) — ลอง fallback lap cache...")
        stale = _load_lap_cache(activity_id)
        if stale:
            path = _lap_cache_path(activity_id)
            age_h = (time.time() - path.stat().st_mtime) / 3600
            stale["_source"] = f"cache_stale ({age_h:.1f}h ago)"
            logger.warning(f"📦 Using stale lap cache ({age_h:.1f}h old): activity {activity_id}")
            return stale

        logger.error(f"❌ ไม่มี lap data สำหรับ activity {activity_id}")
        return {
            "activity_id": activity_id,
            "lap_count":   0,
            "laps":        [],
            "_source":     "unavailable",
        }


def is_garmin_reachable() -> bool:
    """
    ทดสอบว่า Garmin API พร้อมใช้งานไหม (quick connectivity check)
    คืน True ถ้า API ตอบสนองปกติ, False ถ้า 429 หรือ network error
    """
    try:
        client = get_client()
        today = datetime.now().strftime("%Y-%m-%d")
        garmin_get(client.get_user_summary, today)
        return True
    except Exception:
        return False
