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

# Wellness master data (single source of truth — แทน health_cache เดิม)
WELLNESS_DIR     = Path(__file__).resolve().parent.parent / "wellness"
HEALTH_CACHE_TTL = 2 * 3600  # 2 ชั่วโมง (วินาที) — refresh "วันนี้" ถ้า snapshot เก่ากว่านี้

# bb_morning = snapshot แรกในช่วง 04:00–10:00 เท่านั้น (กัน post-run snapshot ปลอมตัวเป็น morning)
MORNING_HOUR_START = 4
MORNING_HOUR_END   = 10


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
def _make_client(mfa_code: str | None = None):
    """สร้าง Garmin client ใหม่พร้อม tokenstore directory approach

    mfa_code: ใส่ตอน fresh login ครั้งแรกถ้า account เปิด 2FA (ปกติไม่ต้องใส่
    เพราะ tokenstore restore จะข้ามขั้นตอนนี้ไปหลัง login สำเร็จครั้งแรก)
    """
    try:
        from garminconnect import Garmin
    except ImportError:
        raise ImportError(
            "❌ garminconnect library ไม่พบ\n"
            "  pip install garminconnect --break-system-packages"
        )

    username, password = _load_credentials()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    def _prompt_mfa():
        # Called by garminconnect only when the account actually needs a
        # 2FA/MFA code. An AI agent runs tools non-interactively (no
        # terminal attached to stdin) — without this guard, garminconnect
        # would call input() and hang forever waiting for a code nobody can
        # type, and the agent would see a stuck/timed-out process and often
        # misdiagnose it as a wrong password, retrying until Garmin
        # rate-limits or locks the account.
        if mfa_code:
            return mfa_code
        if sys.stdin.isatty():
            return input("🔐 Garmin ต้องการ MFA/2FA code (เช็คอีเมล/แอป authenticator): ").strip()
        raise RuntimeError(
            "❌ Garmin account นี้เปิด MFA/2FA — ต้อง login ครั้งแรกแบบ interactive ก่อน\n"
            "  รันคำสั่งนี้จาก terminal จริงของคุณเอง (ไม่ใช่ผ่าน AI agent):\n"
            "    cd GarminRawData/tools && ../../.venv/bin/python3.13 garmin_client.py --mfa <code จากอีเมล/แอป>\n"
            "  หลัง login สำเร็จครั้งแรก session token จะถูกเก็บไว้ใน tokenstore —\n"
            "  ทุก tool/AI agent ครั้งต่อไปจะ restore session นี้ได้เลยโดยไม่ต้องขอ MFA ซ้ำ"
        )

    client = Garmin(username, password, prompt_mfa=_prompt_mfa)

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


def get_client(mfa_code: str | None = None):
    """
    คืน singleton Garmin client (สร้างใหม่ถ้ายังไม่มี หรือถ้าถูก invalidate)

    mfa_code: ส่งต่อไป _make_client() — ใช้เฉพาะตอน fresh login ครั้งแรกที่
    ต้อง MFA (ดู `python3 garmin_client.py --mfa <code>` สำหรับ one-time setup)

    Returns:
        Garmin client instance ที่พร้อมใช้งาน
    """
    global _client
    if _client is None:
        _client = _make_client(mfa_code=mfa_code)
    return _client


def invalidate_session():
    """Force re-login ครั้งถัดไป (เรียกเมื่อพบ 401 หรือ session error)"""
    global _client
    _client = None
    logger.info("Session invalidated — จะ re-login ในครั้งถัดไป")


# ---------------------------------------------------------------------------
# Wellness Master Data  (BB / HRV / RHR / Sleep)
#
# SINGLE SOURCE OF TRUTH = GarminRawData/wellness/wellness_YYYY-MM-DD.json
# โครงสร้าง: { date, snapshots:[...], + flat top-level superset สำหรับ tools }
# health_cache เดิมถูกยกเลิก — ทุก tool อ่าน wellness master ตัวเดียว
# ---------------------------------------------------------------------------
_HEALTH_KEYS = (
    "body_battery", "bb_high", "bb_low", "bb_morning", "hrv_status",
    "resting_hr", "stress_avg", "sleep_score", "sleep_deep_min",
    "sleep_rem_min", "sleep_light_min", "sleep_awake_min",
    "respiratory_avg", "respiratory_low", "respiratory_high",
    "spo2_avg", "spo2_low",
)


def _wellness_path(date_str: str) -> Path:
    WELLNESS_DIR.mkdir(parents=True, exist_ok=True)
    return WELLNESS_DIR / f"wellness_{date_str}.json"


