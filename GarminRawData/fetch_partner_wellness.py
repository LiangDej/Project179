#!/usr/bin/env python3
"""
fetch_partner_wellness.py — Wellness fetch for a second Garmin account (raw data only)

Completely separate from the primary athlete pipeline:
  - Credentials : ~/.config/garmin-coach/.env.partner   (own file, never shared with .env)
  - Session/token: ~/.config/garmin-coach/partner_session/  (own tokenstore, never mixes
                    with the primary account's garmin_tokens.json)
  - Output      : GarminRawData/wellness_partner/wellness_YYYY-MM-DD.json

No running/training logic here on purpose — this is for general health questions
(Body Battery, HRV, Sleep, RHR, Stress, Respiratory), not race/VDOT coaching.

Usage:
    python3 fetch_partner_wellness.py                # yesterday + today
    python3 fetch_partner_wellness.py --days 14       # last N days
"""
import os
import sys
import json
import time
import random
import logging
import argparse
from datetime import date, timedelta, datetime
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ENV_PATH     = Path.home() / ".config" / "garmin-coach" / ".env.partner"
SESSION_DIR  = Path.home() / ".config" / "garmin-coach" / "partner_session"
OUTPUT_DIR   = Path(__file__).resolve().parent / "wellness_partner"

_MIN_CALL_INTERVAL = 1.0
_last_call_time = 0.0


def _rate_limit():
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)
    _last_call_time = time.monotonic()


def _garmin_get(fn, *args, max_retries=4, **kwargs):
    """Minimal retry+backoff wrapper — self-contained, no shared client/session state."""
    for attempt in range(max_retries + 1):
        try:
            _rate_limit()
            return fn(*args, **kwargs)
        except Exception as e:
            err = str(e).lower()
            if ("429" in err or "too many requests" in err) and attempt < max_retries:
                wait = min(2.0 * (2 ** attempt), 60.0) + random.uniform(0, 1.0)
                logger.warning(f"429 rate limit — waiting {wait:.1f}s (attempt {attempt+1})")
                time.sleep(wait)
                continue
            raise


def _load_credentials() -> tuple[str, str]:
    if not ENV_PATH.exists():
        raise FileNotFoundError(
            f"\n❌ ไม่พบ {ENV_PATH}\n\n"
            "  Setup:\n"
            "    mkdir -p ~/.config/garmin-coach\n"
            "    nano ~/.config/garmin-coach/.env.partner\n"
            "    # ใส่ GARMIN_USERNAME=... / GARMIN_PASSWORD=... แล้ว save\n"
        )
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    # Accept either name — GARMIN_USERNAME matches the primary pipeline's
    # convention, GARMIN_EMAIL is what the setup instructions actually told
    # the user to type, so honor both rather than make them re-enter it.
    username = (os.environ.get("GARMIN_USERNAME", "").strip()
                or os.environ.get("GARMIN_EMAIL", "").strip())
    password = os.environ.get("GARMIN_PASSWORD", "").strip()
    if not username or not password:
        raise ValueError(f"❌ GARMIN_USERNAME/GARMIN_EMAIL หรือ GARMIN_PASSWORD ว่างเปล่าใน {ENV_PATH}")
    return username, password


def get_client(mfa_code: str | None = None):
    from garminconnect import Garmin
    username, password = _load_credentials()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    prompt_mfa = (lambda: mfa_code) if mfa_code else None
    client = Garmin(username, password, prompt_mfa=prompt_mfa)
    try:
        client.login(tokenstore=str(SESSION_DIR))
        logger.info("✅ Partner Garmin session restored from tokenstore")
        return client
    except Exception as e:
        logger.info(f"Session restore ไม่ได้ ({e}) — fresh login...")

    client.login()
    logger.info("✅ Partner Garmin fresh login สำเร็จ")
    try:
        client.garth.dump(str(SESSION_DIR))
    except Exception as e:
        logger.warning(f"บันทึก session token ไม่ได้: {e}")
    return client


