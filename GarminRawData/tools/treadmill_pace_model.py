"""
treadmill_pace_model.py — Single source for TM HR→Pace interpolation.

Imported by: tm_patch.py, session_logger.py
DO NOT duplicate the anchor table elsewhere.

Calibration: Bangkok TM sessions, 20°C, incline 1%, Garmin Forerunner optical HR.
Note: TM optical HR tends to be ~4–6 bpm lower than outdoor at same pace
      due to arm cadence interference — anchors reflect actual TM readings.
Zone labels reference LTHR 183 (Friel TM test 9 มิ.ย. 26): E<163, M 163–174,
T 174–183, I 183–187. Anchors refined with that test's measured HR↔speed.
"""

from __future__ import annotations

# (avg_hr_bpm, pace_sec_per_km) — measured TM calibration points.
# Easy + top-end anchors verified against the 9 มิ.ย. 26 LT2 test (clean 30-min).
TM_HR_ANCHORS: list[tuple[int, int]] = [
    (140, 7*60 + 9),    #  8.4 km/h  Recovery
    (150, 6*60 + 40),   #  9.0 km/h  Easy        (LT2 test: 9.0 → HR 150)
    (162, 5*60 + 46),   # 10.4 km/h  Marathon
    (167, 5*60 + 30),   # 10.9 km/h  Marathon
    (175, 5*60 + 10),   # 11.6 km/h  Threshold floor (LT2: 11.6 → HR 175)
    (183, 5*60 + 0),    # 12.0 km/h  Threshold ceiling = LTHR (LT2: 12.0 → HR 183)
]


def infer_pace_from_hr(avg_hr: float | None) -> str | None:
    """
    Interpolate TM pace (M:SS/km) from average HR using calibration anchors.

    Returns None if avg_hr is falsy.
    Clamps to anchor range (no extrapolation beyond 183 bpm = LTHR).
    Appends '~' suffix to indicate this is an estimate, not belt speed.

    Args:
        avg_hr: average HR in bpm (float)
    Returns:
        Pace string e.g. "5:46/km~" or None
    """
    if not avg_hr:
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
