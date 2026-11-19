"""
treadmill_pace_model.py — Single source for TM HR→Pace interpolation.

Imported by: session_logger.py
DO NOT duplicate the anchor table elsewhere.

Calibration points are DATA: athlete.json → lt2_test.tm_hr_anchors
([avg_hr, pace_sec_per_km], measured on the treadmill — v1.8 moved them out
of this file so a retest is a data edit, not a code change).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "skills" / "garmin_coach_mcp"))
from config import ATHLETE  # noqa: E402

# (avg_hr_bpm, pace_sec_per_km) — measured TM calibration points, sorted by HR.
TM_HR_ANCHORS: list[tuple[int, int]] = sorted(
    (int(h), int(p)) for h, p in ATHLETE.get("lt2_test", {}).get("tm_hr_anchors", []))


def infer_pace_from_hr(avg_hr: float | None) -> str | None:
    """
    Interpolate TM pace (M:SS/km) from average HR using calibration anchors.

    Returns None if avg_hr is falsy.
    Clamps to the anchor range (no extrapolation).
    Appends '~' suffix to indicate this is an estimate, not belt speed.

    Args:
        avg_hr: average HR in bpm (float)
    Returns:
        Pace string e.g. "5:46/km~" or None
    """
    if not avg_hr or len(TM_HR_ANCHORS) < 2:
        return None

    hr = float(avg_hr)
    lo_hr, lo_pace = TM_HR_ANCHORS[0]
    hi_hr, hi_pace = TM_HR_ANCHORS[-1]

    if hr <= lo_hr:
        pace_s = float(lo_pace)
    elif hr >= hi_hr:
        pace_s = float(hi_pace)
    else:
        pace_s = float(lo_pace)
        for i in range(len(TM_HR_ANCHORS) - 1):
            h0, p0 = TM_HR_ANCHORS[i]
            h1, p1 = TM_HR_ANCHORS[i + 1]
            if h0 <= hr <= h1:
                t = (hr - h0) / (h1 - h0)
                pace_s = p0 + t * (p1 - p0)
                break

    m, s = divmod(int(pace_s), 60)
    return f"{m}:{s:02d}/km"