def extract_wellness_snapshot(client, date_str: str) -> dict:
    """Same field set as the primary pipeline's wellness snapshot — kept
    identical on purpose so any future tooling can reuse the same shape."""
    snap = {
        "date": date_str,
        "snapshot_at": datetime.now().isoformat(),
        "body_battery": None, "bb_high": None, "bb_low": None,
        "hrv_status": None, "resting_hr": None, "stress_avg": None,
        "sleep_score": None, "sleep_deep_min": None, "sleep_rem_min": None,
        "sleep_light_min": None, "sleep_awake_min": None,
        "respiratory_avg": None, "respiratory_low": None, "respiratory_high": None,
        "spo2_avg": None, "spo2_low": None,
    }

    try:
        stats = _garmin_get(client.get_stats, date_str)
        if stats:
            snap["resting_hr"] = stats.get("restingHeartRate")
            snap["stress_avg"] = stats.get("averageStressLevel")
            snap["bb_high"]    = stats.get("bodyBatteryHighestValue")
            snap["bb_low"]     = stats.get("bodyBatteryLowestValue")
    except Exception as e:
        logger.warning(f"stats fetch failed ({date_str}): {e}")

    try:
        bb_data = _garmin_get(client.get_body_battery, date_str, date_str)
        if bb_data and isinstance(bb_data, list):
            for entry in bb_data:
                vals = entry.get("bodyBatteryValuesArray", [])
                if vals:
                    snap["body_battery"] = vals[-1][1] if isinstance(vals[-1], list) else vals[-1]
    except Exception as e:
        logger.warning(f"BB fetch failed ({date_str}): {e}")

    try:
        hrv = _garmin_get(client.get_hrv_data, date_str)
        if hrv:
            summary = hrv.get("hrvSummary", {})
            snap["hrv_status"] = summary.get("status") or hrv.get("hrvStatus")
    except Exception as e:
        logger.warning(f"HRV fetch failed ({date_str}): {e}")

    try:
        sleep = _garmin_get(client.get_sleep_data, date_str)
        if sleep:
            dto = sleep.get("dailySleepDTO", {}) or {}
            raw_score = dto.get("sleepScores", {}).get("overall") or dto.get("sleepScore")
            snap["sleep_score"] = raw_score.get("value") if isinstance(raw_score, dict) else raw_score
            for key, src in [("sleep_deep_min", "deepSleepSeconds"),
                              ("sleep_rem_min", "remSleepSeconds"),
                              ("sleep_light_min", "lightSleepSeconds"),
                              ("sleep_awake_min", "awakeSleepSeconds")]:
                if dto.get(src) is not None:
                    snap[key] = round(dto[src] / 60, 1)
            snap["respiratory_avg"]  = dto.get("averageRespirationValue")
            snap["respiratory_low"]  = dto.get("lowestRespirationValue")
            snap["respiratory_high"] = dto.get("highestRespirationValue")
            snap["spo2_avg"]         = dto.get("averageSpO2Value")
            snap["spo2_low"]         = dto.get("lowestSpO2Value")
    except Exception as e:
        logger.warning(f"Sleep fetch failed ({date_str}): {e}")

    return snap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=2, help="จำนวนวันย้อนหลังที่จะดึง (default: วันนี้+เมื่อวาน)")
    parser.add_argument("--mfa", type=str, default=None, help="MFA code (one-time, ใช้แค่ตอน fresh login ครั้งแรก)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = get_client(mfa_code=args.mfa)

    today = date.today()
    saved = 0
    for i in range(args.days):
        d = today - timedelta(days=i)
        d_str = d.isoformat()
        snap = extract_wellness_snapshot(client, d_str)
        out_path = OUTPUT_DIR / f"wellness_{d_str}.json"
        out_path.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"✅ {d_str} — BB {snap['body_battery']} | HRV {snap['hrv_status']} | Sleep {snap['sleep_score']}")
        saved += 1

    print(f"\n💾 บันทึก {saved} วัน → {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
