#!/usr/bin/env python3
"""
post_session_analyzer.py — JD Analytics Toolkit (P1)
วิเคราะห์เซสชันที่วิ่งจบ: Decoupling, Zone%, Power, GCT, Cadence, Stamina
รองรับทั้ง Quality Run (T/I/R) และ Easy Run depth analysis

Usage:
    python3 post_session_analyzer.py --latest
    python3 post_session_analyzer.py --id <activity_id>
    python3 post_session_analyzer.py --latest --update-log   # บันทึกผลลง session log
"""

from __future__ import annotations

import os
import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from garminconnect import Garmin

# --- Config ---
BASE_DIR = Path(__file__).parent.parent
SECURE_ENV = Path.home() / ".config" / "garmin-coach" / ".env"
# Load only from secure path — never from project/synced directory
load_dotenv(dotenv_path=SECURE_ENV)

# Import athlete constants from single source of truth
sys.path.append(str(BASE_DIR.parent / "skills" / "garmin_coach_mcp"))
from config import ATHLETE, HR_ZONES, BASELINES, ZONE_PCT  # noqa: E402

ZONE_LABELS = ["Z1(Easy)", "Z2(M)", "Z3(T)", "Z4(I)", "Z5(R)"]
ZONES = HR_ZONES  # alias for compatibility

SESSION_LOG_PATH = BASE_DIR / "QualitySessionLog" / "sessions.json"


# ---------------------------------------------------------------------------
# Garmin client helpers
# ---------------------------------------------------------------------------
def get_garmin_client():
    username = os.environ.get("GARMIN_USERNAME")
    password = os.environ.get("GARMIN_PASSWORD")
    session_dir = Path.home() / ".config" / "garmin-coach"
    client = Garmin(username, password)
    client.login(tokenstore=str(session_dir))
    return client


def fetch_activity(act_id):
    qd_dir = BASE_DIR / "QualityDetails"
    cache_dir = BASE_DIR / "SessionCache"

    # 1. Try QualityDetails first (pattern: quality_*_{act_id}.json)
    if qd_dir.exists():
        for f in qd_dir.glob(f"quality_*_{act_id}.json"):
            print(f"📂 Loading session {act_id} from QualityDetails ({f.name})...")
            with open(f, "r") as fh:
                return json.load(fh)

    # 2. Try SessionCache (pattern: session_{act_id}.json)
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / f"session_{act_id}.json"
    if cache_file.exists():
        print(f"📂 Loading session {act_id} from SessionCache...")
        with open(cache_file, "r") as f:
            return json.load(f)

    # 3. Download to SessionCache
    print(f"📡 Downloading session {act_id} from Garmin...")
    client = get_garmin_client()
    data = client.get_activity_details(act_id)

    # Attach summary DTO (for canonical avg pace — fixes TM speed-sample bias)
    try:
        summary = client.get_activity(act_id)
        if isinstance(summary, dict):
            data["_activitySummary"] = summary
    except Exception as e:
        print(f"⚠️  summary fetch failed: {e}")

    # Attach lap splits (for per-rep quality analysis)
    try:
        splits = client.get_activity_splits(act_id)
        if isinstance(splits, dict):
            data["_activitySplits"] = splits
    except Exception as e:
        print(f"⚠️  splits fetch failed: {e}")

    with open(cache_file, "w") as f:
        json.dump(data, f, indent=2)
    return data


def get_latest_activity_id():
    """Fetch latest activity from Garmin live (fixes stale cache bug)."""
    try:
        from garmin_client import get_client
        client = get_client()
        acts = client.get_activities(0, 1)
        if acts:
            return acts[0]["activityId"], acts[0]["startTimeLocal"]
    except Exception as e:
        print(f"⚠️  Live fetch failed ({e}) — fallback to cache")
    # Fallback: cache, but sort by startTimeLocal desc to avoid stale [0]
    acts_file = BASE_DIR / "running_activities_all.json"
    with open(acts_file) as f:
        acts = json.load(f)
    acts_sorted = sorted(acts, key=lambda a: a.get("startTimeLocal", ""), reverse=True)
    return acts_sorted[0]["activityId"], acts_sorted[0]["startTimeLocal"]


def get_activity_meta(act_id):
    acts_file = BASE_DIR / "running_activities_all.json"
    with open(acts_file) as f:
        acts = json.load(f)
    for a in acts:
        if a["activityId"] == act_id:
            return a["startTimeLocal"], a.get("duration", 0)
    return None, 0


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------
def get_val(p, idx):
    if idx is not None and idx < len(p):
        return p[idx]
    return None


def classify_session(avg_hr, avg_pace_min_km, rhr=None, mhr=None):
    rhr = rhr or ATHLETE["rhr"]
    mhr = mhr or ATHLETE["mhr"]
    hrr = mhr - rhr
    hr_pct = (avg_hr - rhr) / hrr * 100
    if hr_pct < ZONE_PCT["E"]:
        return "Easy Run"
    elif hr_pct < ZONE_PCT["M"]:
        return "Marathon Pace"
    elif hr_pct < ZONE_PCT["T"]:
        return "Threshold (T)"
    elif hr_pct < ZONE_PCT["I"]:
        return "Interval (I)"
    else:
        return "Repetition (R)"


def calculate_decoupling(metrics, hr_idx, dist_idx, speed_idx):
    valid = [p for p in metrics if get_val(p, hr_idx) and get_val(p, speed_idx) and get_val(p, speed_idx) > 1.0]
    if len(valid) < 200:
        return None, None, None
    mid = len(valid) // 2
    h1, h2 = valid[:mid], valid[mid:]

    def eff(pts):
        avg_hr = sum(get_val(p, hr_idx) for p in pts) / len(pts)
        spd = [get_val(p, speed_idx) for p in pts if get_val(p, speed_idx) and get_val(p, speed_idx) > 1.0]
        avg_spd = sum(spd) / len(spd) if spd else 1
        return avg_hr / avg_spd if avg_spd > 0 else 0

    e1, e2 = eff(h1), eff(h2)
    dc = round(((e2 / e1) - 1) * 100, 1) if e1 > 0 else 0
    hr1 = int(sum(get_val(p, hr_idx) for p in h1) / len(h1))
    hr2 = int(sum(get_val(p, hr_idx) for p in h2) / len(h2))
    return dc, hr1, hr2


