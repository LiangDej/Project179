"""
training_planner.py — Full season plan from VDOT + race dates + current phase
Works backwards from target race → builds periodized weekly blocks

Usage:
    python3 training_planner.py --race bangsaen
    python3 training_planner.py --race bangsaen --weeks 8
    python3 training_planner.py --race sponsor21 --date 2026-05-17
"""

from __future__ import annotations
import sys, os, argparse
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
_mcp = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "skills", "garmin_coach_mcp"))
if os.path.isdir(_mcp):
    sys.path.insert(0, _mcp)
try:
    from config import (ATHLETE, VDOT_PACES, HR_ZONE_BOUNDS, TRAINING_PHASES,
                        PHASE_PRESCRIPTIONS, get_current_phase, render_prescription)
    from jd_rules import long_run_cap
except ImportError:
    print("❌ config.py not found — run from GarminRawData/tools/")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Race targets
# ---------------------------------------------------------------------------
# SINGLE SOURCE: races.json via race_registry (no hardcoded dates/Fuji).
try:
    from race_registry import load_races, active_race_key
    RACE_TARGETS = {
        k: {"name": f"{r['name']} ({r['dist_km']:.0f}km)",
            "dist_km": r["dist_km"],
            "date": date.fromisoformat(r["date"])}
        for k, r in load_races().items() if r.get("active", True)
    }
    DEFAULT_RACE = active_race_key()
except Exception:
    print("❌ race_registry unavailable — check GarminRawData/races.json exists and is valid")
    print("   Fix: cp GarminRawData/races.example.json GarminRawData/races.json")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Phase weekly km targets (progression) — DYNAMIC, derived per-athlete at runtime.
# ---------------------------------------------------------------------------
# Base/quality/race_specific "max" are computed from the athlete's own training
# history (see _historical_peak_km / _target_peak_km), not a fixed table — a
# runner with a 40km/wk history gets a different plan than one with 62km/wk.
# Ratios below (base:quality:race_specific relative to peak) preserve the
# progressive-overload shape that MASTER_PLAN_2026 validated for this athlete
# (62:65:70 ≈ 0.886:0.929:1.0), applied proportionally to whatever peak the
# CURRENT athlete's history supports.
PHASE_PEAK_RATIO = {"base": 0.886, "quality": 0.929, "race_specific": 1.0}
BASE_FLOOR_KM    = 20   # generic sanity floor if activity history is empty/corrupt
STRETCH_FACTOR   = 1.13  # peak plan = historical peak + 13% realistic stretch
DELOAD_FACTOR    = 0.80  # deload week = 80% of that week's peak
WU_CD_KM         = 4     # warm-up + cool-down around a quality session
EASY_MIN_KM, EASY_MAX_KM = 6, 14

# Generic taper science (Mujika & Padilla): volume drops progressively as % of
# peak, floor at ~15% shakeout the week before race, 0 on race week itself.
TAPER_PCT = {4: 0.75, 3: 0.55, 2: 0.35, 1: 0.15, 0: 0.0}

# ---------------------------------------------------------------------------
# Determine phase name from week offset to race
# Boundaries revised 2026-07-01 (MASTER_PLAN_2026):
#   Taper 5wk | Race Specific 3wk | Quality 6wk | Base = rest
# ---------------------------------------------------------------------------
def get_phase_for_week(weeks_to_race: int, total_weeks: int) -> str:
    if weeks_to_race < 5:
        return "taper"
    elif weeks_to_race < 8:
        return "race_specific"
    elif weeks_to_race < 14:
        return "quality"
    else:
        return "base"


# ---------------------------------------------------------------------------
# Dynamic starting km — actual recent weekly km (last 4 complete weeks)
# ---------------------------------------------------------------------------
def _current_weekly_km() -> float:
    import json as _json
    from collections import defaultdict
    from activity_loader import load_activities_merged
    try:
        acts = load_activities_merged()
        weekly: dict = defaultdict(float)
        today = date.today()
        cutoff = today - timedelta(weeks=6)
        for a in acts:
            ts = (a.get("startTimeLocal") or "")[:10]
            if not ts:
                continue
            d = date.fromisoformat(ts)
            if d < cutoff:
                continue
            ws = str(d - timedelta(days=d.weekday()))
            weekly[ws] += (a.get("distance") or 0) / 1000
        current_week = str(today - timedelta(days=today.weekday()))
        complete = sorted([(k, v) for k, v in weekly.items() if k < current_week])
        recent = [v for _, v in complete[-4:]]
        return round(sum(recent) / len(recent)) if recent else float(BASE_FLOOR_KM)
    except Exception:
        return float(BASE_FLOOR_KM)


