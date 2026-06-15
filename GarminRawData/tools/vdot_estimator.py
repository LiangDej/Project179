#!/usr/bin/env python3
"""
vdot_estimator.py — Breakthrough Session Detector + VDOT Estimate

ตรวจจับ sessions ที่ทำผลงานทะลุเป้า (Breakthrough) และประเมิน VDOT estimate
ข้อสำคัญ: VDOT อัปเดตจริงต้องใช้ผลแข่งเท่านั้น (JD principle)
           ตัวนี้ให้ "estimate" เพื่อ monitor เท่านั้น ไม่ใช้แทนค่าจากการแข่ง

Usage:
    python3 vdot_estimator.py               # วิเคราะห์ 8 สัปดาห์ล่าสุด
    python3 vdot_estimator.py --weeks 12
    python3 vdot_estimator.py --json
"""

import sys
import json
import math
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR      = Path(__file__).parent.parent
TOOLS_DIR     = Path(__file__).parent
COACH_MCP     = BASE_DIR.parent / "skills" / "garmin_coach_mcp"
DATA_FILE     = BASE_DIR / "running_activities_all.json"
SESSIONS_JSON = BASE_DIR / "QualitySessionLog" / "sessions.json"
MASTER_JSON   = BASE_DIR / "QualitySessionLog" / "sessions_master.json"

# Session types that count as quality (not easy) — matches sessions.json values
QUALITY_TYPES = {"Threshold (T)", "Interval (I)", "Tempo", "Fast Finish"}

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from config import ATHLETE                             # noqa: E402
from vdot_math import compute_vdot as _vdot_compute   # noqa: E402

CURRENT_VDOT = ATHLETE["vdot"]

# Breakthrough threshold: VDOT estimate must exceed current by >= this amount
BREAKTHROUGH_THRESHOLD = 1.0


def _compute_vdot(distance_m: float, duration_min: float) -> float | None:
    """Delegate to vdot_math (single source of truth). min_dist 3000m for quality sessions."""
    result = _vdot_compute(distance_m, duration_min, min_dist_m=3000)
    return round(result, 1) if result else None


def _fmt_pace(sec_km: float) -> str:
    m, s = divmod(int(sec_km), 60)
    return f"{m}:{s:02d}/km"


def _load_quality_sessions(weeks: int) -> list:
    """Load quality sessions from sessions.json + sessions_master.json.

    Preferred over raw Garmin data because:
    - Uses corrected quality-lap pace (tm_patch applied for TM sessions)
    - Uses quality_km only (excludes warm-up / cool-down from VDOT estimate)
    Returns [] if sessions.json is missing or has no usable quality data.
    """
    if not SESSIONS_JSON.exists():
        return []

    with open(SESSIONS_JSON, encoding="utf-8") as f:
        sessions_data = json.load(f)

    # Build quality_km + quality_pace index from sessions_master
    # quality_pace = weighted avg pace of laps with role="quality" (excludes warmup/cooldown)
    quality_km_idx: dict[int, float] = {}
    quality_pace_idx: dict[int, float] = {}   # activity_id → avg pace_sec from quality laps
    if MASTER_JSON.exists():
        with open(MASTER_JSON, encoding="utf-8") as f:
            master = json.load(f)
        for s in master.get("sessions", []):
            aid = s.get("activity_id")
            if not aid:
                continue
            aid = int(aid)
            qkm = s.get("quality_km") or s.get("total_km") or 0
            quality_km_idx[aid] = float(qkm)

            # Compute weighted avg pace from quality laps
            laps = s.get("laps") or []
            q_laps = [l for l in laps if l.get("role") == "quality"]
            if q_laps:
                total_dur = sum(l.get("duration_s", 0) for l in q_laps)
                total_dist = sum(l.get("distance_km", 0) for l in q_laps) * 1000  # metres
                if total_dist > 0:
                    quality_pace_idx[aid] = total_dur / total_dist * 1000  # sec/km

    cutoff = date.today() - timedelta(weeks=weeks)
    results = []

    for s in sessions_data.get("sessions", []):
        if s.get("session_type") not in QUALITY_TYPES:
            continue

        dt_str = s.get("date", "")
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dt < cutoff:
            continue

        aid = s.get("activity_id")
        aid_int = int(aid) if aid else -1

        # Quality distance from sessions_master
        quality_km = quality_km_idx.get(aid_int, 0)
        if quality_km < 3.0:
            continue

        is_treadmill = s.get("is_treadmill", False)

        # Pace source priority:
        # - TM sessions: use sessions.json pace (belt_speed corrected by tm_patch)
        # - GPS sessions: use weighted avg quality-lap pace from sessions_master laps
        #   (sessions.json pace is session-avg including warmup/cooldown — inaccurate)
        if not is_treadmill and aid_int in quality_pace_idx:
            pace_sec = quality_pace_idx[aid_int]
            pace_source_used = "quality_laps"
        else:
            pace_str = (s.get("pace") or "").replace("/km", "").strip()
            if not pace_str or ":" not in pace_str:
                continue
            try:
                pm, ps = pace_str.split(":")
                pace_sec = int(pm) * 60 + int(ps)
            except ValueError:
                continue
            pace_source_used = s.get("pace_source") or "gps"

        # Heat correction for outdoor Bangkok sessions (Ely et al. 2007).
        # GPS pace in heat is penalized → correct back to cool-weather equivalent.
        # IMPROVED 2026-06-09: use month-aware early-morning temp (bangkok_climate)
        # instead of a flat 30°C, which over-corrected cool-season runs (Nov–Feb
        # mornings ~24–26°C) and inflated the VDOT estimate. Corrected pace =
        # actual_pace / (1 + penalty) → faster equiv pace = higher VDOT.
        heat_corrected = False
        pace_sec_for_vdot = pace_sec
        assumed_temp_c = None
        if not is_treadmill:
            try:
                from bangkok_climate import morning_temp_c, ely_penalty_fraction
                assumed_temp_c = morning_temp_c(dt.month)
                ely_penalty = ely_penalty_fraction(assumed_temp_c)
            except Exception:
                assumed_temp_c = 30.0  # fallback
                ely_penalty = max(0.0, (assumed_temp_c - 13.0) * 0.004)
            pace_sec_for_vdot = pace_sec / (1 + ely_penalty)
            heat_corrected = True

        dist_m  = quality_km * 1000
        dur_min = quality_km * pace_sec_for_vdot / 60
        vdot    = _compute_vdot(dist_m, dur_min)

        results.append({
            "date":           dt_str,
            "distance_km":    round(quality_km, 2),
            "duration_min":   round(dur_min, 1),
            "avg_pace":       _fmt_pace(pace_sec),
            "avg_pace_adj":   _fmt_pace(pace_sec_for_vdot) if heat_corrected else None,
            "avg_hr":         s.get("avg_hr"),
            "vdot":           vdot,
            "session_type":   s.get("session_type", ""),
            "is_treadmill":   is_treadmill,
            "heat_corrected": heat_corrected,
            "pace_source":    pace_source_used,
        })

    results.sort(key=lambda x: x["date"])
    return results


