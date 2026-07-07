"""
config.py — Single source of truth for athlete constants and training phases.

All tools (server.py, daily_brief.py, post_session_analyzer.py, weekly_load_report.py,
training_load.py, race_predictor.py) import from here.
To update after a race (new VDOT) or phase change, edit only this file.
"""
from datetime import date
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
    try:
        return _json.loads(_ATHLETE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


_AJ = _load_athlete_json()

ATHLETE = {
    "name":       _AJ.get("name", "Athlete"),
    "rhr":        _AJ.get("rhr", 44),
    "mhr":        _AJ.get("mhr", 195),
    "vdot":       _AJ.get("vdot", 40),     # race/TT confirmed ONLY (not Garmin VO2max)
    "weight_kg":  _AJ.get("weight_kg", 71.6),
    "vdot_source":            _AJ.get("vdot_source", "tt_outdoor_park_jun26"),
    "vdot_calibration_date":  _AJ.get("vdot_calibration_date", "2026-06-01"),
    "vdot_calibration":       _AJ.get("vdot_calibration", "heat_adj_only"),
    "vdot_heat_adj_estimate": _AJ.get("vdot_heat_adj_estimate", 40.6),
    "tt_scheduled":           _AJ.get("tt_scheduled", "tm_lt2_done_2026-06-09"),
    "lthr":                   _AJ.get("lthr", 183),    # Friel 30-min TM test 2026-06-09
    "lthr_date":              _AJ.get("lthr_date", "2026-06-09"),
    "lthr_confidence":        _AJ.get("lthr_confidence", "MODERATE"),
}
ATHLETE["hrr"] = ATHLETE["mhr"] - ATHLETE["rhr"]   # derived

# ---------------------------------------------------------------------------
# HR Zones — %HRR (Karvonen) boundaries from athlete.json, LTHR-anchored so
# T-ceiling = LTHR. (E 0.79→163, M 0.862→174, T 0.921→183=LTHR, I 0.95→187)
# ---------------------------------------------------------------------------
_ZP  = _AJ.get("hr_zone_hrr_pct", {"E": 0.79, "M": 0.862, "T": 0.921, "I": 0.95})
_rhr = ATHLETE["rhr"]
_hrr = ATHLETE["hrr"]
_mhr = ATHLETE["mhr"]

HR_ZONES = [
    ("Z1 Easy",       _rhr,                          _rhr + int(_hrr * _ZP["E"])),
    ("Z2 Marathon",   _rhr + int(_hrr * _ZP["E"]),   _rhr + int(_hrr * _ZP["M"])),
    ("Z3 Threshold",  _rhr + int(_hrr * _ZP["M"]),   _rhr + int(_hrr * _ZP["T"])),
    ("Z4 Interval",   _rhr + int(_hrr * _ZP["T"]),   _rhr + int(_hrr * _ZP["I"])),
    ("Z5 Repetition", _rhr + int(_hrr * _ZP["I"]),   _mhr),
]

HR_ZONE_BOUNDS = {
    "Z1_E": (_rhr,                        _rhr + int(_hrr * _ZP["E"])),
    "Z2_M": (_rhr + int(_hrr * _ZP["E"]), _rhr + int(_hrr * _ZP["M"])),
    "Z3_T": (_rhr + int(_hrr * _ZP["M"]), _rhr + int(_hrr * _ZP["T"])),
    "Z4_I": (_rhr + int(_hrr * _ZP["T"]), _rhr + int(_hrr * _ZP["I"])),
    "Z5_R": (_rhr + int(_hrr * _ZP["I"]), _mhr),
}

# Zone classification thresholds (% of HRR) — derived from the same %HRR source.
# A run is zone X if its %HRR < ZONE_PCT[X]; anything >= "I" is Repetition.
ZONE_PCT = {k: round(v * 100) for k, v in _ZP.items()}

# ---------------------------------------------------------------------------
# VDOT Training Paces (sec/km, lo=faster hi=slower) — from athlete.json.
# Derived for current VDOT; edit vdot_paces_sec in athlete.json after a TT.
# ---------------------------------------------------------------------------
_VP = _AJ.get("vdot_paces_sec", {
    "E": [337, 404], "M": [316, 325], "T": [300, 310], "I": [271, 280], "R": [260, 270],
})
VDOT_PACES = {k: tuple(v) for k, v in _VP.items()}

# Nutrition product sodium (mg) — from athlete.json (used by nutrition_calculator).
_NP = _AJ.get("nutrition_products", {"prevo_na_mg": 650, "aminovital_na_mg": 90})
NUTRITION = {
    "prevo_na_mg":      _NP.get("prevo_na_mg", 650),
    "aminovital_na_mg": _NP.get("aminovital_na_mg", 90),
}

# ---------------------------------------------------------------------------
# Quality-run baselines for post-session grading
# ---------------------------------------------------------------------------
BASELINES = {
    "cadence_min":       164,   # spm (double) — min acceptable
    "gct_max":           275,   # ms  — max acceptable at T-pace
    "gct_treadmill_add":  20,   # ms  — allowance for treadmill
    "decoupling_good":     5,   # %   — below = green
    "decoupling_warn":    10,   # %   — above = red
    "stamina_drain_max":  50,   # %   — drain > 50% = very hard
    "vr_good":           8.5,   # %   Vertical Ratio target
    "vr_warn":           9.5,   # %   Vertical Ratio warning
    "power_min":         238,   # W
    "power_max":         265,   # W
}

# ---------------------------------------------------------------------------
# Training Phases (May–Dec 2026, อิงจาก Mileage Plan)
# ---------------------------------------------------------------------------
TRAINING_PHASES = [
    {
        "name":       "Recovery + Phra Rama 8",
        "start":      date(2026, 5, 1),
        "end":        date(2026, 5, 17),
        "phase":      "taper",
        "km_target":  148,
    },
    {
        "name":       "Base Building I",
        "start":      date(2026, 5, 18),
        "end":        date(2026, 7, 31),
        "phase":      "base",
        "km_target":  160,
    },
    {
        "name":       "Base Building II",
        "start":      date(2026, 8, 1),
        "end":        date(2026, 8, 31),
        "phase":      "base",
        "km_target":  180,
    },
    {
        # Quality I ยาว 4 สัปดาห์ — LR 30km สัปดาห์ที่ 3 (Sep 20), deload สัปดาห์ที่ 4
        "name":       "Quality I",
        "start":      date(2026, 9, 1),
        "end":        date(2026, 9, 27),
        "phase":      "quality",
        "km_target":  190,
    },
    {
        # JD B-race prep: ลด volume 30% เฉพาะ 7 วันสุดท้ายก่อน Sponsor21 HM — ไม่ full taper
        "name":       "Pre-Sponsor21 Race Week",
        "start":      date(2026, 9, 28),
        "end":        date(2026, 10, 3),
        "phase":      "taper",
        "km_target":  140,
    },
    {
        # Oct 4 = Sponsor21 HM race | Oct 5-14 = easy recovery | Oct 15-17 = gentle quality return
        "name":       "Sponsor21 + Recovery",
        "start":      date(2026, 10, 4),
        "end":        date(2026, 10, 17),
        "phase":      "quality",
        "km_target":  100,
    },
    {
        # JD Peak Block: LR #1 Oct 18 (30km) + LR #2 Oct 25 (32km) = 2× ≥30km ก่อน taper
        "name":       "Race Specific (Peak)",
        "start":      date(2026, 10, 18),
        "end":        date(2026, 11, 1),
        "phase":      "race_specific",
        "km_target":  200,
    },
    {
        # A-RACE = Bangsaen42 Marathon 15 พ.ย. → 2-week taper ends race day
        "name":       "Taper + Bangsaen42",
        "start":      date(2026, 11, 2),
        "end":        date(2026, 11, 15),
        "phase":      "taper",
        "km_target":  120,
    },
    {
        "name":       "Post-race Recovery (off-season)",
        "start":      date(2026, 11, 16),
        "end":        date(2026, 12, 31),
        "phase":      "taper",
        "km_target":  100,
    },
]

# ---------------------------------------------------------------------------
# Phase-aware session prescriptions
# quality1 = Tuesday default, quality2 = Thursday default
# ---------------------------------------------------------------------------
PHASE_PRESCRIPTIONS = {
    "base": {
        "focus":      "สร้าง Aerobic Base — Volume เป็นหลัก ยังไม่เน้น Intensity",
        "quality1":   {
            "type":    "T",
            "workout": "3×10min @ T-pace | HR 174–183 | rec 2min",
            "note":    "Threshold volume — เน้น duration ไม่ใช่ intensity",
        },
        "quality2":   {
            "type":    "E+strides",
            "workout": "10km E-pace + 6×Strides (เร่ง 20วิ R-effort)",
            "note":    "Base phase — ยังไม่ใช้ Interval เปลี่ยนเป็น Easy+Strides",
        },
        "long_km_start": 16,   # wk 1 ของ phase (FM: ค่อยๆ build)
        "long_km":       24,   # peak ของ phase
        "long_note":  "100% E-pace | HR < 163",
        "quality_max": 1,
    },
    "quality": {
        "focus":      "เพิ่ม VO2max และ Lactate Threshold — 2 Quality ต่อสัปดาห์",
        "quality1":   {
            "type":    "T",
            "workout": "5×8min @ T-pace | HR 174–183 | rec 90วิ",
            "note":    "Threshold intervals — lactate clearance",
        },
        "quality2":   {
            "type":    "I",
            "workout": "5×1km @ I-pace | HR 183–187 | rec 400m E-pace",
            "note":    "VO2max stimulus — สำคัญที่สุดใน Quality Phase",
        },
        "long_km_start": 26,   # wk 1 ของ phase
        "long_km":       30,   # peak ของ phase (FM: ต้องถึง 30km ก่อน taper)
        "long_note":  "75% E-pace + 25% M-pace ช่วงท้าย 5km",
        "quality_max": 2,
    },
    "race_specific": {
        "focus":      "จำลองสภาพการแข่งสนามไทย — Race-pace conditioning",
        "quality1":   {
            "type":    "T",
            "workout": "3×3km @ T-pace continuous | HR 174–183 | rec 3min",
            "note":    "Cruise intervals — T-pace ต่อเนื่องระยะยาวขึ้น",
        },
        "quality2":   {
            "type":    "I+M",
            "workout": "3×1km @ I-pace + 5km @ M-pace | HR mixed",
            "note":    "Speed + race endurance — จำลอง race day",
        },
        "long_km_start": 30,   # JD: 2 ครั้ง ≥30km (Week 1 Oct 18 + Week 2 Oct 25)
        "long_km":       32,   # peak ของ phase (FM: 2 ครั้ง 30km+ ก่อน taper)
        "long_note":  "60% E-pace + 40% M-pace ช่วงท้าย 8km",
        "quality_max": 2,
    },
    "taper": {
        "focus":      "รักษา Sharpness ลด Volume — เน้นพักฟื้นก่อนแข่ง",
        "quality1":   {
            "type":    "T",
            "workout": "2×10min @ T-pace | HR 174–183 | Volume ลด 40%",
            "note":    "Maintenance quality — รักษา sharpness",
        },
        "quality2":   {
            "type":    "easy",
            "workout": "Easy 6km only | HR < 163",
            "note":    "Taper — quality2 ลดเป็น easy run",
        },
        "long_km_start": 22,   # wk 1 ของ taper (ลดจาก race_specific peak)
        "long_km":       12,   # peak (ลดลงเรื่อยๆ จนถึง race)
        "long_note":  "ถ้า ≥ 14 วันก่อนแข่ง: Easy only | HR < 163 | ไม่เพิ่ม stress\n"
                      "ถ้า 7–14 วันก่อนแข่ง (Dress Rehearsal): 5km Easy + 7km @ Race Pace (HM: 11.2 km/h = Sub 1:53 / 11.5 km/h = Sub 1:50) | HR ceiling 174 bpm | ลด 1% incline | เช็ก HR นิ่งก่อนขึ้น Race Pace",
        "quality_max": 1,
    },
}


def get_current_phase(target_date=None):
    """Return the current training phase dict, or None if between phases."""
    d = target_date or date.today()
    for phase in TRAINING_PHASES:
        if phase["start"] <= d <= phase["end"]:
            return phase
    return None


def calculate_paces_from_vdot(vdot: float) -> dict:
    """Calculate Jack Daniels training paces in seconds/km for a given VDOT."""
    import math
    def calculate_pace_sec(intensity_factor):
        vo2_target = vdot * intensity_factor
        a = 0.000104
        b = 0.182258
        c = -4.60 - vo2_target
        discriminant = b**2 - 4*a*c
        if discriminant < 0:
            return 0
        velocity_m_min = (-b + math.sqrt(discriminant)) / (2*a)
        if velocity_m_min <= 0:
            return 0
        pace_sec_km = (1000 / velocity_m_min) * 60
        return int(round(pace_sec_km))

    # Calibrated Jack Daniels intensity factors matching standard tables
    factors = {
        "E": (0.779, 0.620),
        "M": (0.845, 0.814),
        "T": (0.899, 0.864),
        "I": (1.023, 0.980),
        "R": (1.075, 1.028)
    }
    
    return {
        key: (calculate_pace_sec(f_fast), calculate_pace_sec(f_slow))
        for key, (f_fast, f_slow) in factors.items()
    }


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
        
    global HR_ZONES, TRAINING_PHASES, PHASE_PRESCRIPTIONS
    
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
    }
    athlete_updates["hrr"] = athlete_updates["mhr"] - athlete_updates["rhr"]
    ATHLETE.update(athlete_updates)
    
    # Recalculate HR Zones based on new RHR / MHR
    _rhr = ATHLETE["rhr"]
    _hrr = ATHLETE["hrr"]
    _mhr = ATHLETE["mhr"]
    
    new_hr_zones = [
        ("Z1 Easy",       _rhr,                    _rhr + int(_hrr * 0.79)),
        ("Z2 Marathon",   _rhr + int(_hrr * 0.79),  _rhr + int(_hrr * 0.862)),
        ("Z3 Threshold",  _rhr + int(_hrr * 0.862), _rhr + int(_hrr * 0.921)),
        ("Z4 Interval",   _rhr + int(_hrr * 0.921), _rhr + int(_hrr * 0.95)),
        ("Z5 Repetition", _rhr + int(_hrr * 0.95),  _mhr),
    ]
    HR_ZONES.clear()
    HR_ZONES.extend(new_hr_zones)

    HR_ZONE_BOUNDS.update({
        "Z1_E": (_rhr,                     _rhr + int(_hrr * 0.79)),
        "Z2_M": (_rhr + int(_hrr * 0.79),  _rhr + int(_hrr * 0.862)),
        "Z3_T": (_rhr + int(_hrr * 0.862), _rhr + int(_hrr * 0.921)),
        "Z4_I": (_rhr + int(_hrr * 0.921), _rhr + int(_hrr * 0.95)),
        "Z5_R": (_rhr + int(_hrr * 0.95),  _mhr),
    })
    
    # Recalculate paces mathematically from new VDOT
    new_paces = calculate_paces_from_vdot(ATHLETE["vdot"])
    VDOT_PACES.update(new_paces)
    
    # Load custom baselines if present
    if "baselines" in profile:
        BASELINES.update(profile["baselines"])
        
    # Load custom training phases if present
    if "training_phases" in profile:
        TRAINING_PHASES.clear()
        TRAINING_PHASES.extend(profile["training_phases"])
        
    # Load custom phase prescriptions if present
    if "phase_prescriptions" in profile:
        PHASE_PRESCRIPTIONS.update(profile["phase_prescriptions"])
        
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

