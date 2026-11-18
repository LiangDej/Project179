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
    python3 race_predictor.py --json        # JSON for AI agents (also lists breakthrough sessions;
                                            #   replaces vdot_estimator.py, merged v1.7)
"""

from __future__ import annotations

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
    # Only UPCOMING active races, nearest first — a finished race (e.g. last
    # month's HM) must not keep showing as the goal (2026-11-17 review fix).
    _today_iso = date.today().isoformat()
    _upcoming = sorted(
        ((k, r) for k, r in _races_pred.items()
         if r.get("active") and str(r.get("date", "")) >= _today_iso),
        key=lambda kr: str(kr[1].get("date", "")))
    _fm_key, _fm_race = next(
        ((k, r) for k, r in _upcoming if r.get("dist_km", 0) >= 40), (None, {}))
    _hm_key, _hm_race = next(
        ((k, r) for k, r in _upcoming if 15 <= r.get("dist_km", 0) < 40), (None, {}))
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
    _FM_RACE_NAME = _fm_race.get("short", _fm_race.get("name", "no upcoming FM"))
    _FM_RACE_DATE = _fm_race.get("date", "—")[:7]
    _HM_RACE_NAME = _hm_race.get("short", _hm_race.get("name", "no upcoming HM"))
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
    _FM_RACE_DATE = "—"
    _HM_RACE_NAME = "HM Race"


# ---------------------------------------------------------------------------
# Jack Daniels VDOT formulas — imported from vdot_math (single source of truth)
# ---------------------------------------------------------------------------
# compute_vdot and predict_race_time imported above from vdot_math


def fm_goal_requirements(goal_min: float, vdot: float, factor: float = 1.0) -> dict:
    """What a marathon goal really demands, given the athlete's endurance factor.

    Why (2026-11-18 fix): the old goal line computed `compute_vdot(42195, goal)`
    — the VDOT whose *pure* FM prediction hits the goal — while the gap shown
    next to it came from the *personal* (× factor) prediction. With factor 1.17
    that printed "need VDOT 37.9 (gap +-3.2)" for sub-4 at VDOT 41: a goal that
    looked already reached while the same line said 22 min short.
    The VDOT that actually delivers the goal is the one whose pure prediction
    equals goal / factor. Also returns the endurance factor that would deliver
    the goal at the current VDOT (the other lever: long runs / fuelling / heat).
    """
    factor = factor or 1.0
    vdot_needed = compute_vdot(42195.0, goal_min / factor)
    raw_pred = predict_race_time(vdot, 42195.0)
    return {
        "vdot_needed": round(vdot_needed, 1) if vdot_needed else None,
        "vdot_gap": round((vdot_needed or 0) - vdot, 1),
        "factor_needed_at_current_vdot": round(goal_min / raw_pred, 3) if raw_pred else None,
    }


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
        if hr_deficit >= 0:
            estimated = base_vdot + hr_deficit * 0.5
        else:
            # HR ABOVE T-mid: only a fitness-down signal if the pace was not
            # faster than T pace (running faster than T explains higher HR).
            t_fast = min(VDOT_PACES["T"]) if VDOT_PACES.get("T") else 0
            estimated = base_vdot + (hr_deficit * 0.5 if pace_sec >= t_fast else 0)
        # Cap: HR-only inference is weak — never claim more than ±3 VDOT from it
        estimated = max(base_vdot - 3, min(base_vdot + 3, estimated))
        return round(estimated, 1)

    elif "Interval" in stype or stype == "Interval (I)":
        # Treat 5×1km I-session as equivalent to a 5km near-maximal effort.
        # Race equivalent pace ≈ I-pace + 15 sec/km (rest intervals included)
        # Prefer the REP pace if logged: the session 'pace' field is the whole-
        # activity average (WU + jog recoveries + CD), which made a 6:21/km
        # I-session read as VDOT 27.5 (2026-09-10). Implausible results are
        # rejected later by estimate_current_vdot()'s plausibility band.
        rep = s.get("rep_pace") or s.get("quality_pace")
        if rep and ":" in str(rep):
            try:
                _p = str(rep).replace("/km", "").split(":")
                pace_sec = int(_p[0]) * 60 + int(_p[1])
            except ValueError:
                pass
        equiv_pace_sec = pace_sec + 15
        equiv_dist_m   = 5000
        equiv_time_min = (equiv_pace_sec / 60) * (equiv_dist_m / 1000)
        # Enforce 3000m min (same threshold as find_breakthroughs)
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


PLAUSIBLE_BAND = 5.0      # VDOT points from race-confirmed value
STALE_LOG_DAYS = 28       # warn if the newest quality session is older than this


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

    # Plausibility band: a single-session estimate more than PLAUSIBLE_BAND
    # VDOT away from the race-confirmed value is almost always a data artefact
    # (whole-session pace, wrong session type, HR dropout), not a real change
    # in fitness. Reject it visibly instead of letting a 4:2:1 weight drag the
    # average (2026-11-17: one bad I-session turned 40 → 33.3).
    base = ATHLETE["vdot"]
    rejected = [e for e in estimates if abs(e[2] - base) > PLAUSIBLE_BAND]
    estimates = [e for e in estimates if abs(e[2] - base) <= PLAUSIBLE_BAND]
    if rejected:
        print(f"\n⚠️  ตัด session ที่ค่าประมาณเพี้ยน (ห่าง VDOT ยืนยัน > ±{PLAUSIBLE_BAND}) — น่าจะเป็น data artefact:")
        for d, st, v, _ in rejected:
            print(f"     {d}  {st:<20} ~{v}  (เช่น pace ทั้ง session รวม WU/พัก)")

    if not estimates:
        return None, []

    # Weighted average: most recent = highest weight
    weights = [2 ** i for i in range(len(estimates) - 1, -1, -1)]
    total_w = sum(weights)
    weighted_vdot = sum(v * w for (_, _, v, _), w in zip(estimates, weights)) / total_w

    return round(weighted_vdot, 1), estimates


# ---------------------------------------------------------------------------
# Breakthrough detector (merged from vdot_estimator.py, v1.7)
# ---------------------------------------------------------------------------
# Why merged: vdot_estimator printed its OWN "VDOT estimate" (top-3 pace-only,
# heat-adjusted) next to this tool's HR-based estimate. Same question, two
# answers (2026-11-18: +1.1 here vs "−8.9 gap" there). Training pace is below
# race effort, so a pace-only average systematically under-reads — useless as
# an estimate. What WAS useful is the breakthrough flag: a quality session whose
# heat-adjusted quality-lap pace alone implies VDOT ≥ confirmed + 1. That is a
# one-way signal (it can only say "faster than expected"), so it survives here
# as a list of sessions — no second averaged number.
BREAKTHROUGH_THRESHOLD = 1.0
BREAKTHROUGH_WEEKS     = 8


def _pace_to_sec(p) -> float | None:
    p = str(p or "").replace("/km", "").strip()
    if ":" not in p:
        return None
    try:
        m, s = p.split(":")
        return int(m) * 60 + int(s)
    except ValueError:
        return None


def find_breakthroughs(sessions: list, weeks: int = BREAKTHROUGH_WEEKS) -> list:
    """Outdoor quality sessions whose heat-adjusted quality-lap pace implies
    VDOT ≥ confirmed + BREAKTHROUGH_THRESHOLD. Treadmill sessions are skipped
    (belt/HR-inferred pace is not a performance measurement)."""
    master = {}
    if MASTER_PATH.exists():
        with open(MASTER_PATH, encoding="utf-8") as f:
            for m in json.load(f).get("sessions", []):
                if m.get("activity_id"):
                    master[int(m["activity_id"])] = m
    try:
        from bangkok_climate import morning_temp_c, ely_penalty_fraction
    except Exception:
        morning_temp_c = ely_penalty_fraction = None

    cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
    base = ATHLETE["vdot"]
    out = []
    for s in sessions:
        if s.get("session_type") not in {"Threshold (T)", "Interval (I)", "Tempo", "Fast Finish"}:
            continue
        if s.get("date", "") < cutoff or s.get("is_treadmill"):
            continue
        m = master.get(int(s["activity_id"])) if s.get("activity_id") else None
        if not m:
            continue
        q_laps = [l for l in (m.get("laps") or []) if l.get("role") == "quality"]
        dist_km = sum(l.get("distance_km", 0) for l in q_laps)
        dur_s = sum(l.get("duration_s", 0) for l in q_laps)
        if dist_km < 3.0 or dur_s <= 0:
            continue
        pace = dur_s / dist_km
        month = int(s["date"][5:7])
        pen = ely_penalty_fraction(morning_temp_c(month)) if ely_penalty_fraction else 0.0
        adj = pace / (1 + pen)
        # Interval reps have recoveries between them — the summed rep pace is
        # not a continuous effort. Same +15 s/km race-equivalent as
        # estimate_vdot_from_session (a 4:16 5×1km read as "VDOT 46" without it).
        if "Interval" in s["session_type"]:
            adj += 15
        v = compute_vdot(dist_km * 1000, dist_km * adj / 60, min_dist_m=3000)
        if v and v > base + PLAUSIBLE_BAND:
            continue                       # artefact (wrong laps/type), not a breakthrough
        if v and v >= base + BREAKTHROUGH_THRESHOLD:
            out.append({"date": s["date"], "session_type": s["session_type"],
                        "quality_km": round(dist_km, 2), "pace": fmt_pace(pace),
                        "pace_equiv": fmt_pace(adj), "pace_vdot": round(v, 1)})
    return sorted(out, key=lambda x: x["date"])


def fmt_pace(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}/km"


# ---------------------------------------------------------------------------
# Main output
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal-hm", type=float, default=None, help="HM goal in minutes (e.g. 110 for Sub 1:50)")
    parser.add_argument("--goal-fm", type=float, default=None, help="FM goal in minutes (e.g. 240 for Sub 4:00)")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable summary on stdout (human report → stderr) — for AI agents")
    args = parser.parse_args()
    if args.json:
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):
            result = _run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _run(args)


def _run(args) -> dict:
    result = {"date": date.today().isoformat(), "vdot_confirmed": ATHLETE["vdot"],
              "vdot_confirmed_date": ATHLETE.get("vdot_calibration_date"),
              "training_estimate": None,
              "note": "training_estimate ≠ VDOT. VDOT changes only from a race/TT."}
    sessions = load_session_log()

    print(f"\n{'='*60}")
    print(f"🔮 RACE PREDICTOR — {date.today()}")
    print(f"{'='*60}")

    if not sessions:
        print("⚠️  ไม่พบ session log — รัน:")
        print("   python3 post_session_analyzer.py --latest --update-log")
        print(f"{'='*60}\n")
        result["error"] = "no session log"
        return result

    est_vdot, used = estimate_current_vdot(sessions)

    if not est_vdot:
        print("⚠️  ไม่มี quality session ในlog ที่ประเมิน VDOT ได้")
        print(f"{'='*60}\n")
        result["error"] = "no usable quality session"
        return result

    baseline_vdot = ATHLETE["vdot"]

    _cal_date = ATHLETE.get("vdot_calibration_date", "TT")
    print(f"\n  ✅ VDOT ยืนยัน (race-confirmed, {_cal_date}) : {baseline_vdot}")
    print(f"  🔬 VDOT ประมาณการ (training estimate only)  : {est_vdot}")
    print()
    print(f"  ⚠️  VDOT ประมาณการ ≠ VDOT จริง")
    print(f"      ใช้ได้เฉพาะ 'เทียบทิศทางการพัฒนา' เท่านั้น")
    print(f"      VDOT จริงต้องยืนยันจากผลแข่งตามหลัก Jack Daniels เสมอ")

    delta = round(est_vdot - baseline_vdot, 1)
    if delta >= 1.0:
        print(f"\n  📈 Training signal: Fitness น่าจะพัฒนาขึ้นประมาณ {delta} points")
        print(f"      (ยืนยันได้เฉพาะหลังแข่งครั้งถัดไป)")
    elif delta <= -1.0:
        print(f"\n  📉 Training signal: ต่ำกว่า baseline {abs(delta)} points (อาจล้า/ร้อน/HR เพี้ยน — ไม่ใช่ VDOT ลดจริง)")
    else:
        print(f"\n  🟡 Training signal: Fitness ใกล้เคียง baseline ({delta:+})")

    newest = max(d for d, *_ in used)
    age = (date.today() - date.fromisoformat(newest)).days
    if age > STALE_LOG_DAYS:
        print(f"\n  ⏳ STALE: quality session ล่าสุดที่ใช้ได้คือ {newest} ({age} วันก่อน)")
        print(f"      ตัวเลขนี้สะท้อนฟิตช่วงนั้น ไม่ใช่วันนี้ — อย่าใช้ตัดสินใจ")

    result.update({
        "training_estimate": est_vdot, "delta_vs_confirmed": delta,
        "newest_session": newest, "newest_session_age_days": age,
        "stale": age > STALE_LOG_DAYS,
        "sessions_used": [{"date": d, "type": t, "estimate": v, "quality_hr": q}
                          for d, t, v, q in used],
    })

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
    print(f"  Full Marathon (42.2 km) : {fmt_time(fm_pred)}  (VDOT ล้วน — สมมติ endurance เต็ม)")
    _fef = ATHLETE.get("fm_endurance_factor", 1.0) or 1.0
    result["predictions"] = {"hm": fmt_time(hm_pred), "fm_vdot_only": fmt_time(fm_pred),
                             "fm_endurance_factor": _fef}
    if _fef != 1.0:
        fm_pred = fm_pred * _fef
        print(f"  Full Marathon (personal) : {fmt_time(fm_pred)}  (× {_fef} endurance factor จาก FM จริง — ใช้ตัวนี้)")
    result["predictions"]["fm_personal"] = fmt_time(fm_pred)
    result["goals"] = []

    # HM goal analysis
    print(f"\n  🎯 HM Goal Analysis ({_HM_RACE_NAME}):")
    hm_goals = {k: v for k, v in HM_GOALS.items()}
    if args.goal_hm:
        hm_goals["Custom"] = {"label": f"Custom {fmt_time(args.goal_hm)}", "minutes": args.goal_hm}
    for gkey, g in hm_goals.items():
        goal_min    = g["minutes"]
        gap_min     = hm_pred - goal_min
        gap_vdot    = ATHLETE["vdot"]  # approximate: 1 VDOT ≈ 2-3 min HM
        vdot_needed = compute_vdot(21097.5, goal_min)
        vdot_gap    = round((vdot_needed or 0) - est_vdot, 1)
        status      = "✅ ทำได้แล้วตามฟิตปัจจุบัน" if gap_min <= 0 else f"⚠️  ยังขาด {fmt_time(abs(gap_min))} | ต้องการ VDOT {vdot_needed:.1f} (gap {vdot_gap:+})"
        print(f"     Goal {gkey} {g['label']}: {status}")
        result["goals"].append({"dist": "HM", "race": _HM_RACE_NAME, "key": gkey, "label": g["label"],
                                "goal": fmt_time(goal_min), "met": gap_min <= 0,
                                "short_by": None if gap_min <= 0 else fmt_time(gap_min),
                                "vdot_needed": round(vdot_needed, 1) if vdot_needed else None})

    # FM goal analysis
    if _FM_RACE_DATE == "—" and not args.goal_fm:
        print(f"\n  🏁 FM Goal Analysis: ไม่มี FM ที่กำลังจะมาถึงใน races.json (ใช้ --goal-fm ถ้าต้องการ)")
        FM_GOALS.clear()
    else:
        print(f"\n  🏁 FM Goal Analysis ({_FM_RACE_NAME}, {_FM_RACE_DATE}):")
    fm_goals = {k: v for k, v in FM_GOALS.items()}
    if args.goal_fm:
        fm_goals["Custom"] = {"label": f"Custom {fmt_time(args.goal_fm)}", "minutes": args.goal_fm}
    for gkey, g in fm_goals.items():
        goal_min    = g["minutes"]
        gap_min     = fm_pred - goal_min          # fm_pred is already × factor
        req         = fm_goal_requirements(goal_min, est_vdot, _fef)
        if gap_min <= 0:
            status = "✅ ทำได้แล้ว (แต่ต้องผ่าน race-specific training ก่อน)"
        else:
            status = (f"⚠️  ยังขาด {fmt_time(abs(gap_min))} | ต้องการ VDOT {req['vdot_needed']} "
                      f"(gap {req['vdot_gap']:+}) ที่ endurance factor {_fef}")
        print(f"     Goal {gkey} {g['label']}: {status}")
        if gap_min > 0 and _fef != 1.0 and req["factor_needed_at_current_vdot"]:
            print(f"        ↳ หรือ: VDOT เท่าเดิม แต่ลด endurance factor เหลือ ≤ "
                  f"{req['factor_needed_at_current_vdot']} (long run 30-32 km / fuelling / สนามเย็น)")
        result["goals"].append({"dist": "FM", "race": _FM_RACE_NAME, "key": gkey, "label": g["label"],
                                "goal": fmt_time(goal_min), "met": gap_min <= 0,
                                "short_by": None if gap_min <= 0 else fmt_time(gap_min),
                                "vdot_needed_at_current_factor": req["vdot_needed"],
                                "factor_needed_at_current_vdot": req["factor_needed_at_current_vdot"]})

    # Breakthrough sessions (pace-only, heat-adjusted) — one-way "faster than expected" flag
    bts = find_breakthroughs(sessions)
    result["breakthroughs"] = bts
    print(f"\n  ⚡ Breakthrough ({BREAKTHROUGH_WEEKS} สัปดาห์, outdoor quality, pace ปรับร้อน ≥ VDOT ยืนยัน +{BREAKTHROUGH_THRESHOLD}):")
    if bts:
        for b in bts:
            print(f"     {b['date']}  {b['session_type']:<14} {b['quality_km']}km @ {b['pace']}"
                  f" → {b['pace_equiv']} [ปรับร้อน+เทียบวิ่งต่อเนื่อง] | pace-VDOT {b['pace_vdot']}")
        print(f"     → สัญญาณว่าอาจพร้อม TT/แข่งเพื่อยืนยัน VDOT ใหม่ (ยังไม่ใช่ VDOT จริง)")
    else:
        print(f"     ไม่มี")

    print(f"\n  ─────────────────────────────────────────────────────────")
    print(f"  ⚠️  DISCLAIMER (Jack Daniels Protocol)")
    print(f"     ตัวเลขข้างต้นคือ 'training-based estimate' เท่านั้น")
    print(f"     ห้ามใช้อัพเดต VDOT ใน config.py หรือ skill file")
    print(f"     VDOT จะอัพเดตได้เมื่อมีผลแข่งจริงเท่านั้น")
    print(f"{'='*60}\n")
    return result


if __name__ == "__main__":
    main()