def analyze(weeks: int) -> dict:
    # ── Prefer sessions.json (quality-lap corrected data) over raw Garmin ──────
    sessions = _load_quality_sessions(weeks)
    data_source = "sessions.json (quality laps)"

    # ── Fallback: raw Garmin data (total session avg — less accurate) ─────────
    if not sessions:
        from activity_loader import load_activities_merged
        activities = load_activities_merged()   # file + live overlay (กัน stale)
        if not activities:
            return {"error": "No activities available (file + live). Run fetch_incremental.py first."}

        cutoff = date.today() - timedelta(weeks=weeks)
        data_source = "running_activities_all.json (fallback)"

        for a in activities:
            dt_str = a.get("startTimeLocal", "")[:10]
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if dt < cutoff:
                continue

            dist_m   = a.get("distance") or 0
            dur_s    = a.get("duration") or 0
            avg_hr   = a.get("averageHR") or a.get("averageHeartRate")
            avg_spd  = a.get("averageSpeed") or 0

            if dist_m < 3000 or dur_s < 600:
                continue

            dur_min  = dur_s / 60
            pace_sec = round(1000 / avg_spd) if avg_spd > 0 else None

            # Skip Easy / Long runs (pace ≤ 6:00/km = 360 sec/km)
            if pace_sec is None or pace_sec > 360:
                continue

            vdot = _compute_vdot(dist_m, dur_min)
            sessions.append({
                "date":         dt_str,
                "distance_km":  round(dist_m / 1000, 2),
                "duration_min": round(dur_min, 1),
                "avg_pace":     _fmt_pace(pace_sec) if pace_sec else "N/A",
                "avg_hr":       avg_hr,
                "vdot":         vdot,
                "is_treadmill": False,
                "pace_source":  "gps",
            })

    sessions.sort(key=lambda x: x["date"])

    # Exclude TM sessions (belt_speed / HR-inferred pace) from primary VDOT estimate
    gps_sessions = [s for s in sessions if not s.get("is_treadmill")]
    tm_sessions  = [s for s in sessions if s.get("is_treadmill")]

    vdot_values = [s["vdot"] for s in gps_sessions if s["vdot"]]

    # Rolling peak: best VDOT over last 4 weeks (GPS sessions only)
    recent_cutoff = date.today() - timedelta(weeks=4)
    recent_vdots  = [s["vdot"] for s in gps_sessions
                     if s["vdot"] and s["date"] >= recent_cutoff.isoformat()]
    peak_recent   = max(recent_vdots) if recent_vdots else None

    # Breakthroughs: sessions where VDOT > CURRENT_VDOT + threshold
    breakthroughs = [
        s for s in gps_sessions
        if s["vdot"] and s["vdot"] >= CURRENT_VDOT + BREAKTHROUGH_THRESHOLD
    ]

    # VDOT estimate: weighted average of top-3 recent GPS quality sessions
    top3 = sorted(recent_vdots, reverse=True)[:3]
    estimated_vdot = round(sum(top3) / len(top3), 1) if top3 else None

    # Trend (GPS sessions, all weeks)
    trend_note = None
    if len(vdot_values) >= 4:
        first_avg = sum(vdot_values[:2]) / 2
        last_avg  = sum(vdot_values[-2:]) / 2
        delta     = round(last_avg - first_avg, 1)
        if delta >= 0.5:
            trend_note = f"VDOT +{delta} 📈 improving over {weeks}w"
        elif delta <= -0.5:
            trend_note = (
                f"VDOT {delta} 📉 pace-based estimate ลดลง — "
                f"อาจเกิดจาก heat penalty (Bangkok) ไม่ใช่ fitness จริง "
                f"→ ดู TM sessions เปรียบเทียบ"
            )
        else:
            trend_note = f"VDOT stable (±{delta}) — maintenance"

    # Heat gap: GPS estimate vs official VDOT
    heat_gap = round(CURRENT_VDOT - estimated_vdot, 1) if estimated_vdot else None

    return {
        "analysis_weeks":     weeks,
        "current_vdot":       CURRENT_VDOT,
        "data_source":        data_source,
        "vdot_estimate":      estimated_vdot,
        "vdot_estimate_note": "⚠️ Estimate only — อัปเดต VDOT จริงจากผลแข่งเท่านั้น (JD principle)",
        "heat_gap":           heat_gap,
        "peak_vdot_4w":       peak_recent,
        "trend":              trend_note,
        "breakthrough_count": len(breakthroughs),
        "breakthroughs":      breakthroughs,
        "all_sessions":       gps_sessions,
        "tm_sessions":        tm_sessions,
    }


