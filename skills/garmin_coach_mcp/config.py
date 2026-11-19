"""
config.py — Single source of truth for athlete constants and training phases.

All tools (server.py, daily_brief.py, post_session_analyzer.py, weekly_load_report.py,
training_load.py, race_predictor.py) import from here.
Edit DATA, not this file: athlete.json (athlete), races.json (races),
training_plan.json (season phases + workout specs). This module only derives.
"""
from datetime import date, timedelta
import json as _json
from pathlib import Path as _Path

# ---------------------------------------------------------------------------
# SINGLE SOURCE OF TRUTH for athlete-mutable values: GarminRawData/athlete.json
# After a TT/race, edit THAT json (or run post_race_updater.py) — NOT this file.
# config.py derives HR zones + paces + ZONE_PCT from it. The literals below are
# FALLBACKS, used only if athlete.json is missing/corrupt.
# ---------------------------------------------------------------------------
_ATHLETE_JSON = _Path(__file__).resolve().parent.parent.parent / "GarminRawData" / "athlete.json"


def _load_athlete_json() -> dict:
    """athlete.json, else athlete.example.json (fresh clone) — never literals here."""
    for f in (_ATHLETE_JSON, _ATHLETE_JSON.with_name("athlete.example.json")):
        try:
            return _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


_AJ = _load_athlete_json()

ATHLETE = {
    "name":       _AJ.get("name", "Athlete"),
    "rhr":        _AJ["rhr"],
    "mhr":        _AJ["mhr"],
    "vdot":       _AJ["vdot"],              # race/TT confirmed ONLY (not Garmin VO2max)
    "weight_kg":  _AJ.get("weight_kg"),
    "vdot_source":            _AJ.get("vdot_source"),
    "vdot_calibration_date":  _AJ.get("vdot_calibration_date"),
    "vdot_calibration":       _AJ.get("vdot_calibration"),
    "vdot_heat_adj_estimate": _AJ.get("vdot_heat_adj_estimate"),
    "tt_scheduled":           _AJ.get("tt_scheduled"),
    "lthr":                   _AJ["lthr"],
    "lthr_date":              _AJ.get("lthr_date"),
    "lthr_confidence":        _AJ.get("lthr_confidence"),
    "pain_status":            _AJ.get("pain_status", "none"),  # none|mild|moderate — persisted injury state
    # Personal marathon-endurance factor: actual FM / VDOT-predicted FM.
    # 1.0 = no adjustment. Recalibrate after every marathon.
    "fm_endurance_factor":    float(_AJ.get("fm_endurance_factor", 1.0)),
    # Standing athlete preferences the coach must not violate (printed in daily brief)
    "coaching_constraints":   list(_AJ.get("coaching_constraints", [])),
    # Measured treadmill speed↔HR points (LT2 test) — treadmill_pace_model
    "lt2_test":               _AJ.get("lt2_test", {}),
}
ATHLETE["hrr"] = ATHLETE["mhr"] - ATHLETE["rhr"]   # derived

# ---------------------------------------------------------------------------
# Shared pure helpers live in GarminRawData/tools (vdot_math = JD formulas,
# jd_rules = JD limits + workout rendering) — imported, never duplicated here.
# ---------------------------------------------------------------------------
import sys as _sys
_TOOLS_DIR = _Path(__file__).resolve().parent.parent.parent / "GarminRawData" / "tools"
if str(_TOOLS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_TOOLS_DIR))
from vdot_math import jd_paces as _jd_paces          # noqa: E402
from jd_rules import (JD_E_MAX_PCT_HRMAX,            # noqa: E402
                      render_quality as _render_quality, render_long as _render_long)

# ---------------------------------------------------------------------------
# HR Zones (v1.8)
#   E ceiling = JD 79 % HRmax (was %HRR 0.79 → 163 = Friel, not JD → 154)
#   M / I     = %HRR from athlete.json hr_zone_hrr_pct
#   T ceiling = LTHR (measured), by definition
# ---------------------------------------------------------------------------
_ZP_DEFAULT = {"M": 0.862, "I": 0.95}