def _historical_peak_km(lookback_weeks: int = 52) -> float:
    """Highest single completed week (km) in the last `lookback_weeks` weeks.

    This is the athlete's own proof of capacity — used instead of a fixed
    per-athlete number so a new runner and a 60km/wk veteran each get a peak
    target calibrated to what THEY have actually demonstrated they can handle.
    """
    import json as _json
    from collections import defaultdict
    from activity_loader import load_activities_merged
    try:
        acts = load_activities_merged()
        weekly: dict = defaultdict(float)
        today = date.today()
        cutoff = today - timedelta(weeks=lookback_weeks)
        current_week = str(today - timedelta(days=today.weekday()))
        for a in acts:
            ts = (a.get("startTimeLocal") or "")[:10]
            if not ts:
                continue
            d = date.fromisoformat(ts)
            if d < cutoff:
                continue
            ws = str(d - timedelta(days=d.weekday()))
            if ws >= current_week:
                continue  # exclude in-progress week
            weekly[ws] += (a.get("distance") or 0) / 1000
        return max(weekly.values()) if weekly else 0.0
    except Exception:
        return 0.0


def _target_peak_km(current_weekly_km: float, historical_peak_km: float) -> int:
    """Dynamic peak-week volume target, rounded to nearest 5km.

    Grows from whichever is higher — current training load or the athlete's
    best-ever demonstrated week — by STRETCH_FACTOR. Never set below current
    load + one progression step, so a runner mid-buildup isn't handed a lower
    target than where they already are.
    """
    baseline = max(historical_peak_km, current_weekly_km, BASE_FLOOR_KM)
    stretched = baseline * STRETCH_FACTOR
    floor = current_weekly_km + 5
    return max(int(round(stretched / 5) * 5), int(round(floor / 5) * 5))


def _build_phase_km(base_start_km: float, target_peak_km: int) -> dict:
    """Base/quality/race_specific min-max-step, scaled off target_peak_km."""
    base_max = max(int(round(target_peak_km * PHASE_PEAK_RATIO["base"] / 5) * 5), int(base_start_km) + 5)
    quality_max = max(int(round(target_peak_km * PHASE_PEAK_RATIO["quality"] / 5) * 5), base_max + 3)
    race_max = max(target_peak_km, quality_max + 2)
    return {
        "base":          {"min": int(base_start_km), "max": base_max,    "step": 3},
        "quality":       {"min": base_max,            "max": quality_max, "step": 3},
        "race_specific": {"min": quality_max,         "max": race_max,    "step": 2},
    }


def _taper_km(weeks_to_race: int, peak_km: int) -> int:
    """Taper-week volume as a % of peak_km (generic taper curve, not a fixed table)."""
    pct = TAPER_PCT.get(weeks_to_race, TAPER_PCT[4])
    return int(round(peak_km * pct))


# ---------------------------------------------------------------------------
# Weekly session schedule — driven by athlete.json training_days_per_week /
# rest_days, so a 4-day/week runner and a 6-day/week runner each get a
# schedule that fits the days they actually have, instead of a fixed
# Mon-Str/Tue-Q1/Wed-E/Thu-Q2/Fri-REST/Sat-E+strides/Sun-Long template.
# ---------------------------------------------------------------------------
_DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_ABBR  = {"monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
              "friday": "Fri", "saturday": "Sat", "sunday": "Sun"}


def _load_schedule_config() -> tuple[int, set, str | None]:
    """Read training_days_per_week / rest_days / strength_day from athlete.json.

    Reads the raw json (not the curated ATHLETE dict from config.py) so this
    stays a two-file change (athlete.json + this file) without also touching
    the protected config.py whitelist.

    training_days_per_week counts RUNNING days only — strength_day is separate
    (a training day, but not a rest day and not a running day).
    """
    # config already loaded athlete.json (or athlete.example.json on a fresh clone)
    from config import _AJ as aj
    rest_days = {d.lower() for d in aj.get("rest_days", ["friday"])}
    strength_day = aj.get("strength_day")
    strength_day = strength_day.lower() if strength_day else None
    non_running = len(rest_days) + (1 if strength_day else 0)
    training_days = aj.get("training_days_per_week", 7 - non_running)
    return training_days, rest_days, strength_day