def _load_wellness_day(date_str: str) -> dict | None:
    """อ่าน wellness master ดิบ (raw master dict) — None ถ้าไม่มีไฟล์"""
    path = WELLNESS_DIR / f"wellness_{date_str}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _save_wellness_day(date_str: str, data: dict):
    path = _wellness_path(date_str)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, path)
    logger.debug(f"💾 Wellness master saved: {path.name}")


def _flatten_wellness(master: dict) -> dict:
    """แปลง wellness master → flat health dict (schema เดิมที่ tools คาดหวัง)"""
    flat = {"date": master.get("date")}
    for k in _HEALTH_KEYS:
        flat[k] = master.get(k)
    # ป้องกัน None ที่ tools เรียก .lower() — default "Unknown" เหมือนเดิม
    if not flat.get("hrv_status"):
        flat["hrv_status"] = "Unknown"
    flat["_source"] = "wellness"
    return flat


def _extract_wellness_snapshot(client, date_str: str) -> dict:
    """ดึง wellness 1 snapshot จาก Garmin API (get_stats + BB + HRV + Sleep)"""
    snap = {
        "snapshot_at": datetime.now().isoformat(),
        "body_battery": None, "bb_high": None, "bb_low": None,
        "hrv_status": None, "resting_hr": None, "stress_avg": None,
        "sleep_score": None, "sleep_deep_min": None, "sleep_rem_min": None,
        "sleep_light_min": None, "sleep_awake_min": None,
        "respiratory_avg": None, "respiratory_low": None, "respiratory_high": None,
        "spo2_avg": None, "spo2_low": None,
    }

    # Daily stats → resting HR / stress / BB high+low (authoritative nadir)
    try:
        stats = garmin_get(client.get_stats, date_str)
        if stats:
            snap["resting_hr"] = stats.get("restingHeartRate")
            snap["stress_avg"] = stats.get("averageStressLevel")
            snap["bb_high"]    = stats.get("bodyBatteryHighestValue")
            snap["bb_low"]     = stats.get("bodyBatteryLowestValue")
    except Exception as e:
        logger.warning(f"stats fetch failed: {e}")

    # Body Battery current (last value of day)
    try:
        bb_data = garmin_get(client.get_body_battery, date_str, date_str)
        if bb_data and isinstance(bb_data, list):
            for entry in bb_data:
                vals = entry.get("bodyBatteryValuesArray", [])
                if vals:
                    snap["body_battery"] = vals[-1][1] if isinstance(vals[-1], list) else vals[-1]
    except Exception as e:
        logger.warning(f"BB fetch failed: {e}")

    # HRV status
    try:
        hrv = garmin_get(client.get_hrv_data, date_str)
        if hrv:
            summary = hrv.get("hrvSummary", {})
            snap["hrv_status"] = summary.get("status") or hrv.get("hrvStatus")
    except Exception as e:
        logger.warning(f"HRV fetch failed: {e}")

    # Sleep score / stages / respiratory / spo2
    try:
        sleep = garmin_get(client.get_sleep_data, date_str)
        if sleep:
            dto = sleep.get("dailySleepDTO", {}) or {}
            raw_score = dto.get("sleepScores", {}).get("overall") or dto.get("sleepScore")
            snap["sleep_score"] = raw_score.get("value") if isinstance(raw_score, dict) else raw_score
            if dto.get("deepSleepSeconds") is not None:
                snap["sleep_deep_min"]  = round(dto["deepSleepSeconds"] / 60, 1)
            if dto.get("remSleepSeconds") is not None:
                snap["sleep_rem_min"]   = round(dto["remSleepSeconds"] / 60, 1)
            if dto.get("lightSleepSeconds") is not None:
                snap["sleep_light_min"] = round(dto["lightSleepSeconds"] / 60, 1)
            if dto.get("awakeSleepSeconds") is not None:
                snap["sleep_awake_min"] = round(dto["awakeSleepSeconds"] / 60, 1)
            snap["respiratory_avg"]  = dto.get("averageRespirationValue")
            snap["respiratory_low"]  = dto.get("lowestRespirationValue")
            snap["respiratory_high"] = dto.get("highestRespirationValue")
            snap["spo2_avg"]         = dto.get("averageSpO2Value")
            snap["spo2_low"]         = dto.get("lowestSpO2Value")
    except Exception as e:
        logger.warning(f"Sleep fetch failed: {e}")

    return snap