def analyze(data):
    desc = {d["key"]: d["metricsIndex"] for d in data.get("metricDescriptors", [])}
    metrics = [m.get("metrics", []) for m in data.get("activityDetailMetrics", [])]

    hr_idx      = desc.get("directHeartRate")
    dist_idx    = desc.get("sumDistance")
    speed_idx   = desc.get("directSpeed")
    cad_idx     = desc.get("directRunCadence")
    gct_idx     = desc.get("directGroundContactTime")
    pow_idx     = desc.get("directPower")
    stam_idx    = desc.get("directAvailableStamina")
    elapsed_idx = desc.get("sumElapsedDuration")
    vr_idx      = desc.get("directVerticalRatio")
    stride_idx  = desc.get("directStrideLength")

    # Detect treadmill — check both legacy + summary DTO paths
    type_key = (data.get("activityType") or {}).get("typeKey")
    if not type_key:
        type_key = (((data.get("_activitySummary") or {})
                     .get("activityTypeDTO") or {}).get("typeKey"))
    is_treadmill = type_key == "treadmill_running"

    active = [p for p in metrics if get_val(p, speed_idx) and get_val(p, speed_idx) > 1.5]

    hrs = [get_val(p, hr_idx) for p in metrics if get_val(p, hr_idx)]
    avg_hr = int(sum(hrs) / len(hrs)) if hrs else 0
    max_hr = int(max(hrs)) if hrs else 0

    spds = [get_val(p, speed_idx) for p in active if get_val(p, speed_idx)]
    avg_speed = sum(spds) / len(spds) if spds else 0
    pace = 1000 / (avg_speed * 60) if avg_speed > 0 else 0

    # Canonical pace override from Garmin summary (fixes TM speed-sample bias)
    # Garmin's averageSpeed = distance / movingDuration → mathematically correct
    summary = data.get("_activitySummary") or {}
    s = summary.get("summaryDTO", summary) if isinstance(summary, dict) else {}
    canonical_speed = s.get("averageSpeed")
    if canonical_speed and canonical_speed > 0:
        canonical_pace = 1000 / (canonical_speed * 60)
        # Use canonical if it differs >5% from sample-avg (catches TM bias)
        if pace == 0 or abs(canonical_pace - pace) / max(pace, 0.01) > 0.05:
            pace = canonical_pace
            avg_speed = canonical_speed

    # Garmin streams single-leg cadence (~80-90) — double to full spm BEFORE
    # rounding to int, not after, or truncating the raw mean first (e.g.
    # int(82.8)=82 -> *2=164) systematically undercounts by 1-3 spm vs
    # Garmin's own summary field (which doubles at full precision first).
    cads = [get_val(p, cad_idx) for p in active if get_val(p, cad_idx)]
    avg_cad_raw = sum(cads) / len(cads) if cads else 0
    if 0 < avg_cad_raw < 120:
        avg_cad_raw *= 2
    avg_cad = round(avg_cad_raw)

    gcts = [get_val(p, gct_idx) for p in active if get_val(p, gct_idx)]
    avg_gct = int(sum(gcts) / len(gcts)) if gcts else 0

    vrs = [get_val(p, vr_idx) for p in active if get_val(p, vr_idx)]
    avg_vr = round(sum(vrs) / len(vrs), 1) if vrs else None

    strides = [get_val(p, stride_idx) for p in active if get_val(p, stride_idx)]
    avg_stride = round(sum(strides) / len(strides) / 100, 2) if strides else None

    pows = [get_val(p, pow_idx) for p in active if get_val(p, pow_idx) and get_val(p, pow_idx) > 50]
    avg_pow = int(sum(pows) / len(pows)) if pows else 0

    stams = [get_val(p, stam_idx) for p in metrics if get_val(p, stam_idx)]
    stam_start = int(stams[0]) if stams else None
    stam_end   = int(stams[-1]) if stams else None

    dur_raw = get_val(metrics[-1], elapsed_idx) if metrics and elapsed_idx is not None else 0
    dur_sec = int(dur_raw) if dur_raw else 0

    zone_counts = [0] * 5
    for h in hrs:
        for i, (_, lo, hi) in enumerate(ZONES):
            if lo <= h < hi:
                zone_counts[i] += 1
                break
    total = len(hrs) or 1
    zone_pct = [round(c / total * 100, 1) for c in zone_counts]

    dc, hr1, hr2 = calculate_decoupling(metrics, hr_idx, dist_idx, speed_idx)

    _zone_to_type = {"R": "Repetition (R)", "I": "Interval (I)",
                      "T": "Threshold (T)", "M": "Marathon Pace",
                      "E": "Easy Run"}

    # Priority 1: cross-check against the prescribed workout name itself.
    # garmin_workout_pusher.py names every pushed session "Quality <T/I/R>
    # ..." (see build_threshold/build_interval) — if that tag is on the
    # activity, it IS the prescribed type, no need to infer anything.
    _act_name = (data.get("_activitySummary") or {}).get("activityName") or ""
    _tag_match = re.search(r"\bQuality\s+([EMTIR])\b", _act_name)

    # Priority 2 (no tag — manual/watch-recorded run): rep-window PACE (via
    # _classify_speed, auto-tracks VDOT), for genuine multi-rep interval/
    # threshold structures (≥2 reps split by recovery/WU/CD). Pace is the
    # JD-prescribed target and stays stable rep-to-rep, while avg_hr is not
    # — WU+CD dilute it toward Easy/M, and fatigue/heat drift it upward
    # within the work block itself (see decoupling above), so HR-based
    # classification can land on the wrong zone either way.

    # Priority 3 (fallback): whole-session avg_hr — used as-is for true
    # easy/long runs, and for a single unbroken block (TT/race/continuous
    # tempo effort) where there's no prescribed pace to key off and HR is
    # the truer intensity signal.
    session_type = classify_session(avg_hr, pace)
    form_scope = "whole_session"
    _splits_for_type = data.get("_activitySplits")
    if _splits_for_type:
        _lap_data = analyze_laps(_splits_for_type, is_treadmill)
        if _lap_data and len(_lap_data["reps"]) > 1:
            _reps = _lap_data["reps"]
            _tot_t = sum(rp["duration_s"] for rp in _reps) or 1
            _rep_pace = sum(rp["pace_min_km"] * rp["duration_s"] for rp in _reps) / _tot_t
            _rep_speed_kmh = 60.0 / _rep_pace if _rep_pace > 0 else 0
            if _rep_speed_kmh > 0:
                session_type = _zone_to_type[_classify_speed(_rep_speed_kmh)]

            # Same dilution problem as HR/pace classification above, but for
            # form metrics: WU/CD/recovery jogs sit inside the plain speed>1.5
            # "active" filter used below, so headline Cadence/GCT/Power/VR
            # would otherwise blend easy-jog form with work-rep form. For a
            # real multi-rep quality session, report form AS EXECUTED during
            # the work reps only — duration-weighted across reps.
            avg_cad   = sum(rp["avg_cadence"] * rp["duration_s"] for rp in _reps) / _tot_t
            avg_pow   = sum(rp["avg_power"]   * rp["duration_s"] for rp in _reps) / _tot_t
            avg_gct   = sum(rp["avg_gct"]     * rp["duration_s"] for rp in _reps) / _tot_t
            avg_vr    = sum(rp["avg_vr"]      * rp["duration_s"] for rp in _reps) / _tot_t
            avg_stride = sum(rp["avg_stride"] * rp["duration_s"] for rp in _reps) / _tot_t
            avg_cad, avg_pow, avg_gct = round(avg_cad), round(avg_pow), round(avg_gct)
            avg_vr, avg_stride = round(avg_vr, 1), round(avg_stride, 2)
            form_scope = "work_reps"
    if _tag_match:
        session_type = _zone_to_type[_tag_match.group(1)]

    # --- Garmin-native assessment (Plan A Tier 1) ---
    # Fields live in 3 places depending on which API returned them:
    #   1. _activitySummary top-level
    #   2. _activitySummary.summaryDTO  (preferred for trainingEffect)
    #   3. running_activities_all.json list entry (has hrTimeInZone_*, normPower, aerobicTrainingEffect)
    parent_summary = data.get("_activitySummary") or {}
    summary_dto = s if isinstance(s, dict) else {}

    # Fallback to running_activities_all.json by activityId
    act_id = data.get("activityId") or parent_summary.get("activityId")
    list_entry: dict = {}
    if act_id:
        try:
            from pathlib import Path as _P
            _acts_path = _P(__file__).resolve().parent.parent / "running_activities_all.json"
            if _acts_path.exists():
                import json as _json
                for a in _json.loads(_acts_path.read_text()):
                    if a.get("activityId") == act_id:
                        list_entry = a
                        break
        except Exception:
            pass

    def _g(*keys):
        """Try multiple keys across all 3 sources, return first non-None."""
        for src in (parent_summary, summary_dto, list_entry):
            for k in keys:
                v = src.get(k)
                if v is not None:
                    return v
        return None

    # summaryDTO uses "trainingEffect" (no "aerobic" prefix), list uses "aerobicTrainingEffect"
    garmin_aerobic_te    = _g("aerobicTrainingEffect", "trainingEffect")
    garmin_anaerobic_te  = _g("anaerobicTrainingEffect")
    garmin_te_label      = _g("trainingEffectLabel")
    garmin_aerobic_msg   = _g("aerobicTrainingEffectMessage")
    garmin_anaerobic_msg = _g("anaerobicTrainingEffectMessage")
    # HR time-in-zone (seconds) — only in list_entry typically
    garmin_hr_zone_sec = [(_g(f"hrTimeInZone_{i}") or 0) for i in range(1, 6)]
    # Normalized power
    garmin_norm_power = _g("normPower")

    return {
        "session_type": session_type,
        "form_scope": form_scope,
        "duration_sec": dur_sec,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "hr1": hr1, "hr2": hr2,
        "pace": pace,
        "cadence": avg_cad,
        "gct": avg_gct,
        "power": avg_pow,
        "stam_start": stam_start,
        "stam_end": stam_end,
        "zone_pct": zone_pct,
        "decoupling": dc,
        "is_treadmill": is_treadmill,
        "vr": avg_vr,
        "stride": avg_stride,
        "_splits": data.get("_activitySplits"),
        # Plan A Tier 1: Garmin native + derived metrics
        "garmin_aerobic_te":    garmin_aerobic_te,
        "garmin_anaerobic_te":  garmin_anaerobic_te,
        "garmin_te_label":      garmin_te_label,
        "garmin_aerobic_msg":   garmin_aerobic_msg,
        "garmin_anaerobic_msg": garmin_anaerobic_msg,
        "garmin_hr_zone_sec":   garmin_hr_zone_sec,
        "garmin_norm_power":    garmin_norm_power,
    }


