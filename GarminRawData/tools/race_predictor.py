#!/usr/bin/env python3
"""
race_predictor.py — Race Time Predictor จาก Fitness ปัจจุบัน

ประเมิน effective VDOT จาก quality sessions ล่าสุด แล้ว predict เวลา HM/FM
พร้อมบอก gap ถึง Goal A/B และคำแนะนำว่าต้องทำอะไรต่อ

Logic:
  1. อ่าน QualitySessionLog/sessions.json
  2. ประเมิน VDOT จาก pace และ HR ของแต่ละ session
     - T-session: VDOT จาก effective VO2 ที่ HR relative to T-zone
     - I-session: VDOT จาก I-pace โดยตรง (เปรียบเทียบกับ 5km equivalent)
  3. Weighted average (recent sessions หนักกว่า)
  4. คำนวณ predicted race times และ gap to goal

Usage:
    python3 race_predictor.py               # ประเมินฟิตปัจจุบัน + predict
    python3 race_predictor.py --goal-hm 110 # Sub 1:50 HM (110 นาที)
    python3 race_predictor.py --goal-fm 240 # Sub 4:00 FM (240 นาที)
"""

import sys
import json
import math
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta

BASE_DIR     = Path(__file__).parent.parent
LOG_PATH     = BASE_DIR / "QualitySessionLog" / "sessions.json"
MASTER_PATH  = BASE_DIR / "QualitySessionLog" / "sessions_master.json"
sys.path.append(str(BASE_DIR.parent / "skills" / "garmin_coach_mcp"))
sys.path.insert(0, str(Path(__file__).parent))
from config import ATHLETE, VDOT_PACES, HR_ZONE_BOUNDS  # noqa: E402
from vdot_math import compute_vdot, predict_race_time    # noqa: E402

RHR = ATHLETE["rhr"]
MHR = ATHLETE["mhr"]
HRR = ATHLETE["hrr"]
T_HR_LO, T_HR_HI = HR_ZONE_BOUNDS["Z3_T"]   # 174–183 (LTHR 183-anchored)
I_HR_LO, I_HR_HI = HR_ZONE_BOUNDS["Z4_I"]   # 176–187

# Goal definitions — loaded from races.json via race_registry (single source of truth)
try:
    from race_registry import list_races as _lr_pred
    _races_pred = dict(_lr_pred(active_only=False))
    _fm_key, _fm_race = next(
        ((k, r) for k, r in _races_pred.items() if r.get("dist_km", 0) >= 40 and r.get("active")),
        (None, {}))
    _hm_key, _hm_race = next(
        ((k, r) for k, r in _races_pred.items() if 15 <= r.get("dist_km", 0) < 40 and r.get("active")),
        (None, {}))
    HM_GOALS = {
        "A": {"label": _hm_race.get("goal_label", "Sub 1:50"),
              "minutes": float(_hm_race.get("goal_min", 110.0))},
        "B": {"label": "Sub 1:55", "minutes": 115.0},
    }
    FM_GOALS = {
        "A": {"label": _fm_race.get("goal_label", "Sub 4:00"),
              "minutes": float(_fm_race.get("goal_min", 240.0))},
        "B": {"label": "Sub 4:15", "minutes": 255.0},
    }
    _FM_RACE_NAME = _fm_race.get("short", _fm_race.get("name", "FM Race"))
    _FM_RACE_DATE = _fm_race.get("date", "2026")[:7]
except Exception:
    HM_GOALS = {
        "A": {"label": "Sub 1:50", "minutes": 110.0},
        "B": {"label": "Sub 1:55", "minutes": 115.0},
    }
    FM_GOALS = {
        "A": {"label": "Sub 4:00", "minutes": 240.0},
        "B": {"label": "Sub 4:15", "minutes": 255.0},
    }
    _FM_RACE_NAME = "FM Race"
    _FM_RACE_DATE = "2026"


# ---------------------------------------------------------------------------
# Jack Daniels VDOT formulas — imported from vdot_math (single source of truth)
# ---------------------------------------------------------------------------
# compute_vdot and predict_race_time imported above from vdot_math


def fmt_time(minutes: float) -> str:
    """Format decimal minutes to H:MM:SS."""
    total_sec = int(round(minutes * 60))
    h  = total_sec // 3600
    m  = (total_sec % 3600) // 60
    s  = total_sec % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# VDOT estimation from session log