def sync_wellness_day(client, date_str: str) -> dict:
    """
    Fetch 1 snapshot จาก Garmin → merge เข้า wellness master → เขียนไฟล์ → คืน master

    - เก็บหลาย snapshot/วัน (morning, post-run) ใน snapshots[]
    - upsert: snapshot ภายใน 10 นาที = overwrite, ไกลกว่านั้น = append
    - bb_morning = snapshot แรกในช่วง 04:00–10:00 เท่านั้น
    - flat top-level superset (latest snapshot) สำหรับ tools อ่านง่าย
    """
    master = _load_wellness_day(date_str) or {"date": date_str, "snapshots": []}
    snap = _extract_wellness_snapshot(client, date_str)

    snaps = master.get("snapshots", [])
    now = datetime.fromisoformat(snap["snapshot_at"])
    replaced = False
    for i, s in enumerate(snaps):
        try:
            if abs((now - datetime.fromisoformat(s["snapshot_at"])).total_seconds()) < 600:
                snaps[i] = snap
                replaced = True
                break
        except Exception:
            pass
    if not replaced:
        snaps.append(snap)
    master["snapshots"] = snaps

    # bb_morning — snapshot แรกในหน้าต่างเช้าเท่านั้น
    morning_bb = None
    for s in sorted(snaps, key=lambda x: x.get("snapshot_at", "")):
        try:
            h = datetime.fromisoformat(s["snapshot_at"]).hour
            if MORNING_HOUR_START <= h < MORNING_HOUR_END:
                morning_bb = s.get("body_battery")
                break
        except Exception:
            pass

    # flat top-level superset (จาก latest snapshot + computed)
    latest = snaps[-1]
    for k in _HEALTH_KEYS:
        master[k] = latest.get(k)
    master["bb_morning"]           = morning_bb
    master["bb_morning_confirmed"] = morning_bb is not None
    master["bb_latest"]            = latest.get("body_battery")
    master["fetched_at"]           = snap["snapshot_at"]

    _save_wellness_day(date_str, master)
    return master


def _load_health_cache(date_str: str) -> dict | None:
    """
    [compat] offline read — คืน flat health dict จาก wellness master (ไม่เรียก API)
    คงชื่อเดิมไว้เพื่อ backward-compat กับ tools ที่ import (taper_monitor, session_prescriber, daily_brief --offline)
    """
    master = _load_wellness_day(date_str)
    if not master:
        return None
    flat = _flatten_wellness(master)
    flat["_source"] = "cache"
    return flat


def get_health_cached(client, date_str: str, force_refresh: bool = False) -> dict:
    """
    ดึง BB / HRV / RHR / Sleep — อ่านจาก wellness master (single source of truth)

    - past date: trust master เสมอ (BB ของวันที่ผ่านไปแล้ว final)
    - today: refresh จาก API ถ้า master ไม่มี หรือ snapshot ล่าสุดเก่ากว่า TTL (2h)
    - API ไม่พร้อม: fallback master เก่า → skeleton

    Returns: flat dict (date, body_battery, bb_high, bb_low, hrv_status, ...)
    """
    path = _wellness_path(date_str)
    is_today = date_str == datetime.now().strftime("%Y-%m-%d")

    stale = False
    if path.exists():
        stale = (time.time() - path.stat().st_mtime) > HEALTH_CACHE_TTL

    need_refresh = force_refresh or (not path.exists()) or (is_today and stale)

    if need_refresh:
        try:
            master = sync_wellness_day(client, date_str)
            flat = _flatten_wellness(master)
            flat["_source"] = "live"
            logger.info(f"✅ Wellness fetched live: {date_str}")
            return flat
        except Exception as e:
            logger.warning(f"⚠️  API ไม่พร้อม ({e}) — ลอง fallback wellness master...")

    master = _load_wellness_day(date_str)
    if master and master.get("snapshots"):
        flat = _flatten_wellness(master)
        flat["_source"] = "cache" if need_refresh else "wellness"
        return flat

    logger.error(f"❌ ไม่มีข้อมูล wellness สำหรับ {date_str} ทั้ง live และ master")
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


# ---------------------------------------------------------------------------
# One-time interactive setup — run this directly (not via an AI agent) if
# your Garmin account has MFA/2FA enabled, before letting any tool/agent try
# to log in non-interactively.
#
#   cd GarminRawData/tools
#   ../../.venv/bin/python3.13 garmin_client.py --mfa <code from email/app>
#
# On success the session token is saved to the tokenstore directory and every
# tool afterwards (agent-run or manual) restores it without needing MFA again.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="One-time interactive Garmin login (for MFA/2FA accounts)")
    parser.add_argument("--mfa", type=str, default=None,
                         help="MFA/2FA code from email or authenticator app")
    args = parser.parse_args()
    invalidate_session()
    get_client(mfa_code=args.mfa)
    print("✅ Login สำเร็จ — session token บันทึกแล้ว ใช้ tool อื่นได้เลยโดยไม่ต้องขอ MFA อีก")