def weekly_structure(q_max: int) -> dict:
    """{weekday 0=Mon..6: day_type} from athlete.json schedule — the ONE weekly
    template (session_prescriber + daily_brief used to hardcode their own).
    day_type ∈ strength | quality1 | quality2 | easy | easy+strides | long | rest.
    """
    training_days, rest_cfg, strength_day = _load_schedule_config()
    excluded = rest_cfg | ({strength_day} if strength_day else set())
    active = [d for d in _DAY_ORDER if d not in excluded]
    while len(active) > training_days and len(active) > 3:
        active.pop(len(active) // 2)  # trim a mid-week day first

    schedule = {d: "rest" for d in _DAY_ORDER}
    if strength_day:
        schedule[strength_day] = "strength"
    long_day = "sunday" if "sunday" in active else (active[-1] if active else _DAY_ORDER[-1])
    schedule[long_day] = "long"
    remaining = [d for d in active if d != long_day]
    quality_days = []
    if remaining and q_max >= 1:   # recovery phase: quality_max 0 → no quality day
        quality_days.append(remaining[0])
    if q_max >= 2 and len(remaining) >= 3:
        quality_days.append(remaining[len(remaining) // 2])
    for i, d in enumerate(quality_days):
        schedule[d] = f"quality{i + 1}"
    other = [d for d in remaining if d not in quality_days]
    if other:
        schedule[other[-1]] = "easy+strides"
        for d in other[:-1]:
            schedule[d] = "easy"
    return {i: schedule[d] for i, d in enumerate(_DAY_ORDER)}


_SHORT = {"strength": "Str", "rest": "REST", "quality1": "Q1", "quality2": "Q2",
          "easy": "E", "easy+strides": "E+strides"}


def _build_session_schedule(q_max: int, long_km) -> tuple[str, str]:
    """(schedule_str, quality_days) for the season table — from weekly_structure."""
    ws = weekly_structure(q_max)
    parts, qd = [], []
    for i, d in enumerate(_DAY_ORDER):
        t = ws[i]
        parts.append(f"{_DAY_ABBR[d]}:{f'Long {long_km}km' if t == 'long' else _SHORT[t]}")
        if t.startswith("quality"):
            qd.append(d)
    return " | ".join(parts), ",".join(qd)


# ---------------------------------------------------------------------------
# Format pace
# ---------------------------------------------------------------------------
def fmt_pace(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}/km"


# ---------------------------------------------------------------------------
# Build weekly plan
# ---------------------------------------------------------------------------
def build_plan(race_key: str, target_date: date, num_weeks: int):
    race = RACE_TARGETS[race_key]
    race_date = target_date or race["date"]
    today = date.today()
    weeks_total = num_weeks

    # Start from Monday of current week
    start = today - timedelta(days=today.weekday())

    vdot = ATHLETE["vdot"]
    e_pace  = VDOT_PACES["E"]
    m_pace  = VDOT_PACES["M"]
    t_pace  = VDOT_PACES["T"]
    i_pace  = VDOT_PACES["I"]

    print("=" * 70)
    print(f"📅 TRAINING PLANNER — {race['name']}")
    print(f"   VDOT {vdot} | Race: {race_date} | {weeks_total} สัปดาห์")
    print(f"   Generated: {today}")
    print("=" * 70)
    print()

    # Header
    print(f"{'#':<4} {'สัปดาห์':<12} {'Phase':<14} {'km':>5} {'Quality':>8}  Sessions")
    print("-" * 70)

    peak_km = 0
    for w in range(weeks_total):
        week_start = start + timedelta(weeks=w)
        week_end   = week_start + timedelta(days=6)
        weeks_to_race = max(0, (race_date - week_start).days // 7)

        wp = week_plan(week_start, race_key, race_date)
        phase_name, presc, km = wp["phase"], wp["prescription"], wp["km_target"]
        if phase_name not in ("taper", "recovery"):
            peak_km = max(peak_km, km)
        q_max = presc.get("quality_max", 1)
        q1 = presc.get("quality1") or {"type": "-"}
        q2 = presc.get("quality2")
        sched_str, _ = _build_session_schedule(q_max, wp["long_km"])
        qual_str = f"{q1['type']}+{q2['type']}" if (q_max >= 2 and q2) else q1["type"]
        sessions = sched_str.replace("Q1", q1["type"]).replace("Q2", q2["type"] if q2 else "")

        # Markers
        marker = ""
        if week_start <= race_date <= week_end:
            marker = "🏁 RACE"
            km = 0
            sessions = f"🏁 {race['name']} — {race_date}"
        elif wp["is_deload"]:
            marker = "🔄 deload"
        elif km == peak_km and phase_name not in ("taper", "recovery"):
            marker = "⭐ peak"

        phase_label = phase_name.replace("_", " ").title()
        print(f"{w+1:<4} {str(week_start):<12} {phase_label:<14} {km:>5}  {qual_str:>8}  "
              f"L{wp['long_km']:<3} {marker}{'  ✂️ JD' if wp['long_capped'] else ''}")
        print(f"     {sessions}")

    print()
    print(f"⭐ Peak week: {peak_km} km")
    print()

    # Paces reference
    print("📐 VDOT {} Pace Reference:".format(vdot))
    print(f"   E (Easy)      : {fmt_pace(e_pace[0])}–{fmt_pace(e_pace[1])}")
    print(f"   M (Marathon)  : {fmt_pace(m_pace[0])}–{fmt_pace(m_pace[1])}")
    print(f"   T (Threshold) : {fmt_pace(t_pace[0])}–{fmt_pace(t_pace[1])}")
    print(f"   I (Interval)  : {fmt_pace(i_pace[0])}–{fmt_pace(i_pace[1])}")
    print()

    # Phase legend
    print("📋 Phase Sessions:")
    for phase_key, presc in PHASE_PRESCRIPTIONS.items():
        q1 = presc.get("quality1") or {"workout": "— (easy only)"}
        q2 = presc.get("quality2")
        long_note = presc.get("long_note", "")
        print(f"   {phase_key.replace('_',' ').title():<16} Q1: {q1['workout'][:45]}")
        if q2 and presc.get("quality_max", 1) >= 2:
            print(f"   {'':16} Q2: {q2['workout'][:45]}")
        print(f"   {'':16} Long: {long_note[:50]}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Public API — used by session_prescriber to link short + long term plans
# ---------------------------------------------------------------------------

def progressive_long_km(phase_info: dict, monday: date) -> int:
    """
    Linear interpolation: long_km_start → long_km (peak) across the phase.
    Deload weeks (every 4th week within a phase) get ~75% of that week's value.
    Taper phase counts DOWN (start=high → peak=low) to reflect volume reduction.
    Returns nearest even integer (clean distances: 16, 18, 20…).
    """
    if not phase_info:
        return PHASE_PRESCRIPTIONS.get("quality", {}).get("long_km", 20)

    prescription = PHASE_PRESCRIPTIONS.get(phase_info["phase"], {})
    long_start   = prescription.get("long_km_start")
    long_peak    = prescription.get("long_km", 16)

    # No progression defined → use fixed peak
    if long_start is None or long_start == long_peak:
        return long_peak

    phase_start  = phase_info["start"]
    phase_end    = phase_info["end"]
    total_weeks  = max(1, round((phase_end - phase_start).days / 7))
    week_num     = max(0, (monday - phase_start).days // 7)  # 0-indexed

    # Linear interpolation t: 0.0 (week 1) → 1.0 (last week) — clamped to [0,1]
    t = min(1.0, week_num / max(1, total_weeks - 1))
    raw = long_start + (long_peak - long_start) * t

    # Deload every 4th week within the phase (week_num 3, 7, 11…) → 75%
    # Skip deload logic for taper phase — volume reduction is already built-in
    is_deload = (week_num + 1) % 4 == 0 and phase_info["phase"] != "taper"
    if is_deload:
        raw *= 0.75

    # Round to nearest 2km for clean prescription
    return max(8, round(raw / 2) * 2)


def _phase_week_km(phase_info: dict, monday: date) -> tuple[int, bool]:
    """(km, is_deload) from training_plan.json weekly_km [first, last] — linear
    across the phase, every 4th week of a non-taper phase = DELOAD_FACTOR."""
    lo, hi = phase_info["weekly_km"]
    total = max(1, round((phase_info["end"] - phase_info["start"]).days / 7))
    wk = max(0, (monday - phase_info["start"]).days // 7)
    raw = lo + (hi - lo) * min(1.0, wk / max(1, total - 1))
    deload = (wk + 1) % 4 == 0 and phase_info["phase"] not in ("taper", "recovery")
    return int(round(raw * (DELOAD_FACTOR if deload else 1.0))), deload


def week_plan(monday: date = None, race_key: str | None = None,
              race_date: date | None = None) -> dict:
    """ONE computation of a training week (phase, volume, long run, quality,
    easy km). get_week_info (brief / prescriber) and build_plan (season
    table) both call this — they used to each "mirror" the logic by copy.

    Volume: training_plan.json phase weekly_km when present (the agreed plan),
    else the history-based dynamic fallback.
    """
    if monday is None:
        today = date.today()
        monday = today - timedelta(days=today.weekday())

    race      = RACE_TARGETS[race_key or DEFAULT_RACE]
    race_date = race_date or race["date"]
    _season_floor = race_date - timedelta(weeks=52)
    plan_start = next(
        (p["start"] for p in TRAINING_PHASES
         if p["phase"] == "base" and _season_floor < p["start"] <= race_date),
        race_date - timedelta(weeks=26),  # no base phase in training_plan.json
    )
    week_idx      = max(0, (monday - plan_start).days // 7)
    weeks_to_race = max(0, (race_date - monday).days // 7)
    total_weeks   = max(1, round((race_date - plan_start).days / 7))
    days_to_race  = (race_date - monday).days

    # Phase: training_plan.json (by date) is the source; week-offset rule only
    # for dates the plan doesn't cover.
    phase_info = get_current_phase(monday)
    phase_name = phase_info["phase"] if phase_info else get_phase_for_week(weeks_to_race, total_weeks)

    if phase_info and phase_info.get("weekly_km"):
        km_target, is_deload = _phase_week_km(phase_info, monday)
        vol_src = "plan"
    else:
        actual_recent_km   = _current_weekly_km()
        historical_peak_km = _historical_peak_km()
        base_start_km      = max(BASE_FLOOR_KM, int(actual_recent_km / 5) * 5)
        target_peak_km     = _target_peak_km(actual_recent_km, historical_peak_km)
        phase_km           = _build_phase_km(base_start_km, target_peak_km)
        if phase_name == "taper":
            km_target, is_deload = _taper_km(weeks_to_race, target_peak_km), False
        else:
            cfg = phase_km[phase_name]
            raw = (base_start_km if phase_name == "base" else cfg["min"]) + min(week_idx, 10) * cfg["step"]
            raw = min(raw, cfg["max"])
            is_deload = (week_idx + 1) % 4 == 0
            km_target = int(raw * DELOAD_FACTOR) if is_deload else raw
        vol_src = "dynamic"

    # Long run: phase progression, then the JD cap (≤25–30 % of the week and
    # ≤150 min) outside taper; inside taper the taper profile caps it.
    presc     = render_prescription(phase_name, weekly_km=km_target)
    q_max     = presc.get("quality_max", 1)
    long_plan = progressive_long_km(phase_info, monday) if phase_info else presc.get("long_km", 16)
    long_km, long_capped = long_plan, None
    long_rule = (phase_info or {}).get("long_rule", "general")
    if phase_name not in ("taper", "recovery"):
        cap, why = long_run_cap(km_target, VDOT_PACES["E"], long_rule)
        if cap and long_plan > cap:
            long_km = max(EASY_MIN_KM, int(cap))
            long_capped = f"Long {long_plan}→{long_km} km ({why})"

    ws = weekly_structure(q_max)
    q_km = sum((presc.get(f"quality{i}") or {}).get("quality_km", 0) + WU_CD_KM
               for i in (1, 2) if f"quality{i}" in ws.values())
    easy_days = sum(1 for v in ws.values() if v in ("easy", "easy+strides"))
    remaining = max(0, km_target - long_km - q_km)
    easy_km   = max(EASY_MIN_KM, min(EASY_MAX_KM, round(remaining / max(1, easy_days) / 2) * 2))

    return {
        "week_num":     week_idx + 1,
        "total_weeks":  total_weeks,
        "km_target":    km_target,
        "volume_source": vol_src,
        "is_deload":    is_deload,
        "phase":        phase_name,
        "days_to_race": days_to_race,
        "weeks_to_race": weeks_to_race,
        "race_name":    race["name"],
        "race_date":    str(race_date),
        "easy_km":      easy_km,
        "long_km":      long_km,
        "long_capped":  long_capped,
        "phase_info":   phase_info,
        "prescription": presc,
        "structure":    ws,
    }


def get_week_info(monday: date = None) -> dict:
    """Week context for the active A-race (races.json) — see week_plan()."""
    return week_plan(monday)


def build_chart(race_key: str, target_date: date, num_weeks: int):
    """
    ASCII bar chart of the full season — km target per week, coloured by phase.
    Shows current week with ► marker and actual km if available.
    """
    import json as _json

    race = RACE_TARGETS[race_key]
    today = date.today()
    start = today - timedelta(days=today.weekday())

    # Actual weekly km — shared activity loader
    from activity_loader import load_activities_merged
    actual_by_week: dict[str, float] = {}
    try:
        acts = load_activities_merged()
        for a in acts:
            dist_km = (a.get("distance") or 0) / 1000
            ts = a.get("startTimeLocal", "")[:10]
            if ts:
                d = date.fromisoformat(ts)
                ws = str(d - timedelta(days=d.weekday()))
                actual_by_week[ws] = actual_by_week.get(ws, 0) + dist_km
    except Exception:
        pass

    PHASE_COLORS = {
        "base": "·",
        "quality": "▪",
        "race_specific": "█",
        "taper": "░",
        "recovery": "-",
    }

    BAR_SCALE = 1.2   # chars per km
    MAX_KM    = 80    # max for scale

    print("=" * 72)
    print(f"📊 SEASON CHART — {race['name']} ({target_date})")
    print(f"   · base  ▪ quality  █ race_specific  ░ taper  ► current week")
    print("=" * 72)
    print(f"  {'Wk':<4} {'Date':<12} {'Phase':<14} {'km':>4}  Chart (actual/plan)")
    print("  " + "─" * 66)

    for w in range(num_weeks):
        week_start    = start + timedelta(weeks=w)
        week_end      = week_start + timedelta(days=6)
        # v1.8.1: same week computation as build_plan / the brief (week_plan);
        # the chart used to re-run the pre-v1.8 dynamic logic.
        wp         = week_plan(week_start, race_key, target_date)
        phase_name = wp["phase"]
        char       = PHASE_COLORS.get(phase_name, "·")
        km         = wp["km_target"]

        is_current = (week_start <= today <= week_end)
        is_past    = week_end < today
        marker     = "►" if is_current else " "

        # Actual bar
        ws_str  = str(week_start)
        actual  = actual_by_week.get(ws_str)

        bar_len = int(min(km, MAX_KM) * BAR_SCALE)
        bar     = char * bar_len

        if actual is not None and is_past:
            act_bar_len = int(min(actual, MAX_KM) * BAR_SCALE)
            # Show actual as separate indicator: filled vs hollow
            bar = ("█" * act_bar_len).ljust(bar_len, "░") if act_bar_len <= bar_len else "█" * bar_len + "+"
            actual_str = f" ({actual:.0f}km actual)"
        elif actual is not None and is_current:
            actual_str = f" ({actual:.0f}km so far)"
        else:
            actual_str = ""

        phase_label = phase_name.replace("_", " ").title()
        print(f" {marker}{w+1:<3} {str(week_start):<12} {phase_label:<14} {km:>3}  {bar}{actual_str}")

    print("  " + "─" * 66)
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--race",  default=DEFAULT_RACE, choices=list(RACE_TARGETS.keys()))
    parser.add_argument("--weeks", type=int, default=None)
    parser.add_argument("--date",  default=None, help="Override race date YYYY-MM-DD")
    parser.add_argument("--chart", action="store_true", help="ASCII season bar chart")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else RACE_TARGETS[args.race]["date"]
    today = date.today()
    weeks_to_race = max(4, (target_date - today).days // 7)
    num_weeks = args.weeks or min(weeks_to_race + 1, 32)

    if args.chart:
        build_chart(args.race, target_date, num_weeks)
    else:
        build_plan(args.race, target_date, num_weeks)


if __name__ == "__main__":
    main()