# ---------------------------------------------------------------------------
# Easy run depth analysis (Gap 5)
# ---------------------------------------------------------------------------
def analyze_easy_run(r):
    """Additional metrics for Easy Run sessions — aerobic discipline check."""
    z = r["zone_pct"]
    z1_z2 = z[0] + z[1]
    above_easy = z[2] + z[3] + z[4]
    avg_hr = r["avg_hr"]
    e_ceiling = ZONES[1][2]  # top of Z2 (Marathon) = bottom of Z3

    hr_delta = avg_hr - ZONES[0][2]  # diff vs top of Z1 (155 bpm)

    if z1_z2 >= 90:
        grade = "A ✅  — Easy run สมบูรณ์แบบ Truly aerobic"
    elif z1_z2 >= 75:
        grade = "B 🟡 — เกิน Z2 เล็กน้อย ลด Pace ลงอีก 15–20 วิ/km"
    else:
        grade = f"C ⚠️  — Easy run หนักเกินไป ลด HR ให้อยู่ใต้ {HR_ZONES[0][2]} bpm"

    hr_trend = "✅ อยู่ใน E-zone" if hr_delta <= 0 else f"⚠️ เกิน E-zone ceiling {hr_delta:+} bpm"

    dc = r["decoupling"]
    if dc is not None:
        if dc <= 5:
            ae_comment = "✅ Aerobic Efficiency ดีมาก (drift < 5%)"
        elif dc <= 10:
            ae_comment = "🟡 Aerobic Efficiency ปานกลาง (drift 5–10%)"
        else:
            ae_comment = "⚠️ Aerobic Efficiency ต่ำ (drift > 10%) — ควรลด Pace"
    else:
        ae_comment = "N/A (ข้อมูลไม่พอคำนวณ drift)"

    return {
        "z1_z2_pct": z1_z2,
        "above_easy_pct": above_easy,
        "hr_trend": hr_trend,
        "aerobic_efficiency": ae_comment,
        "grade": grade,
    }


# ---------------------------------------------------------------------------
# Quality run grading
# ---------------------------------------------------------------------------
def grade_session(r):
    dc  = r["decoupling"] or 0
    cad = r["cadence"] or 0
    vr  = r["vr"] or 0

    if dc < 5 and cad >= 155 and (vr == 0 or vr <= 8.5):
        return "S 🏆"
    elif dc < 8 and cad >= 153 and (vr == 0 or vr <= 9.5):
        return "A ✅"
    elif dc < 12:
        return "B 🟡"
    else:
        return "C ⚠️"


def flag_issues(r):
    flags = []
    if r["cadence"] and r["cadence"] < BASELINES["cadence_min"]:
        flags.append(f"⚠️  Cadence ต่ำ ({r['cadence']} spm < {BASELINES['cadence_min']} เป้าหมาย) — ตรวจสอบ ACL Compensation")

    gct_threshold = BASELINES["gct_max"] + (BASELINES["gct_treadmill_add"] if r.get("is_treadmill") else 0)
    if r["gct"] and r["gct"] > gct_threshold:
        tag = " (Treadmill +20ms)" if r.get("is_treadmill") else ""
        flags.append(f"⚠️  GCT สูง ({r['gct']}ms > {gct_threshold}ms) — Ground contact นานเกินไป{tag}")

    if r["vr"]:
        if r["vr"] > BASELINES["vr_warn"]:
            flags.append(f"🔴 Vertical Ratio สูง ({r['vr']}%) — สูญเสียพลังงานแนวตั้ง (เป้า ≤{BASELINES['vr_good']}%)")

    if r["power"]:
        if r["power"] < BASELINES["power_min"]:
            flags.append(f"🟡 Power ต่ำ ({r['power']}W < {BASELINES['power_min']}W)")
        elif r["power"] > BASELINES["power_max"]:
            flags.append(f"🟡 Power สูง ({r['power']}W > {BASELINES['power_max']}W)")

    dc = r["decoupling"]
    if dc is not None:
        if dc > BASELINES["decoupling_warn"]:
            flags.append(f"🔴 HR Drift สูงมาก ({dc}%) — Aerobic base ยังไม่รองรับ Pace นี้")
        elif dc > BASELINES["decoupling_good"]:
            flags.append(f"🟡 HR Drift ปานกลาง ({dc}%) — ยอมรับได้แต่ควรสังเกต")

    if r["stam_start"] and r["stam_end"]:
        drain = r["stam_start"] - r["stam_end"]
        if drain > BASELINES["stamina_drain_max"]:
            flags.append(f"🔴 Stamina หมดเร็ว ({drain}% drain) — เซสชันหนักเกิน Recovery 48h+")

    if r.get("bb_start") and r.get("bb_end"):
        bb_drain = r["bb_start"] - r["bb_end"]
        if bb_drain > 35:
            flags.append(f"🔴 Body Battery ลดลงเยอะมาก (-{bb_drain}) — ร่างกายใช้พลังงานเกินในเซสชันนี้")
    return flags


