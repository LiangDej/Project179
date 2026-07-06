"""
training_planner.py — Full season plan from VDOT + race dates + current phase
Works backwards from target race → builds periodized weekly blocks

Usage:
    python3 training_planner.py --race fuji
    python3 training_planner.py --race fuji --weeks 8
    python3 training_planner.py --race hm --date 2026-05-17
"""
import sys, os, argparse
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
_mcp = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "skills", "garmin_coach_mcp"))
if os.path.isdir(_mcp):
    sys.path.insert(0, _mcp)
try:
    from config import ATHLETE, VDOT_PACES, TRAINING_PHASES, PHASE_PRESCRIPTIONS, get_current_phase
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
        for k, r in load_races().items()
    }
    DEFAULT_RACE = active_race_key()
except Exception:
    RACE_TARGETS = {"atm": {"name": "ATM Bangkok Marathon (42km)", "dist_km": 42.2, "date": date(2026, 11, 29)}}
    DEFAULT_RACE = "atm"

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
    acts_path = os.path.join(os.path.dirname(__file__), "..", "running_activities_all.json")
    try:
        acts = _json.loads(open(acts_path).read())
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
    acts_path = os.path.join(os.path.dirname(__file__), "..", "running_activities_all.json")
    try:
        acts = _json.loads(open(acts_path).read())
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
    import json as _json
    athlete_path = os.path.join(os.path.dirname(__file__), "..", "athlete.json")
    try:
        aj = _json.loads(open(athlete_path, encoding="utf-8").read())
    except Exception:
        aj = {}
    rest_days = {d.lower() for d in aj.get("rest_days", ["friday"])}
    strength_day = aj.get("strength_day")
    strength_day = strength_day.lower() if strength_day else None
    non_running = len(rest_days) + (1 if strength_day else 0)
    training_days = aj.get("training_days_per_week", 7 - non_running)
    return training_days, rest_days, strength_day