# ---------------------------------------------------------------------------
def estimate_vdot_from_session(s: dict, quality_hr_avg: float | None = None) -> float | None:
    """
    Estimate effective VDOT from a logged quality session.

    Method:
    - T-session: compare quality lap HR (quality_hr_avg from sessions_master)
      against T-zone midpoint. Using full session avg_hr is wrong because it
      includes warm-up / cool-down laps at lower HR, inflating the estimate.
      Falls back to s["avg_hr"] only if quality_hr_avg not available.
    - I-session: compute VDOT from I-pace treating it as ~5km race effort
      (I-pace ≈ 5km race pace adjusted by +15 sec/km for rest intervals).
    - M/E sessions: not used for VDOT estimate.
    """
    stype    = s.get("session_type", "")
    pace_str = s.get("pace", "")

    # Parse pace "M:SS/km" → sec/km
    pace_sec = None
    if pace_str and ":" in pace_str:
        parts = pace_str.replace("/km", "").split(":")
        try:
            pace_sec = int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            return None

    if pace_sec is None:
        return None

    if "Threshold" in stype or stype == "Threshold (T)":
        # Use quality lap HR (not full-session avg which includes WU/CD)
        hr = quality_hr_avg or s.get("avg_hr") or 0
        if hr == 0:
            return None
        # HR must be within T zone to estimate VDOT
        # If HR < T zone lower bound → effort below threshold → cannot estimate
        if hr < T_HR_LO:
            return None
        t_mid      = (T_HR_LO + T_HR_HI) / 2   # ~173 bpm
        hr_deficit = t_mid - hr                  # >0 = running below T-zone mid
        # Each bpm below T-zone mid → ~0.5 VDOT uplift (conservative)
        base_vdot  = ATHLETE["vdot"]
        estimated  = base_vdot + max(0, hr_deficit * 0.5)
        return round(estimated, 1)

    elif "Interval" in stype or stype == "Interval (I)":
        # Treat 5×1km I-session as equivalent to a 5km near-maximal effort.
        # Race equivalent pace ≈ I-pace + 15 sec/km (rest intervals included)
        equiv_pace_sec = pace_sec + 15
        equiv_dist_m   = 5000
        equiv_time_min = (equiv_pace_sec / 60) * (equiv_dist_m / 1000)
        # Enforce 3000m min to match vdot_estimator.py threshold (audit fix)
        return compute_vdot(equiv_dist_m, equiv_time_min, min_dist_m=3000)

    return None


def load_quality_hr_index() -> dict:
    """Load quality_hr_avg per activity_id from sessions_master.json.
    Returns {activity_id: quality_hr_avg} for fast lookup.
    Falls back to empty dict if file missing.
    """
    if not MASTER_PATH.exists():
        return {}
    with open(MASTER_PATH) as f:
        master = json.load(f)
    return {
        s["activity_id"]: s["quality_hr_avg"]
        for s in master.get("sessions", [])
        if s.get("activity_id") and s.get("quality_hr_avg")
    }


def load_session_log():
    if not LOG_PATH.exists():
        return []
    with open(LOG_PATH) as f:
        data = json.load(f)
    return data.get("sessions", [])


def estimate_current_vdot(sessions: list, n_recent: int = 5) -> tuple:
    """
    Compute weighted average VDOT from n most recent quality sessions.
    Returns (estimated_vdot, list_of_used_sessions).
    """
    quality_hr_idx = load_quality_hr_index()   # {activity_id: quality_hr_avg}

    quality_types = {"Threshold (T)", "Interval (I)", "Repetition (R)"}
    quality_sessions = [s for s in sessions if s.get("session_type") in quality_types]
    # Sort by date descending
    quality_sessions.sort(key=lambda s: s["date"], reverse=True)
    recent = quality_sessions[:n_recent]

    estimates = []
    missing_hr_warn = []
    for s in recent:
        aid = s.get("activity_id")
        q_hr = quality_hr_idx.get(aid) if aid else None
        # Warn if quality_hr_avg missing → fallback to full-session avg_hr (includes WU/CD)
        if q_hr is None and s.get("avg_hr"):
            missing_hr_warn.append(f"  ⚠️  {s['date']} [{aid}]: quality_hr_avg ไม่อยู่ใน sessions_master "
                                   f"→ ใช้ avg_hr={s['avg_hr']} แทน (รวม WU/CD — อาจ overestimate)")
        v = estimate_vdot_from_session(s, quality_hr_avg=q_hr)
        if v:
            estimates.append((s["date"], s["session_type"], v, q_hr))

    if missing_hr_warn:
        print("\n⚠️  sessions_master dependency warning:")
        for w in missing_hr_warn:
            print(w)

    if not estimates:
        return None, []

    # Weighted average: most recent = highest weight
    weights = [2 ** i for i in range(len(estimates) - 1, -1, -1)]
    total_w = sum(weights)
    weighted_vdot = sum(v * w for (_, _, v, _), w in zip(estimates, weights)) / total_w

    return round(weighted_vdot, 1), estimates


