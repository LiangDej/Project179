"""
jd_rules.py — Jack Daniels training-load limits + workout rendering (v1.8).

ONE place for the JD rules every planner must respect, and for turning a
structured workout spec (training_plan.json) into the text the athlete sees,
with paces/HR taken from config at call time — never typed into a string.

Pure module: no config import; callers pass paces (sec/km) and zone bounds.

JD limits (Daniels' Running Formula, 3rd ed., ch. 4):
  E   HR ceiling 79 % HRmax (E = 65–79 % HRmax / 59–74 % VO2max)
  M   per session ≤ lesser of 20 % weekly volume or 29 km
  T   per session ≤ 10 % weekly volume, but a 20-min steady tempo (JD's
      basic T workout) is always allowed — so low-volume weeks still get T
  I   per session ≤ lesser of 8 % weekly volume or 10 km
  R   per session ≤ lesser of 5 % weekly volume or 8 km
  L   long run ≤ 25 % weekly volume (30 % if < 64 km/wk) and ≤ 150 min —
      the % part is made monotonic (no drop at 64 km/wk: 30 % of 64 km is
      the floor above it). Marathon build / race-specific phases
      (training_plan.json long_rule "marathon") use JD's marathon-plan limit:
      ≤ 150 min only, no %-of-week cap.
T/I/R are prescribed by PACE (HR lags in 3–5 min reps and wrist HR can lock
onto cadence); HR is shown only as a ceiling.
"""
from __future__ import annotations

import math

JD_E_MAX_PCT_HRMAX = 0.79
QUALITY_CAP = {            # kind: (fraction of weekly km, absolute km cap or None)
    "M": (0.20, 29.0),
    "T": (0.10, None),
    "I": (0.08, 10.0),
    "R": (0.05, 8.0),
}
T_MIN_MINUTES = 20        # JD steady tempo run
LONG_PCT_HIGH_VOL = 0.25
LONG_PCT_LOW_VOL = 0.30
LONG_LOW_VOL_KM = 64.0      # ≈ 40 mi/wk
LONG_MAX_MIN = 150