def _build_zones(rhr: int, mhr: int, lthr: float, zp: dict) -> dict:
    hrr = mhr - rhr
    e_hi = int(round(mhr * JD_E_MAX_PCT_HRMAX))
    m_hi = rhr + int(hrr * zp.get("M", _ZP_DEFAULT["M"]))
    t_hi = int(lthr)
    i_hi = rhr + int(hrr * zp.get("I", _ZP_DEFAULT["I"]))
    return {"Z1_E": (rhr, e_hi), "Z2_M": (e_hi, m_hi), "Z3_T": (m_hi, t_hi),
            "Z4_I": (t_hi, i_hi), "Z5_R": (i_hi, mhr)}


def _zone_list(b: dict) -> list:
    names = [("Z1 Easy", "Z1_E"), ("Z2 Marathon", "Z2_M"), ("Z3 Threshold", "Z3_T"),
             ("Z4 Interval", "Z4_I"), ("Z5 Repetition", "Z5_R")]
    return [(n, *b[k]) for n, k in names]


def _zone_pct(b: dict, rhr: int, hrr: int) -> dict:
    """%HRR upper bound per zone (classification thresholds)."""
    return {k: round((b[z][1] - rhr) / hrr * 100) for k, z in
            (("E", "Z1_E"), ("M", "Z2_M"), ("T", "Z3_T"), ("I", "Z4_I"))}


HR_ZONE_BOUNDS = _build_zones(ATHLETE["rhr"], ATHLETE["mhr"], ATHLETE["lthr"],
                              _AJ.get("hr_zone_hrr_pct", {}))
HR_ZONES = _zone_list(HR_ZONE_BOUNDS)
ZONE_PCT = _zone_pct(HR_ZONE_BOUNDS, ATHLETE["rhr"], ATHLETE["hrr"])

# ---------------------------------------------------------------------------
# VDOT Training Paces (sec/km, fast, slow) — DERIVED from VDOT (vdot_math).
# athlete.json → pace_overrides_sec {zone: [fast, slow]} only if you must.
# ---------------------------------------------------------------------------
VDOT_PACES = _jd_paces(ATHLETE["vdot"])
VDOT_PACES.update({k: tuple(v) for k, v in _AJ.get("pace_overrides_sec", {}).items()
                   if not k.startswith("_")})

# ---------------------------------------------------------------------------
# Nutrition — product numbers + per-session protocol, all from athlete.json.
# No product fallbacks: a missing value is None and the tool asks for it
# (the old 650/90 fallbacks were the pre-2026-08-02 WRONG label reading).
# ---------------------------------------------------------------------------
_NP = _AJ.get("nutrition_products", {})
NUTRITION = {
    "prevo_na_mg":      _NP.get("prevo_na_mg"),
    "aminovital_na_mg": _NP.get("aminovital_na_mg"),
    "aminovital_carb_g": _NP.get("aminovital_carb_g"),
    "sweat_na_mmol":    _AJ.get("sweat_na_mmol", 45.0),   # Shirreffs & Maughan mid-range
    "electrolyte_name": _NP.get("electrolyte_name", "Electrolyte cap"),
    "gel_name":         _NP.get("gel_name", "Gel"),
    "in_race_schedule": _AJ.get("race_day_nutrition", {}).get("in_race_schedule"),
}


def _day_nutrition() -> dict:
    q = _AJ.get("quality_nutrition", {})
    lo = _AJ.get("long_run_nutrition", {}).get("outdoor", {})
    rd = _AJ.get("race_day_nutrition", {})
    sn = _AJ.get("session_nutrition", {})
    pre = sn.get("pre_base", "")

    def _fuel(d):
        if not d:
            return pre or "-"
        return f"{pre + ' + ' if pre else ''}{d.get('pre_60min', '')} (T-60) → {d.get('pre_15min', '')} (T-15)"
    out = {
        "quality1": _fuel(q), "quality2": _fuel(q), "long": _fuel(lo),
        "easy": sn.get("easy", pre or "-"), "easy+strides": sn.get("easy", pre or "-"),
        "strength": sn.get("strength", "-"), "rest": sn.get("rest", "-"),
        "post_run": sn.get("post_run", "-"), "carb_load": sn.get("carb_load", "-"),
        "race": (f"{rd.get('pre_150min', '')} (T-2.5hr) → {rd.get('pre_30min', '')} (T-30min) → "
                 f"{rd.get('in_race', '')}") if rd else "-",
    }
    return out