def print_report(r: dict):
    if "error" in r:
        print(f"❌ {r['error']}")
        return

    print(f"\n{'='*57}")
    print(f"🔬 VDOT ESTIMATOR — {r['analysis_weeks']} สัปดาห์ย้อนหลัง")
    print(f"{'='*57}")
    print(f"   Data source: {r.get('data_source', 'unknown')}")
    print(f"\n📊 VDOT Summary:")
    print(f"   Official VDOT (config):  {r['current_vdot']}")
    if r['vdot_estimate']:
        print(f"   Estimate (top-3 GPS):    {r['vdot_estimate']}  ← GPS sessions only")
        print(f"   ⚠️  {r['vdot_estimate_note']}")
        heat_gap = r.get("heat_gap")
        if heat_gap is not None and heat_gap >= 3:
            print(f"   🌡️  Residual gap: -{heat_gap} vs official VDOT (after Ely 30°C correction)")
            print(f"       Training pace < race effort → gap expected; not a fitness concern")
        elif heat_gap is not None and heat_gap < 0:
            print(f"   🔥 Estimate {abs(heat_gap)} above official VDOT — fitness may be improving")
    if r['peak_vdot_4w']:
        print(f"   Peak (4w GPS):           {r['peak_vdot_4w']}")
    if r['trend']:
        print(f"   Trend: {r['trend']}")

    gps_sessions = r.get("all_sessions", [])
    tm_sessions  = r.get("tm_sessions", [])

    if gps_sessions:
        print(f"\n🏃 GPS Quality Sessions ({len(gps_sessions)}):")
        print(f"   ℹ️  Training pace < max effort → estimates below official VDOT is normal")
        for s in gps_sessions:
            stype = s.get("session_type", "")[:3]
            adj_note = f" → {s['avg_pace_adj']} [heat-adj]" if s.get("heat_corrected") and s.get("avg_pace_adj") else ""
            print(f"   {s['date']} | {s['distance_km']}km @ {s['avg_pace']}{adj_note} | VDOT {s['vdot']} | {stype}")

    if tm_sessions:
        print(f"\n🏃‍♂️ TM Sessions — belt_speed / HR-inferred pace (excluded from estimate) ({len(tm_sessions)}):")
        for s in tm_sessions:
            stype = s.get("session_type", "")[:3]
            print(f"   {s['date']} | {s['distance_km']}km @ {s['avg_pace']}~ | VDOT ~{s['vdot']} | {stype}")

    if r['breakthroughs']:
        print(f"\n⚡ Breakthrough Sessions ({r['breakthrough_count']}):")
        for b in r['breakthroughs']:
            print(f"   {b['date']} | {b['distance_km']}km | {b['avg_pace']} | VDOT {b['vdot']}")
    elif gps_sessions:
        print(f"\n💡 ยังไม่มี breakthrough session (GPS VDOT estimate ต้องเกิน {CURRENT_VDOT + BREAKTHROUGH_THRESHOLD:.1f})")

    print(f"\n{'='*57}\n")


def main():
    parser = argparse.ArgumentParser(description="VDOT Estimator + Breakthrough Detector")
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--json",  action="store_true")
    args = parser.parse_args()

    result = analyze(args.weeks)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