def _build_session_schedule(q_max: int, long_km) -> tuple[str, str]:
    """Return (schedule_str, quality_day_tag) for one week.

    quality_day_tag is unused by callers today but kept for future use by
    session_prescriber to know which day is Q1 vs Q2 without re-deriving it.
    """
    training_days, rest_cfg, strength_day = _load_schedule_config()
    excluded = rest_cfg | ({strength_day} if strength_day else set())
    active = [d for d in _DAY_ORDER if d not in excluded]
    while len(active) > training_days and len(active) > 3:
        active.pop(len(active) // 2)  # trim a mid-week day first

    schedule = {d: "REST" for d in rest_cfg}
    if strength_day:
        schedule[strength_day] = "Str"
    for d in _DAY_ORDER:
        if d not in schedule and d not in active:
            schedule[d] = "REST"  # trimmed beyond training_days_per_week

    long_day = "sunday" if "sunday" in active else (active[-1] if active else _DAY_ORDER[-1])
    schedule[long_day] = f"Long {long_km}km"
    remaining = [d for d in active if d != long_day]

    quality_days = []
    if remaining:
        quality_days.append(remaining[0])
    if q_max >= 2 and len(remaining) >= 3:
        quality_days.append(remaining[len(remaining) // 2])
    for i, d in enumerate(quality_days):
        schedule[d] = f"Q{i + 1}"

    other = [d for d in remaining if d not in quality_days]
    if other:
        strides_day = other[-1]
        schedule[strides_day] = "E+strides"
        other = other[:-1]
    for d in other:
        schedule[d] = "E"

    order_str = " | ".join(f"{_DAY_ABBR[d]}:{schedule[d]}" for d in _DAY_ORDER)
    return order_str, ",".join(quality_days)


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

    # Dynamic volume targets: this athlete's own recent load + best-ever week,
    # not a fixed per-athlete table (see _historical_peak_km / _target_peak_km).
    actual_recent_km   = _current_weekly_km()
    historical_peak_km = _historical_peak_km()
    base_start_km      = max(BASE_FLOOR_KM, int(actual_recent_km / 5) * 5)
    target_peak_km     = _target_peak_km(actual_recent_km, historical_peak_km)
    phase_km           = _build_phase_km(base_start_km, target_peak_km)

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

        phase_name = get_phase_for_week(weeks_to_race, weeks_total)
        presc = PHASE_PRESCRIPTIONS.get(phase_name, PHASE_PRESCRIPTIONS["base"])

        # Volume — progressive with 10% rule, deload every 4th week
        if phase_name == "taper":
            km = _taper_km(weeks_to_race, target_peak_km)
        else:
            phase_cfg = phase_km[phase_name]
            if phase_name == "base":
                raw_km = base_start_km + min(w, 10) * phase_cfg["step"]
            else:
                raw_km = phase_cfg["min"] + min(w, 10) * phase_cfg["step"]
            raw_km = min(raw_km, phase_cfg["max"])
            # Deload every 4th week
            if (w + 1) % 4 == 0:
                raw_km = int(raw_km * DELOAD_FACTOR)
            km = raw_km
            peak_km = max(peak_km, km)

        # Quality sessions
        q_max = presc.get("quality_max", 1)
        q1 = presc["quality1"]
        q2 = presc.get("quality2", None)

        sched_str, _ = _build_session_schedule(q_max, presc.get("long_km", "?"))
        if q_max >= 2 and q2:
            qual_str = f"{q1['type']}+{q2['type']}"
        else:
            qual_str = q1['type']
        sessions = sched_str.replace("Q1", q1['type']).replace("Q2", q2['type'] if q2 else "")

        # Markers
        marker = ""
        if weeks_to_race == 0:
            marker = "🏁 RACE"
            km = 0
            sessions = f"🏁 {race['name']} — {race_date}"
        elif (w + 1) % 4 == 0 and phase_name not in ("taper",):
            marker = "🔄 deload"
        elif km == peak_km and phase_name not in ("taper",):
            marker = "⭐ peak"

        phase_label = phase_name.replace("_", " ").title()
        print(f"{w+1:<4} {str(week_start):<12} {phase_label:<14} {km:>5}  {qual_str:>8}  {marker}")

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
        q1 = presc["quality1"]
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

def get_week_info(monday: date = None) -> dict:
    """
    Return structured context for one training week.
    Called by session_prescriber so the 7-day plan is aware of the 30-week plan.

    Returns:
        week_num      : 1-indexed week in the season (1–30)
        total_weeks   : total weeks in plan
        km_target     : target weekly km (already deloaded if deload week)
        is_deload     : True if this is a 4th-week deload
        phase         : "base" | "quality" | "race_specific" | "taper"
        days_to_race  : calendar days until Fuji
        race_name     : "Fuji FM (42km)"
        race_date     : "2026-12-13"
        easy_km       : recommended easy run distance (km) to hit weekly target
    """
    if monday is None:
        today = date.today()
        monday = today - timedelta(days=today.weekday())

    race      = RACE_TARGETS[DEFAULT_RACE]   # A-race จาก races.json (active_race)
    race_date = race["date"]
    # Derive plan_start from config.TRAINING_PHASES — first base-phase Monday
    # (skips taper/recovery phases at the very start)
    plan_start = next(
        (p["start"] for p in TRAINING_PHASES if p["phase"] == "base"),
        date(2026, 5, 18),  # fallback if no base phase defined
    )

    week_idx      = max(0, (monday - plan_start).days // 7)   # 0-indexed
    weeks_to_race = max(0, (race_date - monday).days // 7)
    total_weeks   = max(1, round((race_date - plan_start).days / 7))
    days_to_race  = (race_date - monday).days

    phase_name = get_phase_for_week(weeks_to_race, total_weeks)

    # Dynamic volume targets (mirrors build_plan logic exactly)
    actual_recent_km   = _current_weekly_km()
    historical_peak_km = _historical_peak_km()
    base_start_km      = max(BASE_FLOOR_KM, int(actual_recent_km / 5) * 5)
    target_peak_km     = _target_peak_km(actual_recent_km, historical_peak_km)
    phase_km           = _build_phase_km(base_start_km, target_peak_km)

    # Weekly km target (mirrors build_plan logic exactly)
    if phase_name == "taper":
        km_target = _taper_km(weeks_to_race, target_peak_km)
        is_deload = False
    else:
        phase_cfg = phase_km[phase_name]
        if phase_name == "base":
            raw_km = base_start_km + min(week_idx, 10) * phase_cfg["step"]
        else:
            raw_km = phase_cfg["min"] + min(week_idx, 10) * phase_cfg["step"]
        raw_km    = min(raw_km, phase_cfg["max"])
        is_deload = (week_idx + 1) % 4 == 0
        km_target = int(raw_km * DELOAD_FACTOR) if is_deload else raw_km

    # Recommended easy run km — distribute remaining km after quality + long
    presc    = PHASE_PRESCRIPTIONS.get(phase_name, {})
    q_max    = presc.get("quality_max", 1)
    q_km_est = q_max * 13          # ~13km per quality session (WU+quality+CD)
    long_km  = presc.get("long_km", 20)
    easy_days = 3 if q_max == 1 else 2   # Thu is quality2 in quality+ phases
    remaining  = max(0, km_target - long_km - q_km_est)
    easy_km    = max(6, min(14, round(remaining / max(1, easy_days) / 2) * 2))

    return {
        "week_num":     week_idx + 1,
        "total_weeks":  total_weeks,
        "km_target":    km_target,
        "is_deload":    is_deload,
        "phase":        phase_name,
        "days_to_race": days_to_race,
        "race_name":    race["name"],
        "race_date":    str(race_date),
        "easy_km":      easy_km,
    }


def build_chart(race_key: str, target_date: date, num_weeks: int):
    """
    ASCII bar chart of the full season — km target per week, coloured by phase.
    Shows current week with ► marker and actual km if available.
    """
    import json as _json

    race = RACE_TARGETS[race_key]
    today = date.today()
    start = today - timedelta(days=today.weekday())

    # Load actual weekly km from running_activities_all.json
    acts_path = os.path.join(os.path.dirname(__file__), "..", "running_activities_all.json")
    actual_by_week: dict[str, float] = {}
    try:
        acts = _json.loads(open(acts_path).read())
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
    }

    BAR_SCALE = 1.2   # chars per km
    MAX_KM    = 80    # max for scale

    # Dynamic volume targets (mirrors build_plan logic exactly)
    actual_recent_km   = _current_weekly_km()
    historical_peak_km = _historical_peak_km()
    base_start_km      = max(BASE_FLOOR_KM, int(actual_recent_km / 5) * 5)
    target_peak_km     = _target_peak_km(actual_recent_km, historical_peak_km)
    phase_km           = _build_phase_km(base_start_km, target_peak_km)

    print("=" * 72)
    print(f"📊 SEASON CHART — {race['name']} ({target_date})")
    print(f"   · base  ▪ quality  █ race_specific  ░ taper  ► current week")
    print("=" * 72)
    print(f"  {'Wk':<4} {'Date':<12} {'Phase':<14} {'km':>4}  Chart (actual/plan)")
    print("  " + "─" * 66)

    for w in range(num_weeks):
        week_start    = start + timedelta(weeks=w)
        week_end      = week_start + timedelta(days=6)
        weeks_to_race = max(0, (target_date - week_start).days // 7)

        phase_name = get_phase_for_week(weeks_to_race, num_weeks)
        char       = PHASE_COLORS.get(phase_name, "·")

        # Plan km (mirrors build_plan logic exactly — same helpers, no separate copy)
        if phase_name == "taper":
            km = _taper_km(weeks_to_race, target_peak_km)
        else:
            phase_cfg = phase_km[phase_name]
            if phase_name == "base":
                base_km = base_start_km + min(w, 10) * phase_cfg["step"]
            else:
                base_km = phase_cfg["min"] + min(w, 10) * phase_cfg["step"]
            base_km = min(base_km, phase_cfg["max"])
            if (w + 1) % 4 == 0:
                base_km = int(base_km * DELOAD_FACTOR)
            km = base_km

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
