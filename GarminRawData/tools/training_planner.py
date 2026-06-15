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
# Phase weekly km targets (progression)
# ---------------------------------------------------------------------------
# Calibrated for VDOT 40 (~sub-4:00 marathoner, ~180 km/month base toward ATM)
# Daniels' Running Formula: peak weekly ≈ 1.5× current weekly base
# Pfitzinger's Advanced Marathoning: 55–70 km/wk for sub-4 FM build
PHASE_KM = {
    "base":          {"min": 35, "max": 48, "step": 3},   # peak 48 km/wk
    "quality":       {"min": 45, "max": 58, "step": 3},   # peak 58 km/wk
    "race_specific": {"min": 52, "max": 62, "step": 2},   # peak 62 km/wk
    "taper":         {"min": 20, "max": 40, "step": -10},
}

# ---------------------------------------------------------------------------
# Determine phase name from week offset to race
# ---------------------------------------------------------------------------
def get_phase_for_week(weeks_to_race: int, total_weeks: int) -> str:
    pct = (total_weeks - weeks_to_race) / total_weeks
    if weeks_to_race <= 2:
        return "taper"
    elif weeks_to_race <= 4:
        return "taper"
    elif weeks_to_race <= 8:
        return "race_specific"
    elif pct < 0.5:
        return "base"
    else:
        return "quality"


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

        phase_name = get_phase_for_week(weeks_to_race, weeks_total)
        presc = PHASE_PRESCRIPTIONS.get(phase_name, PHASE_PRESCRIPTIONS["base"])

        # Volume — progressive with 10% rule, deload every 4th week
        phase_cfg = PHASE_KM[phase_name]
        if phase_name == "taper":
            if weeks_to_race <= 1:
                km = 20
            elif weeks_to_race <= 2:
                km = 30
            else:
                km = 40
        else:
            base_km = phase_cfg["min"] + min(w, 10) * phase_cfg["step"]
            base_km = min(base_km, phase_cfg["max"])
            # Deload every 4th week
            if (w + 1) % 4 == 0:
                base_km = int(base_km * 0.75)
            km = base_km
            peak_km = max(peak_km, km)

        # Quality sessions
        q_max = presc.get("quality_max", 1)
        q1 = presc["quality1"]
        q2 = presc.get("quality2", None)

        if q_max >= 2 and q2:
            qual_str = f"{q1['type']}+{q2['type']}"
            sessions = f"Mon:Str | Tue:{q1['type']} | Wed:E | Thu:{q2['type']} | Fri:REST | Sat:E+strides | Sun:Long {presc.get('long_km','?')}km"
        else:
            qual_str = q1['type']
            sessions = f"Mon:Str | Tue:{q1['type']} | Wed:E | Thu:E | Fri:REST | Sat:E+strides | Sun:Long {presc.get('long_km','?')}km"

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
    phase_cfg  = PHASE_KM[phase_name]

    # Weekly km target (mirrors build_plan logic exactly)
    if phase_name == "taper":
        if weeks_to_race <= 1:
            km_target = 20
        elif weeks_to_race <= 2:
            km_target = 30
        else:
            km_target = 40
        is_deload = False
    else:
        raw_km    = phase_cfg["min"] + min(week_idx, 10) * phase_cfg["step"]
        raw_km    = min(raw_km, phase_cfg["max"])
        is_deload = (week_idx + 1) % 4 == 0
        km_target = int(raw_km * 0.75) if is_deload else raw_km

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
        phase_cfg  = PHASE_KM[phase_name]
        char       = PHASE_COLORS.get(phase_name, "·")

        # Plan km (mirrors build_plan logic)
        if phase_name == "taper":
            if weeks_to_race <= 1:
                km = 0
            elif weeks_to_race <= 2:
                km = 20
            elif weeks_to_race <= 3:
                km = 30
            else:
                km = 40
        else:
            base_km = phase_cfg["min"] + min(w, 10) * phase_cfg["step"]
            base_km = min(base_km, phase_cfg["max"])
            if (w + 1) % 4 == 0:
                base_km = int(base_km * 0.75)
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