# ---------------------------------------------------------------------------
# BB drain helper
# ---------------------------------------------------------------------------
def get_bb_drain(client, date_str, start_str, duration_sec):
    """Return (bb_start, bb_end) sampled as close as possible to the run's
    actual start/end times. Garmin's bodyBatteryValuesArray is sometimes
    very sparse (as few as 5-6 points/day if the watch isn't tracking
    continuously) — matching to whatever's nearest without a distance check
    can silently pair a point hours away from the run (e.g. that morning's
    BB, or that evening's) and report a huge "drain" that's really just the
    rest of the day's normal drift, not the run. Discard any match more
    than BB_MATCH_MAX_GAP_MS away rather than use a stale value unflagged.
    """
    from datetime import timedelta
    BB_MATCH_MAX_GAP_MS = 20 * 60 * 1000  # 20min — beyond this, don't trust the match
    try:
        bb_data = client.get_body_battery(date_str, date_str)
        if not bb_data or not isinstance(bb_data, list):
            return None, None
        vals = bb_data[0].get("bodyBatteryValuesArray", [])
        if not vals:
            return None, None

        start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        end_dt   = start_dt + timedelta(seconds=duration_sec)
        start_ms = start_dt.timestamp() * 1000
        end_ms   = end_dt.timestamp() * 1000

        bb_start = bb_end = None
        for v in reversed(vals):
            if v[0] <= start_ms:
                if start_ms - v[0] <= BB_MATCH_MAX_GAP_MS:
                    bb_start = int(v[1])
                break
        for v in vals:
            if v[0] >= end_ms:
                if v[0] - end_ms <= BB_MATCH_MAX_GAP_MS:
                    bb_end = int(v[1])
                break
        return bb_start, bb_end
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def format_dur(sec):
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def analyze_laps(splits: dict, is_treadmill: bool = False,
                 rep_speeds_kmh: list | None = None) -> dict | None:
    """Group lap splits into reps (ACTIVE blocks separated by REST/WU/CD).

    Returns dict with: reps[], recoveries[], wu_km, cd_km, structure_str
    Each rep: {n, dist_km, duration_s, pace_min_km, avg_hr, max_hr, lap_ids}
    """
    if not splits or not isinstance(splits, dict):
        return None
    laps = splits.get("lapDTOs") or []
    if not laps:
        return None

    reps = []
    recoveries = []
    strides = []
    wu_km = cd_km = 0.0
    cur = None  # current rep buffer
    rep_n = 0

    # Some workouts (seen on structured-workout pushes) tag every lap
    # "INTERVAL" with no WARMUP/RECOVERY/COOLDOWN at all — the jog-recovery
    # laps between reps are still there, just mistagged as INTERVAL like the
    # work laps. Left alone, the pre-pass below can misfile a slow recovery
    # lap as a "stride" (short + followed by another ACTIVE lap), and reps
    # split by GPS/distance auto-lap (e.g. one 8min rep crossing a 1km mark)
    # get counted as separate reps instead of one. Use PACE, not just
    # duration/tag, to tell recovery jogs and true reps apart.
    try:
        from config import VDOT_PACES as _VP
        _fast_thresh = _VP["M"][1]   # slower than this -> not a stride candidate
        _slow_thresh = _VP["E"][1]   # slower than this -> definitely a recovery jog
    except Exception:
        _fast_thresh, _slow_thresh = 325, 404  # VDOT-40 fallback (M-slow, E-slow)

    def _lap_pace_sec_km(lap):
        dist_km = (lap.get("distance") or 0) / 1000
        dur = lap.get("duration") or 0
        return (dur / dist_km) if dist_km > 0.01 else None

    # Reference work-pace: some sessions (e.g. a treadmill interval with the
    # recovery jog set to a brisk ~9km/h, not a slow shuffle) have recovery
    # laps that are still faster than VDOT_PACES["E"]'s slow end — the
    # absolute _slow_thresh check below misses those entirely, so every lap
    # stays tagged INTERVAL and analyze_laps() can't split reps from
    # recovery, which then makes the caller fall back to whole-session
    # avg HR/pace and misclassify the whole thing as an Easy run. Fix: also
    # flag a lap as recovery when it's meaningfully slower (>=15%) than the
    # session's own fast-lap (work-rep) pace, not just slower than a fixed
    # VDOT-derived floor.
    _fast_lap_paces = [p for p in (_lap_pace_sec_km(l) for l in laps)
                        if p is not None and p <= _fast_thresh]
    _work_pace_ref = (sum(_fast_lap_paces) / len(_fast_lap_paces)
                       if _fast_lap_paces else None)

    # Pre-pass: reclassify short ACTIVE laps as STRIDE when:
    #   - duration < 180s
    #   - NOT followed by RECOVERY/REST (i.e. they precede a real rep)
    #   - pace is actually fast (rules out a short slow recovery-jog lap)
    laps = list(laps)
    for i, lap in enumerate(laps):
        t = (lap.get("intensityType") or "ACTIVE").upper()
        if t in ("ACTIVE", "INTERVAL"):
            dur = lap.get("duration") or 0
            nxt = laps[i + 1] if i + 1 < len(laps) else None
            nxt_t = (nxt.get("intensityType") or "").upper() if nxt else ""
            pace = _lap_pace_sec_km(lap)
            is_fast = pace is not None and pace <= _fast_thresh
            # short ACTIVE + next is ACTIVE (not recovery) + fast pace → stride/lead-in
            if dur < 180 and nxt_t in ("ACTIVE", "INTERVAL") and is_fast:
                lap["_reclassified"] = "STRIDE"
            # slow vs a fixed VDOT floor, OR meaningfully slower than this
            # session's own work-rep pace → jog recovery, mistagged as
            # INTERVAL/ACTIVE by the watch
            elif pace is not None and (
                pace >= _slow_thresh
                or (_work_pace_ref is not None and pace >= _work_pace_ref * 1.15)
            ):
                lap["_reclassified"] = "RECOVERY"

    def _close(buf):
        if not buf or buf["dist_km"] <= 0.05:
            return
        # weight HR by lap duration for accuracy
        total_t = sum(l["duration"] for l in buf["_laps"])
        weighted_hr = sum((l.get("averageHR") or 0) * l["duration"] for l in buf["_laps"])
        avg_hr = weighted_hr / total_t if total_t > 0 else 0
        max_hr = max((l.get("maxHR") or l.get("averageHR") or 0) for l in buf["_laps"])
        pace = (buf["duration_s"] / 60) / buf["dist_km"] if buf["dist_km"] > 0 else 0

        def _wavg(field):
            vals = [(l.get(field) or 0) * l["duration"] for l in buf["_laps"]]
            return sum(vals) / total_t if total_t > 0 else 0

        avg_cadence = _wavg("averageRunCadence")
        avg_power   = _wavg("averagePower")
        avg_gct     = _wavg("groundContactTime")
        avg_vr      = _wavg("verticalRatio")
        avg_stride  = _wavg("strideLength") / 100  # cm → m
        # Garmin TM lap pace unreliable — recompute from averageSpeed if available
        if is_treadmill:
            spds = [l.get("averageSpeed") or 0 for l in buf["_laps"]]
            dur  = [l["duration"] for l in buf["_laps"]]
            if any(s > 0 for s in spds):
                tot_d = sum(s * t for s, t in zip(spds, dur))  # meters
                pace = (sum(dur) / 60) / (tot_d / 1000) if tot_d > 0 else pace
        buf.update({
            "avg_hr": round(avg_hr, 1),
            "max_hr": int(max_hr) if max_hr else 0,
            "pace_min_km": round(pace, 3),
            "avg_cadence": round(avg_cadence),
            "avg_power": round(avg_power),
            "avg_gct": round(avg_gct),
            "avg_vr": round(avg_vr, 1),
            "avg_stride": round(avg_stride, 2),
        })
        del buf["_laps"]
        reps.append(buf)

    for lap in laps:
        t = (lap.get("intensityType") or "ACTIVE").upper()
        if lap.get("_reclassified") == "STRIDE":
            t = "STRIDE"
        elif lap.get("_reclassified") == "RECOVERY":
            t = "RECOVERY"
        d = (lap.get("distance") or 0) / 1000
        dur = lap.get("duration") or 0
        if t == "WARMUP":
            wu_km += d
            if cur:
                _close(cur); cur = None
        elif t == "COOLDOWN":
            cd_km += d
            if cur:
                _close(cur); cur = None
        elif t == "STRIDE":
            strides.append({"dist_km": d, "duration_s": dur,
                            "avg_hr": lap.get("averageHR")})
            if cur:
                _close(cur); cur = None
        elif t == "RECOVERY" or t == "REST":
            recoveries.append({"dist_km": d, "duration_s": dur,
                               "avg_hr": lap.get("averageHR")})
            if cur:
                _close(cur); cur = None
        else:  # ACTIVE / INTERVAL / unknown → counts as rep
            if cur is None:
                rep_n += 1
                cur = {"n": rep_n, "dist_km": 0.0, "duration_s": 0.0, "_laps": []}
            cur["dist_km"]    += d
            cur["duration_s"] += dur
            cur["_laps"].append(lap)
    if cur:
        _close(cur)

    if not reps:
        return None

    # Override pace from user-provided actual TM speeds (km/h)
    if rep_speeds_kmh and is_treadmill:
        for i, rep in enumerate(reps):
            if i < len(rep_speeds_kmh) and rep_speeds_kmh[i] > 0:
                kmh = rep_speeds_kmh[i]
                rep["pace_min_km"] = 60.0 / kmh
                rep["dist_km"] = kmh * (rep["duration_s"] / 3600)
                rep["_speed_override"] = kmh

    structure = f"WU {wu_km:.1f}k"
    if strides:
        structure += f" + {len(strides)}×stride"
    structure += f" → {len(reps)}× rep → CD {cd_km:.1f}k"
    return {
        "reps": reps,
        "recoveries": recoveries,
        "strides": strides,
        "wu_km": round(wu_km, 2),
        "cd_km": round(cd_km, 2),
        "structure": structure,
    }