DAY_NUTRITION = _day_nutrition()

# ---------------------------------------------------------------------------
# Form/quality grading baselines — personal values in athlete.json →
# form_baselines; the numbers here are generic defaults only.
# ---------------------------------------------------------------------------
BASELINES = {
    "cadence_min":       164,
    "grade_cadence_s":   155,   # cadence floor for an S grade
    "grade_cadence_a":   153,   # cadence floor for an A grade
    "gct_max":           275,
    "gct_treadmill_add":  20,
    "decoupling_good":     5,   # Friel Pa:HR — <5 % = aerobically fit
    "decoupling_warn":    10,
    "stamina_drain_max":  50,
    "vr_good":           8.5,
    "vr_warn":           9.5,
    "power_min":         None,
    "power_max":         None,
}
BASELINES.update({k: v for k, v in _AJ.get("form_baselines", {}).items() if not k.startswith("_")})

# ---------------------------------------------------------------------------
# Season plan — GarminRawData/training_plan.json (SINGLE SOURCE, editable data)
# Falls back to training_plan.example.json for a fresh clone.
# Workouts are SPECS: reps/pace/HR are rendered from VDOT_PACES + zones and
# capped by JD per-session limits when the planner knows the weekly volume.
# ---------------------------------------------------------------------------
_GRD = _Path(__file__).resolve().parent.parent.parent / "GarminRawData"


def _load_plan() -> dict:
    for f in (_GRD / "training_plan.json", _GRD / "training_plan.example.json"):
        try:
            return _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {"phases": [], "prescriptions": {}}


def _phase(p: dict) -> dict:
    d = dict(p)
    d["start"] = date.fromisoformat(str(p["start"]))
    d["end"] = date.fromisoformat(str(p["end"]))
    return d


def render_prescription(phase_key: str, weekly_km=None, presc_map=None) -> dict:
    """Phase prescription with workouts rendered (and JD-capped if weekly_km)."""
    src = (presc_map or _PLAN.get("prescriptions", {})).get(phase_key) or {}
    if "quality1" in src and isinstance(src["quality1"], dict) and "workout" in src["quality1"] \
            and "type" in src["quality1"] and "reps" not in src["quality1"] and "km" not in src["quality1"] \
            and "parts" not in src["quality1"]:
        return src   # legacy text prescription (DB profile) — pass through
    out = {"focus": src.get("focus", ""), "quality_max": src.get("quality_max", 1)}
    for slot in ("quality1", "quality2"):
        spec = src.get(slot)
        if spec:
            r = _render_quality(spec, weekly_km, VDOT_PACES, HR_ZONE_BOUNDS)
            r["spec"] = spec
            out[slot] = r
    lg = src.get("long", {})
    out["long_spec"] = lg
    out["long_km_start"] = lg.get("start_km")
    out["long_km"] = lg.get("peak_km", 16)
    out["long_note"] = _render_long(lg, out["long_km"], VDOT_PACES, HR_ZONE_BOUNDS)
    return out


_PLAN = _load_plan()
TRAINING_PHASES = [_phase(p) for p in _PLAN.get("phases", [])]
PHASE_PRESCRIPTIONS = {k: render_prescription(k) for k in _PLAN.get("prescriptions", {})}


def get_current_phase(target_date=None):
    """Return the current training phase dict, or None if between phases."""
    d = target_date or date.today()
    for phase in TRAINING_PHASES:
        if phase["start"] <= d <= phase["end"]:
            return phase
    return None


