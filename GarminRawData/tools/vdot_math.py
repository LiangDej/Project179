"""
vdot_math.py — Single source for Jack Daniels VDOT formulas.

Imported by: race_predictor.py, post_race_updater.py
DO NOT duplicate these formulas elsewhere.

Reference: Daniels, J. (2014). Daniels' Running Formula, 3rd ed.
  VO2 = 0.182258·v + 0.000104·v² - 4.60  (v = m/min)
  %VO2max = 0.8 + 0.1894393·e^(-0.012778·t) + 0.2989558·e^(-0.1932605·t)
  VDOT = VO2 / %VO2max
"""

import math
import typing


def compute_vdot(distance_m: float, duration_min: float,
                 min_dist_m: float = 1000) -> typing.Optional[float]:
    """
    Compute VDOT from distance (m) and duration (minutes).
    Returns None if inputs are invalid or below min_dist_m.

    Args:
        distance_m:   race/effort distance in metres
        duration_min: time in minutes
        min_dist_m:   minimum valid distance (default 1000m)
    """
    if distance_m < min_dist_m or duration_min <= 0:
        return None
    v    = distance_m / duration_min          # velocity m/min
    vo2  = 0.182258 * v + 0.000104 * v**2 - 4.60
    pct  = (0.8
            + 0.1894393 * math.exp(-0.012778 * duration_min)
            + 0.2989558 * math.exp(-0.1932605 * duration_min))
    if pct <= 0:
        return None
    return round(vo2 / pct, 2)


def predict_race_time(vdot: float, distance_m: float) -> float:
    """
    Predict race finishing time (minutes) for a given VDOT and distance.
    Uses binary search to invert the VDOT equation.

    Args:
        vdot:       VDOT value
        distance_m: race distance in metres
    Returns:
        Predicted finish time in decimal minutes
    """
    lo, hi = 1.0, 1440.0
    for _ in range(60):
        mid = (lo + hi) / 2
        v   = compute_vdot(distance_m, mid, min_dist_m=0)
        if v is None:
            hi = mid
        elif v > vdot:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)


def vdot_to_pace_sec(vdot: float, pct_vo2max: float) -> float:
    """
    Convert VDOT + %VO2max intensity → pace in sec/km.

    Args:
        vdot:        VDOT value
        pct_vo2max:  fraction of VO2max (e.g. 0.88 for Threshold)
    Returns:
        Pace in seconds per km
    """
    vo2_target = vdot * pct_vo2max
    # Solve: vo2_target = 0.182258·v + 0.000104·v²  - 4.60
    a = 0.000104
    b = 0.182258
    c = -(vo2_target + 4.60)
    disc = b**2 - 4 * a * c
    if disc < 0:
        return 360.0
    vel_m_min = (-b + math.sqrt(disc)) / (2 * a)
    if vel_m_min <= 0:
        return 360.0
    return round(1000 / vel_m_min * 60, 1)


# ---------------------------------------------------------------------------
# JD training paces — the ONE place paces are derived from VDOT (v1.8).
# config.VDOT_PACES calls this; athlete.json no longer stores paces (it used to
# carry hand-typed vdot_paces_sec that had drifted: M 5:16 vs JD 5:26 @ VDOT 40).
#   E  = 59–74 % VO2max                     (Daniels 3rd ed., ch. 4)
#   M  = predicted marathon race pace        (Daniels: M = goal/VDOT FM pace)
#   T  = 86–88 % VO2max  (~60-min race pace; 88 % reproduces the JD tables)
#   I  = 97.5–100 % VO2max (vVO2max)
#   R  = 1500 m – mile race pace
# M is a single pace in JD; ±JD_SINGLE_PACE_TOL_S gives a usable watch band.
# ---------------------------------------------------------------------------
JD_PCT = {"E": (0.74, 0.59), "T": (0.88, 0.86), "I": (1.00, 0.975)}
JD_SINGLE_PACE_TOL_S = 3


def jd_paces(vdot: float) -> dict:
    """{zone: (fast_sec_per_km, slow_sec_per_km)} for E/M/T/I/R."""
    out = {k: (int(round(vdot_to_pace_sec(vdot, hi))), int(round(vdot_to_pace_sec(vdot, lo))))
           for k, (hi, lo) in JD_PCT.items()}
    m = predict_race_time(vdot, 42195) * 60 / 42.195
    out["M"] = (int(round(m - JD_SINGLE_PACE_TOL_S)), int(round(m + JD_SINGLE_PACE_TOL_S)))
    r_fast = predict_race_time(vdot, 1500) * 60 / 1.5
    r_slow = predict_race_time(vdot, 1609.34) * 60 / 1.60934
    out["R"] = (int(round(r_fast)), int(round(r_slow)))
    return {k: out[k] for k in ("E", "M", "T", "I", "R")}