def print_lap_analysis(lap_data: dict, is_treadmill: bool, has_override: bool = False):
    """Print per-rep table with HR drift between reps."""
    reps = lap_data["reps"]
    recs = lap_data["recoveries"]
    if is_treadmill:
        tm_tag = " ✅ TM speeds (manual)" if has_override else " ⚠️ TM pace UNRELIABLE — use --rep-speeds"
    else:
        tm_tag = ""

    print(f"\n{'─'*68}")
    print(f"🔁 PER-REP ANALYSIS — {lap_data['structure']}{tm_tag}")
    print(f"{'─'*68}")
    print(f"  {'Rep':<5}{'Dist':<8}{'Time':<8}{'Pace':<10}{'avgHR':<8}{'maxHR':<8}{'Δ HR':<8}"
          f"{'Cad':<6}{'Pwr':<6}{'GCT':<6}")
    base_hr = reps[0]["avg_hr"] if reps else 0
    for rep in reps:
        pm = int(rep["pace_min_km"])
        ps = int((rep["pace_min_km"] - pm) * 60)
        pace_str = f"{pm}:{ps:02d}/km"
        t_min = int(rep["duration_s"] // 60)
        t_sec = int(rep["duration_s"] % 60)
        time_str = f"{t_min}:{t_sec:02d}"
        delta = rep["avg_hr"] - base_hr
        delta_str = f"{delta:+.1f}" if rep["n"] > 1 else "—"
        print(f"  {rep['n']:<5}{rep['dist_km']:<8.2f}{time_str:<8}"
              f"{pace_str:<10}{rep['avg_hr']:<8.1f}{rep['max_hr']:<8}{delta_str:<8}"
              f"{rep.get('avg_cadence', 0):<6}{rep.get('avg_power', 0):<6}{rep.get('avg_gct', 0):<6}")

    if recs:
        avg_rec = sum(r["duration_s"] for r in recs) / len(recs)
        print(f"\n  Recovery: {len(recs)}× avg {int(avg_rec)}s")

    # Coaching insight
    if len(reps) >= 2:
        drift = reps[-1]["avg_hr"] - reps[0]["avg_hr"]
        print(f"\n  📊 HR Drift across reps: {drift:+.1f} bpm")
        if drift <= 3:
            print(f"     ✅ Excellent — pacing/aerobic capacity ตรง target")
        elif drift <= 7:
            print(f"     🟡 Normal — cardiac drift จาก lactate + fatigue ปกติ")
        elif drift <= 12:
            print(f"     ⚠️  High — เริ่มหนักเกิน อาจ over-pace rep แรก")
        else:
            print(f"     🔴 Excessive — ลด pace 5-10s/km หรือลด rep count")

        # Pace consistency
        paces = [r["pace_min_km"] for r in reps]
        pace_range = max(paces) - min(paces)
        if pace_range > 0.5:  # >30s/km variation
            print(f"     ⚠️  Pace variation {pace_range*60:.0f}s/km — ไม่สม่ำเสมอ")


def print_report(act_id, date_str, r):
    pm  = int(r["pace"])
    ps  = int((r["pace"] - pm) * 60)
    dc  = r["decoupling"]
    dc_str   = f"{dc}%" if dc is not None else "N/A"
    dc_emoji = "✅" if dc is not None and dc <= 5 else ("🟡" if dc is not None and dc <= 10 else "🔴")

    stam_str = f"{r['stam_start']}% → {r['stam_end']}%" if r["stam_start"] else "N/A"
    bb_str   = f"{r.get('bb_start') if r.get('bb_start') is not None else 'N/A'} → {r.get('bb_end') if r.get('bb_end') is not None else 'N/A'}"

    if r.get("bb_start") and r.get("bb_end"):
        bb_drain = r["bb_start"] - r["bb_end"]
        bb_str += f" (-{bb_drain})"
    if r["stam_start"] and r["stam_end"]:
        stam_drain = r["stam_start"] - r["stam_end"]
        stam_str += f" (-{stam_drain}%)"

    hr_drift   = f"{r['hr1']}→{r['hr2']} bpm" if r["hr1"] else "N/A"
    z          = r["zone_pct"]
    treadmill_tag = " (Treadmill 🏃‍♂️)" if r.get("is_treadmill") else ""
    vr_str     = f"{r['vr']}%" if r["vr"] else "N/A"
    stride_str = f"{r['stride']}m" if r["stride"] else "N/A"
    form_scope_tag = "  (work reps only)" if r.get("form_scope") == "work_reps" else "  (whole session)"
    is_easy    = r["session_type"] == "Easy Run"

    print(f"""
{'='*55}
📊 POST-SESSION REPORT | {date_str}{treadmill_tag}
{'='*55}
🏷️  Type: {r['session_type']} | Activity ID: {act_id}
⏱️  Time: {format_dur(r['duration_sec'])}
⚡ Pace(active): {pm}:{ps:02d}/km

❤️  HR:  avg {r['avg_hr']} / max {r['max_hr']} bpm
🔄 Drift: {hr_drift} | Decoupling: {dc_str} {dc_emoji}

📊 Zone Distribution:
   Z1(E): {z[0]}%  Z2(M): {z[1]}%  Z3(T): {z[2]}%  Z4(I): {z[3]}%  Z5(R): {z[4]}%

🦵 Cadence: {r['cadence']} spm  |  GCT: {r['gct']}ms  |  Stride: {stride_str}{form_scope_tag}
📈 Power: {r['power']}W  |  Vertical Ratio: {vr_str}
📉 Stamina: {stam_str}
🔋 Body Battery: {bb_str}""")

    # --- Garmin Native Assessment (Plan A Tier 1) ---
    ga_te = r.get("garmin_aerobic_te")
    gn_te = r.get("garmin_anaerobic_te")
    if ga_te is not None or gn_te is not None:
        def _te_icon(v):
            if v is None: return ""
            if v >= 4.0: return "🔥 highly improving"
            if v >= 3.0: return "🟢 improving"
            if v >= 2.0: return "🟡 maintaining"
            if v >= 1.0: return "🔵 minor benefit"
            return "⚪ no benefit"
        label = r.get("garmin_te_label") or ""
        label_str = f" [{label}]" if label else ""
        print(f"\n🎯 GARMIN ASSESSMENT{label_str}")
        if ga_te is not None:
            print(f"   Aerobic TE   : {ga_te:.1f}  {_te_icon(ga_te)}")
        if gn_te is not None:
            print(f"   Anaerobic TE : {gn_te:.1f}  {_te_icon(gn_te)}")
        msg = r.get("garmin_aerobic_msg") or ""
        if msg:
            # e.g. "HIGHLY_IMPROVING_LACTATE_THRESHOLD_13" → readable
            readable = msg.replace("_", " ").title()
            print(f"   Stimulus     : {readable}")

    # --- Garmin HR Zone Time Distribution (Plan A Tier 1) ---
    zone_sec = r.get("garmin_hr_zone_sec") or []
    if zone_sec and sum(zone_sec) > 0:
        total_s = sum(zone_sec)
        z_pcts = [round(z / total_s * 100, 1) for z in zone_sec]
        polar_low  = z_pcts[0] + z_pcts[1]    # Z1+Z2 (easy)
        polar_high = z_pcts[3] + z_pcts[4]    # Z4+Z5 (hard)
        polar_mid  = z_pcts[2]                # Z3 (threshold gray)
        print(f"\n📊 GARMIN HR ZONE TIME")
        z_labels = ["Z1(E)", "Z2(M)", "Z3(T)", "Z4(I)", "Z5(R)"]
        for lbl, s_, p in zip(z_labels, zone_sec, z_pcts):
            mm, ss = divmod(int(s_), 60)
            bar = "█" * int(p / 3)
            print(f"   {lbl}: {mm:>3}:{ss:02d}  {p:>5.1f}%  {bar}")
        # Polarization check (80/20 rule per Seiler)
        polar_icon = "✅" if (polar_low >= 75 or polar_high >= 75) else "🟡"
        print(f"   Polarization: Low {polar_low:.0f}% / Mid {polar_mid:.0f}% / High {polar_high:.0f}%  {polar_icon}")

    # --- Power/HR Efficiency Ratio (Plan A Tier 1) ---
    if r.get("power") and r.get("avg_hr"):
        ratio = r["power"] / r["avg_hr"]
        norm_p = r.get("garmin_norm_power")
        np_str = f" | NP/AP {norm_p/r['power']:.3f}" if norm_p and r["power"] else ""
        # Reference: HM PB ธ.ค.25 = 1.76 W/bpm
        ratio_icon = "🟢" if ratio >= 1.60 else ("🟡" if ratio >= 1.40 else "🔴")
        print(f"\n⚡ EFFICIENCY")
        print(f"   Power/HR     : {ratio:.2f} W/bpm  {ratio_icon}  (HM PB ref: 1.76){np_str}")

    if is_easy:
        ea = analyze_easy_run(r)
        print(f"""
{'─'*40}
🟢 EASY RUN ANALYSIS
   Z1+Z2 Combined : {ea['z1_z2_pct']}%  |  Above Easy: {ea['above_easy_pct']}%
   HR vs E-ceiling: {ea['hr_trend']}
   Aerobic Drift  : {ea['aerobic_efficiency']}
   Aerobic Grade  : {ea['grade']}""")
    else:
        # Per-rep lap analysis (quality sessions)
        splits = r.get("_splits")
        if splits:
            lap_data = analyze_laps(splits, r.get("is_treadmill", False),
                                    rep_speeds_kmh=r.get("_rep_speeds_override"))
            if lap_data:
                print_lap_analysis(lap_data, r.get("is_treadmill", False),
                                   has_override=bool(r.get("_rep_speeds_override")))

        grade = grade_session(r)
        print(f"\n🎯 Session Grade: {grade}")

    flags = flag_issues(r)
    print(f"{'─'*40}")
    if flags:
        print("📌 Issues Found:")
        for f in flags:
            print(f"   {f}")
    else:
        print("   ✅ ไม่พบปัญหา — เซสชันสมบูรณ์แบบ")
    print("=" * 55)


# ---------------------------------------------------------------------------
# Treadmill phase helpers
# ---------------------------------------------------------------------------
def _speed_to_pace_str(speed_kmh: float) -> str:
    """แปลง km/h → pace string m:ss/km"""
    if speed_kmh <= 0:
        return "N/A"
    total_sec = 3600 / speed_kmh
    m, s = divmod(int(total_sec), 60)
    return f"{m}:{s:02d}/km"


def _classify_speed(speed_kmh: float) -> str:
    """จัดโซน JD จาก treadmill speed (km/h) — derive boundaries จาก config VDOT_PACES
    (auto-track VDOT; เดิม hardcode VDOT-38 magic numbers ที่ผิด — 12.0km/h=5:00=T
    ถูก label เป็น 'R' ทั้งที่ R จริง=4:20-4:30). Boundary = midpoint ระหว่างขอบ
    zone ที่ติดกัน (sec/km). Fixed 2026-06-09.
    """
    pace_sec = 3600 / speed_kmh if speed_kmh > 0 else 999
    try:
        from config import VDOT_PACES as VP
        # zone (fast_lo, slow_hi) sec/km — faster zone first
        r_hi, i_lo = VP["R"][1], VP["I"][0]
        i_hi, t_lo = VP["I"][1], VP["T"][0]
        t_hi, m_lo = VP["T"][1], VP["M"][0]
        m_hi, e_lo = VP["M"][1], VP["E"][0]
        b_ri = (r_hi + i_lo) / 2   # R|I
        b_it = (i_hi + t_lo) / 2   # I|T
        b_tm = (t_hi + m_lo) / 2   # T|M
        b_me = (m_hi + e_lo) / 2   # M|E
    except Exception:
        b_ri, b_it, b_tm, b_me = 270, 290, 313, 331  # VDOT-40 fallback
    if pace_sec <= b_ri:
        return "R"
    elif pace_sec <= b_it:
        return "I"
    elif pace_sec <= b_tm:
        return "T"
    elif pace_sec <= b_me:
        return "M"
    else:
        return "E"


def _prompt_treadmill_phases() -> list | None:
    """
    ถาม user กรอก treadmill phases หลัง session
    Format: "8.4x5,11.2x7,8.4x2"  (speed_kmh × distance_km)
    ใช้ distance เพราะดูจากหน้าจอลู่วิ่งได้ตรงกว่า
    """
    print("\n🏃 Treadmill Session — กรอก Phases (หรือ Enter เพื่อ skip)")
    print("   Format: speed1xkm1,speed2xkm2,...  เช่น  8.4x5,11.2x7,8.4x2")
    raw = input("   Phases: ").strip()
    if not raw:
        return None

    phases = []
    for part in raw.split(","):
        part = part.strip()
        if "x" not in part:
            continue
        try:
            spd_str, km_str = part.split("x", 1)
            speed      = float(spd_str)
            dist_km    = float(km_str)
            dur_min    = dist_km / speed * 60  # คำนวณ duration จาก distance ÷ speed
            phases.append({
                "speed_kmh":    speed,
                "distance_km":  dist_km,
                "duration_min": round(dur_min, 1),
                "pace":         _speed_to_pace_str(speed),
                "zone":         _classify_speed(speed),
            })
        except ValueError:
            print(f"   ⚠️  ข้ามส่วนที่อ่านไม่ได้: {part}")

    return phases if phases else None


def _treadmill_effective_stats(phases: list) -> dict:
    """คำนวณ effective pace + session_type จาก treadmill phases (distance-based)"""
    total_km  = sum(p["distance_km"] for p in phases)
    total_dur = sum(p["duration_min"] for p in phases)
    if total_km == 0 or total_dur == 0:
        return {}

    # Effective pace = total distance ÷ total time (แม่นกว่า weighted avg speed)
    effective_pace_min = total_dur / total_km
    eff_m = int(effective_pace_min)
    eff_s = int((effective_pace_min - eff_m) * 60)
    effective_pace = f"{eff_m}:{eff_s:02d}/km"

    peak_speed  = max(p["speed_kmh"] for p in phases)
    peak_zone   = _classify_speed(peak_speed)

    quality_km  = sum(p["distance_km"] for p in phases if p["zone"] in ("T", "I", "R", "M"))
    quality_pct = round(quality_km / total_km * 100) if total_km > 0 else 0

    zone_to_type = {
        "R": "Repetition (R)",
        "I": "Interval (I)",
        "T": "Threshold (T)",
        "M": "Marathon Pace",
        "E": "Easy Run",
    }
    return {
        "effective_pace":         effective_pace,
        "effective_session_type": zone_to_type.get(peak_zone, "Easy Run"),
        "peak_speed_kmh":         peak_speed,
        "peak_zone":              peak_zone,
        "quality_pct":            quality_pct,
        "total_km_actual":        round(total_km, 2),
        "total_dur_min":          round(total_dur, 1),
    }


# ---------------------------------------------------------------------------
# Session log (Gap 1 — auto-update)
# ---------------------------------------------------------------------------
def load_session_log():
    SESSION_LOG_PATH.parent.mkdir(exist_ok=True)
    if SESSION_LOG_PATH.exists():
        with open(SESSION_LOG_PATH) as f:
            return json.load(f)
    return {"sessions": []}


def save_session_log(log):
    SESSION_LOG_PATH.parent.mkdir(exist_ok=True)
    with open(SESSION_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def log_session(act_id, date_str, r):
    """Append session result to QualitySessionLog/sessions.json."""
    log = load_session_log()

    # Check if already logged
    existing_ids = {s.get("activity_id") for s in log["sessions"]}
    if act_id in existing_ids:
        print(f"ℹ️  Session {act_id} already in log — skipping.")
        return

    pm = int(r["pace"])
    ps = int((r["pace"] - pm) * 60)

    # --- Treadmill phase logging ---
    treadmill_phases = None
    effective_stats  = {}
    rep_speeds_override = r.get("_rep_speeds_override")
    if r.get("is_treadmill") and rep_speeds_override:
        # Skip interactive prompt — use --rep-speeds (non-interactive path)
        kmh = max(rep_speeds_override)
        rep_pace_sec = 3600 / kmh
        m, s = divmod(int(rep_pace_sec), 60)
        effective_stats = {"effective_pace": f"{m}:{s:02d}/km",
                           "quality_pct": None,
                           "effective_session_type": None}
        # Classify session type from rep-window HR (not session avg — WU/CD biased)
        rep_pace_min = rep_pace_sec / 60
        splits = r.get("_splits")
        rep_hr = r["avg_hr"]
        if splits:
            lap_data = analyze_laps(splits, True, rep_speeds_override)
            if lap_data and lap_data["reps"]:
                hrs = [rep["avg_hr"] for rep in lap_data["reps"] if rep.get("avg_hr")]
                if hrs:
                    rep_hr = sum(hrs) / len(hrs)
        eff_type = classify_session(rep_hr, rep_pace_min)
        if eff_type:
            r["session_type"] = eff_type
            effective_stats["effective_session_type"] = eff_type
        print(f"   ✅ Rep speeds applied | Effective pace: {effective_stats['effective_pace']}"
              f" | Type: {r['session_type']}")
    elif r.get("is_treadmill"):
        treadmill_phases = _prompt_treadmill_phases()
        if treadmill_phases:
            effective_stats = _treadmill_effective_stats(treadmill_phases)
            # Override session_type with actual treadmill peak zone
            eff_type = effective_stats.get("effective_session_type")
            if eff_type:
                r["session_type"] = eff_type
            print(f"   ✅ Phases logged | Effective pace: {effective_stats.get('effective_pace')}"
                  f" | Type: {r['session_type']} | Quality: {effective_stats.get('quality_pct')}%")

    is_easy = r["session_type"] == "Easy Run"
    grade   = "—" if is_easy else grade_session(r)

    # Use actual treadmill pace if available, else Garmin recorded pace
    logged_pace = effective_stats.get("effective_pace") or f"{pm}:{ps:02d}/km"

    entry = {
        "date":         date_str,
        "activity_id":  act_id,
        "session_type": r["session_type"],
        "is_treadmill": r.get("is_treadmill", False),
        "pace":         logged_pace,
        "avg_hr":       r["avg_hr"],
        "max_hr":       r["max_hr"],
        "cadence":      r["cadence"],
        "power":        r["power"],
        "gct":          r["gct"],
        "vr":           r["vr"],
        "stride":       r["stride"],
        "decoupling":   r["decoupling"],
        "zone_pct":     r["zone_pct"],
        "stam_start":   r["stam_start"],
        "stam_end":     r["stam_end"],
        "bb_start":     r.get("bb_start"),
        "bb_end":       r.get("bb_end"),
        "grade":        grade,
        "logged_at":    datetime.now().isoformat(timespec="seconds"),
    }

    # Treadmill-specific fields (เพิ่มเฉพาะถ้ามี phases)
    if treadmill_phases:
        entry["treadmill_phases"]  = treadmill_phases
        entry["total_km_actual"]   = effective_stats.get("total_km_actual")
        entry["quality_pct"]       = effective_stats.get("quality_pct")

    log["sessions"].append(entry)
    # Sort by activity_id (monotonic with time), not date-string — avoids
    # a non-ISO "date" value sorting above real dates and pinning a stale
    # session at index 0 (see stamina_patcher.py / session_logger.py).
    log["sessions"].sort(key=lambda s: s.get("activity_id", 0), reverse=True)
    save_session_log(log)
    print(f"✅ Session logged → {SESSION_LOG_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Analyze a Garmin running session (JD Protocol)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--latest", action="store_true", help="Analyze most recent activity")
    group.add_argument("--id", type=int, help="Garmin Activity ID to analyze")
    parser.add_argument("--update-log", action="store_true",
                        help="Append session result to QualitySessionLog/sessions.json")
    parser.add_argument("--rep-speeds", type=str, default=None,
                        help="TM only: actual rep speeds in km/h, comma-separated "
                             "(e.g. '11.5,11.5,11.5'). Overrides unreliable Garmin TM pace.")
    args = parser.parse_args()

    rep_speeds = None
    if args.rep_speeds:
        try:
            rep_speeds = [float(x.strip()) for x in args.rep_speeds.split(",")]
        except ValueError:
            print("⚠️  --rep-speeds parse error, ignoring")

    if args.latest:
        act_id, date_str = get_latest_activity_id()
        print(f"📡 Fetching latest activity: {act_id} ({date_str[:10]})...")
    else:
        act_id = args.id
        date_str = "custom"
        print(f"📡 Fetching activity ID: {act_id}...")

    data      = fetch_activity(act_id)
    r         = analyze(data)

    start_str, duration = get_activity_meta(act_id)
    if start_str:
        try:
            client = get_garmin_client()
            d_str  = start_str[:10]
            bb_start, bb_end = get_bb_drain(client, d_str, start_str, duration)
        except Exception as e:
            print(f"⚠️  Body Battery fetch failed ({e}) — skipping")
            bb_start, bb_end = None, None
        r["bb_start"] = bb_start
        r["bb_end"]   = bb_end
        report_date = start_str[:10]
    else:
        r["bb_start"] = r["bb_end"] = None
        report_date = str(date_str)[:10]

    r["_rep_speeds_override"] = rep_speeds
    print_report(act_id, report_date, r)

    # Long Run (>=14km) → show Energy Efficiency Score inline so it never
    # depends on remembering to run energy_efficiency_scorer.py separately.
    s = (data.get("_activitySummary") or {}).get("summaryDTO", {})
    dist_m = data.get("distance") or s.get("distance") or 0
    total_km = round(dist_m / 1000, 2)
    if total_km >= 14.0 and r.get("stam_start") and r.get("stam_end") and r.get("bb_start") and r.get("bb_end"):
        try:
            from energy_efficiency_scorer import _efficiency_score, _score_label
            stamina_drain_pct = r["stam_start"] - r["stam_end"]
            bb_drop = r["bb_start"] - r["bb_end"]
            score = _efficiency_score(stamina_drain_pct, bb_drop)
            print(f"\n⚡ ENERGY EFFICIENCY: {score}  {_score_label(score)}"
                  f"  (stamina retained/BB unit — {total_km}km long run)")
        except Exception as e:
            print(f"\n⚠️  Energy efficiency score unavailable ({e})")

    if args.update_log:
        log_session(act_id, report_date, r)

    # Auto-log to sessions_master.json — unconditional for Easy Run (matches
    # run_post_easy.sh, which intentionally never passes --update-log since
    # easy runs never belong in sessions.json / VDOT tracking at all). For
    # every other (quality) type, require --update-log so it can't write
    # sessions_master.json without the matching sessions.json entry from
    # log_session() above — see CLAUDE.md's single-source-of-truth policy.
    # Previously this ran unconditionally for every session type, so an
    # ad-hoc "just check this run" call on a QUALITY session silently wrote
    # sessions_master.json without sessions.json, causing 3+ weeks of drift
    # (vdot_estimator/race_predictor/skill_sync all read sessions.json only).
    is_easy_session = r.get("session_type") == "Easy Run"
    if is_easy_session or args.update_log:
        try:
            from session_logger import auto_log
            auto_log(act_id, data, r, report_date)
        except Exception:
            pass


if __name__ == "__main__":
    main()