def load_athlete_context(anon_user_id: str = None, line_user_id: str = None) -> bool:
    """
    Dynamically loads an athlete's profile from Firestore/local DB,
    and updates the config globals in-place to maintain 100% backward compatibility.
    """
    import os
    import sys
    from pathlib import Path
    
    # Ensure current directory of config.py is in sys.path for safety
    config_dir = str(Path(__file__).resolve().parent)
    if config_dir not in sys.path:
        sys.path.insert(0, config_dir)
        
    
    # Resolve anon_user_id from LINE ID if needed
    if not anon_user_id and line_user_id:
        try:
            from security_helper import hash_line_id
            anon_user_id = hash_line_id(line_user_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to hash LINE ID: {e}")
            return False
        
    if not anon_user_id:
        return False
        
    try:
        from db_helper import get_athlete_profile
        profile = get_athlete_profile(anon_user_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to fetch athlete profile from DB: {e}")
        return False
        
    if not profile:
        import logging
        logging.getLogger(__name__).warning(f"No athlete profile found for ID: {anon_user_id}")
        return False
        
    # Update ATHLETE in-place
    athlete_updates = {
        "name": profile.get("name", ATHLETE["name"]),
        "rhr": int(profile.get("rhr", ATHLETE["rhr"])),
        "mhr": int(profile.get("mhr", ATHLETE["mhr"])),
        "weight_kg": float(profile.get("weight_kg", ATHLETE["weight_kg"])),
        "vdot": float(profile.get("vdot", ATHLETE["vdot"])),
        "vdot_source": profile.get("vdot_source", ATHLETE["vdot_source"]),
        "vdot_calibration_date": profile.get("vdot_calibration_date", ATHLETE["vdot_calibration_date"]),
        "vdot_calibration": profile.get("vdot_calibration", ATHLETE["vdot_calibration"]),
        "vdot_heat_adj_estimate": profile.get("vdot_heat_adj_estimate", ATHLETE["vdot_heat_adj_estimate"]),
        "tt_scheduled": profile.get("tt_scheduled", ATHLETE["tt_scheduled"]),
        "lthr": float(profile.get("lthr", ATHLETE["lthr"])),
    }
    athlete_updates["hrr"] = athlete_updates["mhr"] - athlete_updates["rhr"]
    ATHLETE.update(athlete_updates)

    # Recalculate zones + paces with the same helpers as the static path
    _b = _build_zones(ATHLETE["rhr"], ATHLETE["mhr"], ATHLETE["lthr"],
                      profile.get("hr_zone_hrr_pct", {}))
    HR_ZONE_BOUNDS.clear(); HR_ZONE_BOUNDS.update(_b)
    HR_ZONES.clear(); HR_ZONES.extend(_zone_list(_b))
    ZONE_PCT.clear(); ZONE_PCT.update(_zone_pct(_b, ATHLETE["rhr"], ATHLETE["hrr"]))
    VDOT_PACES.clear(); VDOT_PACES.update(_jd_paces(ATHLETE["vdot"]))

    # Load custom baselines if present
    if "baselines" in profile:
        BASELINES.update(profile["baselines"])
        
    # Load custom training phases if present
    if "training_phases" in profile:
        TRAINING_PHASES.clear()
        TRAINING_PHASES.extend(_phase(p) if isinstance(p.get("start"), str) else p
                               for p in profile["training_phases"])
        
    # Load custom phase prescriptions if present
    if "phase_prescriptions" in profile:
        _pp = profile["phase_prescriptions"]
        PHASE_PRESCRIPTIONS.update({k: render_prescription(k, presc_map=_pp) for k in _pp})
        
    return True


# Auto-load athlete context if environment variable is present
import os
_anon_id = os.getenv("ANON_USER_ID") or os.getenv("ATHLETE_ID")
_line_id = os.getenv("LINE_USER_ID")
if _anon_id or _line_id:
    try:
        load_athlete_context(anon_user_id=_anon_id, line_user_id=_line_id)
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(f"Auto-loading athlete context failed: {_e}")