# ---------------------------------------------------------------------------
# Main output
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal-hm", type=float, default=None, help="HM goal in minutes (e.g. 110 for Sub 1:50)")
    parser.add_argument("--goal-fm", type=float, default=None, help="FM goal in minutes (e.g. 240 for Sub 4:00)")
    args = parser.parse_args()

    sessions = load_session_log()

    print(f"\n{'='*60}")
    print(f"🔮 RACE PREDICTOR — {date.today()}")
    print(f"{'='*60}")

    if not sessions:
        print("⚠️  ไม่พบ session log — รัน:")
        print("   python3 post_session_analyzer.py --latest --update-log")
        print(f"{'='*60}\n")
        return

    est_vdot, used = estimate_current_vdot(sessions)

    if not est_vdot:
        print("⚠️  ไม่มี quality session ในlog ที่ประเมิน VDOT ได้")
        print(f"{'='*60}\n")
        return

    baseline_vdot = ATHLETE["vdot"]

    _cal_date = ATHLETE.get("vdot_calibration_date", "TT")
    print(f"\n  ✅ VDOT ยืนยัน (race-confirmed, {_cal_date}) : {baseline_vdot}")
    print(f"  🔬 VDOT ประมาณการ (training estimate only)  : {est_vdot}")
    print()
    print(f"  ⚠️  VDOT ประมาณการ ≠ VDOT จริง")
    print(f"      ใช้ได้เฉพาะ 'เทียบทิศทางการพัฒนา' เท่านั้น")
    print(f"      VDOT จริงต้องยืนยันจากผลแข่งตามหลัก Jack Daniels เสมอ")

    if est_vdot > baseline_vdot:
        delta = round(est_vdot - baseline_vdot, 1)
        print(f"\n  📈 Training signal: Fitness น่าจะพัฒนาขึ้นประมาณ {delta} points")
        print(f"      (ยืนยันได้เฉพาะหลังแข่งครั้งถัดไป)")
    else:
        print(f"\n  🟡 Training signal: Fitness ใกล้เคียง baseline")

    print(f"\n  📋 Sessions ที่ใช้ประเมิน ({len(used)} sessions):")
    for d, stype, v, q_hr in used:
        hr_note = f" | quality HR: {q_hr}" if q_hr else ""
        print(f"     {d}  {stype:<20}  training estimate: ~{v}{hr_note} (ไม่ใช่ VDOT จริง)")

    # Predicted race times
    print(f"\n{'─'*60}")
    print(f"  🏁 Predicted Race Times (based on training estimate — ไม่ใช่ VDOT จริง)")
    print(f"{'─'*60}")

    hm_pred = predict_race_time(est_vdot, 21097.5)
    fm_pred = predict_race_time(est_vdot, 42195.0)

    print(f"  Half Marathon (21.1 km) : {fmt_time(hm_pred)}")
    print(f"  Full Marathon (42.2 km) : {fmt_time(fm_pred)}")

    # HM goal analysis
    print(f"\n  🎯 HM Goal Analysis:")
    hm_goals = {k: v for k, v in HM_GOALS.items()}
    if args.goal_hm:
        hm_goals["Custom"] = {"label": f"Custom {fmt_time(args.goal_hm)}", "minutes": args.goal_hm}
    for gkey, g in hm_goals.items():
        goal_min    = g["minutes"]
        gap_min     = hm_pred - goal_min
        gap_vdot    = ATHLETE["vdot"]  # approximate: 1 VDOT ≈ 2-3 min HM
        vdot_needed = compute_vdot(21097.5, goal_min)
        vdot_gap    = round((vdot_needed or 0) - est_vdot, 1)
        status      = "✅ ทำได้แล้วตามฟิตปัจจุบัน" if gap_min <= 0 else f"⚠️  ยังขาด {fmt_time(abs(gap_min))} | ต้องการ VDOT {vdot_needed:.1f} (gap +{vdot_gap})"
        print(f"     Goal {gkey} {g['label']}: {status}")

    # FM goal analysis
    print(f"\n  🏁 FM Goal Analysis ({_FM_RACE_NAME}, {_FM_RACE_DATE}):")
    fm_goals = {k: v for k, v in FM_GOALS.items()}
    if args.goal_fm:
        fm_goals["Custom"] = {"label": f"Custom {fmt_time(args.goal_fm)}", "minutes": args.goal_fm}
    for gkey, g in fm_goals.items():
        goal_min    = g["minutes"]
        gap_min     = fm_pred - goal_min
        vdot_needed = compute_vdot(42195.0, goal_min)
        vdot_gap    = round((vdot_needed or 0) - est_vdot, 1)
        status      = "✅ ทำได้แล้ว (แต่ต้องผ่าน race-specific training ก่อน)" if gap_min <= 0 else f"⚠️  ยังขาด {fmt_time(abs(gap_min))} | ต้องการ VDOT {vdot_needed:.1f} (gap +{vdot_gap})"
        print(f"     Goal {gkey} {g['label']}: {status}")

    print(f"\n  ─────────────────────────────────────────────────────────")
    print(f"  ⚠️  DISCLAIMER (Jack Daniels Protocol)")
    print(f"     ตัวเลขข้างต้นคือ 'training-based estimate' เท่านั้น")
    print(f"     ห้ามใช้อัพเดต VDOT ใน config.py หรือ skill file")
    print(f"     VDOT จะอัพเดตได้เมื่อมีผลแข่งจริงเท่านั้น")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