def fmt_pace(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def belt_kmh(sec_per_km: float) -> float:
    return round(3600 / sec_per_km, 1)


def mid(rng) -> float:
    return (rng[0] + rng[1]) / 2


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
def quality_cap_km(kind: str, weekly_km: float | None, pace: tuple | None = None) -> float | None:
    """Max km of `kind` pace in ONE session. None = no rule / no weekly volume.
    For T, pass the T pace so the 20-min tempo floor can be applied."""
    rule = QUALITY_CAP.get(kind)
    if rule is None or not weekly_km:
        return None
    pct, abs_cap = rule
    cap = weekly_km * pct
    cap = min(cap, abs_cap) if abs_cap else cap
    if kind == "T" and pace:
        cap = max(cap, T_MIN_MINUTES * 60 / mid(pace))
    return round(cap, 1)


def _pct_cap(weekly_km: float) -> tuple[float, str]:
    """Monotonic %-of-week cap: 30 % below 64 km/wk; above, the larger of
    25 % and the 30 %-of-64 km value (v1.8.1 — 63 km gave 18.9, 64 gave 16.0)."""
    if weekly_km < LONG_LOW_VOL_KM:
        return weekly_km * LONG_PCT_LOW_VOL, f"JD ≤30% ของ {weekly_km:g} km/สัปดาห์"
    floor = LONG_LOW_VOL_KM * LONG_PCT_LOW_VOL
    hi = weekly_km * LONG_PCT_HIGH_VOL
    if hi >= floor:
        return hi, f"JD ≤25% ของ {weekly_km:g} km/สัปดาห์"
    return floor, f"JD ≤{floor:g} km (30% ของ {LONG_LOW_VOL_KM:g} km)"


def long_run_cap(weekly_km: float | None, e_pace: tuple,
                 rule: str = "general") -> tuple[float | None, str]:
    """(max long-run km, reason).
    general  : lesser of the %-of-week cap and 150 min at E pace
    marathon : 150 min at E pace only (JD marathon plans)"""
    time_cap = LONG_MAX_MIN * 60 / mid(e_pace) if e_pace else None
    if rule == "marathon" or not weekly_km:
        return (round(time_cap, 1) if time_cap else None,
                f"JD ≤{LONG_MAX_MIN} นาที" + (" (marathon plan)" if rule == "marathon" else ""))
    pct_cap, why = _pct_cap(weekly_km)
    if time_cap is not None and time_cap < pct_cap:
        return round(time_cap, 1), f"JD ≤{LONG_MAX_MIN} นาที"
    return round(pct_cap, 1), why


def check_long_run(actual_km: float, actual_min: float, weekly_km: float | None,
                   e_pace: tuple, rule: str = "general") -> str | None:
    """Warning text when a completed long run broke the JD limit, else None."""
    cap, why = long_run_cap(weekly_km, e_pace, rule)
    over = []
    if cap and actual_km > cap + 0.5:
        over.append(f"{actual_km:.1f} km > {cap:g} km ({why})")
    if actual_min and actual_min > LONG_MAX_MIN + 5:
        over.append(f"{actual_min:.0f} นาที > {LONG_MAX_MIN} นาที")
    return "Long run เกินเพดาน JD: " + " | ".join(over) if over else None


def check_quality(kind: str, km: float, weekly_km: float | None,
                  pace: tuple | None = None) -> str | None:
    cap = quality_cap_km(kind, weekly_km, pace)
    if cap and km > cap + 0.3:
        return f"{kind} {km:.1f} km > เพดาน JD {cap:g} km/ครั้ง"
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _rep_km(spec: dict, pace: tuple) -> float:
    if spec.get("rep_km"):
        return float(spec["rep_km"])
    if spec.get("rep_m"):
        return float(spec["rep_m"]) / 1000
    if spec.get("rep_min"):
        return float(spec["rep_min"]) * 60 / mid(pace)
    if spec.get("km"):
        return float(spec["km"])
    return 0.0


def _rep_label(spec: dict) -> str:
    if spec.get("rep_min"):
        return f"{spec['rep_min']}min"
    if spec.get("rep_km"):
        return f"{spec['rep_km']:g}km"
    if spec.get("rep_m"):
        return f"{spec['rep_m']}m"
    return f"{spec.get('km', '?')}km"


def _render_part(spec: dict, weekly_km, paces: dict, zones: dict) -> dict:
    kind = spec["type"]
    pace = paces[kind]
    rep_km = _rep_km(spec, pace)
    reps = int(spec.get("reps", 1))
    cap = quality_cap_km(kind, weekly_km, pace)
    capped = None
    if cap and rep_km and reps * rep_km > cap + 0.05:
        new = max(1, math.floor(cap / rep_km))
        if new < reps:
            capped = f"{kind}: {reps}→{new} reps (เพดาน JD {cap:g} km)"
            reps = new
    p = f"{fmt_pace(pace[0])}–{fmt_pace(pace[1])}/km (ลู่ {belt_kmh(pace[1])}–{belt_kmh(pace[0])} km/h)"
    hr_cap = {"M": zones["Z2_M"][1], "T": zones["Z3_T"][1]}.get(kind)
    hr_txt = f" | HR เพดาน ≤{hr_cap}" if hr_cap else " | คุมด้วย pace (HR ขึ้นไม่ทันในรอบสั้น)"
    rest = f" | rec {spec['rest']}" if spec.get("rest") else ""
    head = f"{reps}×{_rep_label(spec)}" if reps > 1 or spec.get("reps") else _rep_label(spec)
    final = dict(spec)
    if spec.get("reps") or capped:
        final["reps"] = reps
    return {"text": f"{head} @ {kind} {p}{hr_txt}{rest}",
            "km": round(reps * rep_km, 1), "kind": kind, "capped": capped, "spec": final}


def render_quality(spec: dict, weekly_km: float | None, paces: dict, zones: dict) -> dict:
    """Workout spec → {"type","workout","note","quality_km","capped":[..]}.

    spec types: T / I / R / M (reps of rep_min|rep_km|rep_m, or km),
    "combo" (parts: [...]), "E+strides" (km, strides), "easy" (km).
    """
    t = spec.get("type", "T")
    e_ceil = zones["Z1_E"][1]
    e = paces["E"]
    if t == "easy":
        return {"type": "easy", "workout": f"Easy {spec.get('km', 6)}km | {fmt_pace(e[0])}–{fmt_pace(e[1])}/km | HR < {e_ceil}",
                "note": spec.get("note", ""), "quality_km": 0.0, "capped": [], "spec": dict(spec)}
    if t == "E+strides":
        return {"type": t, "spec": dict(spec),
                "workout": (f"{spec.get('km', 8)}km E {fmt_pace(e[0])}–{fmt_pace(e[1])}/km (HR < {e_ceil}) "
                            f"+ {spec.get('strides', 6)}×Strides 20วิ @ R {fmt_pace(paces['R'][0])}/km"),
                "note": spec.get("note", ""), "quality_km": 0.0, "capped": []}
    parts = spec["parts"] if t == "combo" else [spec]
    rendered = [_render_part(p, weekly_km, paces, zones) for p in parts]
    final = ({**spec, "parts": [r["spec"] for r in rendered]} if t == "combo" else rendered[0]["spec"])
    return {"type": t if t != "combo" else "+".join(p["kind"] for p in rendered),
            "spec": final,
            "workout": " + ".join(r["text"] for r in rendered),
            "note": spec.get("note", ""),
            "quality_km": round(sum(r["km"] for r in rendered), 1),
            "capped": [r["capped"] for r in rendered if r["capped"]]}


def long_m_finish(spec: dict, km: float, weekly_km: float | None = None) -> float:
    """M-pace finish km: ≤ half the run and ≤ the JD M cap (20 % week / 29 km)."""
    mf = float(spec.get("m_finish_km") or 0)
    if not mf:
        return 0.0
    mf = min(mf, km * 0.5)
    cap = quality_cap_km("M", weekly_km)
    return round(min(mf, cap) if cap else mf, 1)


def render_long(spec: dict, km: float, paces: dict, zones: dict,
                weekly_km: float | None = None) -> str:
    """Long-run text: E pace + optional M-pace finish (see long_m_finish)."""
    e, m = paces["E"], paces["M"]
    txt = f"{km:g}km E {fmt_pace(e[0])}–{fmt_pace(e[1])}/km (HR < {zones['Z1_E'][1]})"
    mf = long_m_finish(spec, km, weekly_km)
    if mf:
        txt += f" — {mf:g} km สุดท้าย @ M {fmt_pace(m[0])}–{fmt_pace(m[1])}/km (HR ≤{zones['Z2_M'][1]})"
    if spec.get("note"):
        txt += f" | {spec['note']}"
    return txt
