"""
readiness.py — the ONE readiness + taper engine (v1.8).

daily_brief (today) and session_prescriber (the week) both decide here, so the
brief and the weekly plan can no longer disagree (before v1.8 each had its own
rules: the prescriber ignored RHR, used other BB cut-offs and never applied the
strictest-rule-wins logic).

Taper (by race tier) follows the taper research rather than "no quality":
Bosquet et al. 2007 (meta-analysis, MSSE 39:1358) and Mujika & Padilla 2003 —
cut VOLUME 41–60 % progressively, KEEP INTENSITY and frequency. So an A-race
keeps a short maintenance T session until T-5; only the last 4 days are easy.
"""
from __future__ import annotations

from datetime import date

RHR_WARN_DELTA = 5    # bpm above baseline → no quality/long today
RHR_HIGH_DELTA = 8    # bpm above baseline → easy/short only (illness/overreach)
BB_REST = 20
BB_LOW = 40
BB_QUALITY_OK = 60
BB_STRENGTH_MIN = 30
EASY_CAP_KM = 8       # default distance cap when a hard day is downgraded
SHORT_CAP_KM = 6

#   window      days before race where taper rules start
#   no_quality  quality becomes easy at ≤ this many days (intensity kept before)
#   race_week   ≤ this many days = "Race Week" (bigger volume cut)
#   long_cap    max long-run km inside the window
#   vol_early / vol_race  volume cut vs peak week (Bosquet: 41–60 %)
TAPER_PROFILES = {
    "A": {"window": 14, "no_quality": 4, "race_week": 7, "long_cap": 12,
          "vol_early": "40%", "vol_race": "60%"},
    "B": {"window": 7,  "no_quality": 3, "race_week": 3, "long_cap": 14,
          "vol_early": "20%", "vol_race": "40%"},
    "C": {"window": 3,  "no_quality": 2, "race_week": 2, "long_cap": 10,
          "vol_early": "0%",  "vol_race": "20%"},
}


def taper_profile(race: dict) -> dict:
    """Taper rules for a race entry (tier-based, races.json taper_days override)."""
    prof = dict(TAPER_PROFILES.get(race.get("tier") or "A", TAPER_PROFILES["A"]))
    if race.get("taper_days"):
        prof["window"] = int(race["taper_days"])
    return prof


def active_races(active_only: bool = True) -> list[dict]:
    """Races from races.json (race_registry). [] if none — no fallback race."""
    try:
        from race_registry import list_races
        return [{"name": r["name"], "date": date.fromisoformat(r["date"]),
                 "tier": r.get("tier", "A"), "taper_days": r.get("taper_days"),
                 "dist_km": float(r.get("dist_km") or 0)}
                for _, r in list_races(active_only=active_only) if r.get("tier") != "template"]
    except Exception:
        return []


# JD post-race recovery: one easy day (no quality, no long) per 3 km raced
# (Daniels' Running Formula — "1 easy day for every 3 km of the race").
JD_RECOVERY_KM_PER_DAY = 3.0


def post_race_recovery(on_date: date, races: list[dict] | None = None):
    """(race name, days since, recovery days) if on_date is inside the JD
    post-race easy window, else None."""
    import math
    best = None
    for r in (active_races(active_only=False) if races is None else races):
        since = (on_date - r["date"]).days
        need = math.ceil((r.get("dist_km") or 0) / JD_RECOVERY_KM_PER_DAY)
        if 1 <= since <= need and (best is None or since < best[1]):
            best = (r["name"], since, need)
    return best


def active_taper(on_date: date, races: list[dict] | None = None):
    """Nearest race whose OWN tier window covers on_date → (name, days, profile)."""
    live = []
    for r in (active_races() if races is None else races):
        days = (r["date"] - on_date).days
        if days < 0:
            continue
        prof = taper_profile(r)
        if days <= prof["window"]:
            live.append((days, r["name"], prof))
    if not live:
        return None, None, None
    days, name, prof = min(live, key=lambda x: x[0])
    return name, days, prof


def readiness_assess(bb, hrv_status, day_type, pain="none", taper_days=None,
                     rhr=None, rhr_baseline=None, no_quality=None, planning=False,
                     recovery=None):
    """Evaluate EVERY readiness rule and return the STRICTEST outcome.

    REST beats MODIFY beats CAUTION beats GO; among MODIFYs the smallest
    distance cap wins; the other triggered reasons are kept.

    no_quality : taper says no quality today (from the race's taper profile).
                 None → derived from the A-race profile and taper_days.
    planning   : True for FUTURE days in a weekly plan — today's BB/HRV/RHR
                 say nothing about next Sunday, so only pain/taper rules apply.
    recovery   : post_race_recovery() tuple → quality/long become easy.

    Returns dict: decision, reason, cap_km (None = no cap), others [str].
    """
    if day_type == "rest":
        return {"decision": "REST", "reason": "🛌 วันพักตามตาราง ห้ามซ้อมเด็ดขาด",
                "cap_km": 0, "others": []}
    if day_type == "strength":
        if not planning and bb is not None and bb < BB_STRENGTH_MIN:
            return {"decision": "MODIFY", "reason": "🟡 BB ต่ำมาก — ลด Weight Training เหลือ Mobility เท่านั้น",
                    "cap_km": None, "others": []}
        return {"decision": "GO", "reason": "✅ ทำ Strength ตามแผน", "cap_km": None, "others": []}

    is_q = day_type in ("quality1", "quality2")
    is_hard = is_q or day_type == "long"
    if no_quality is None:
        no_quality = taper_days is not None and taper_days <= TAPER_PROFILES["A"]["no_quality"]
    hrv = (hrv_status or "unknown").lower()
    rhr_d = round(rhr - rhr_baseline, 1) if (rhr and rhr_baseline) else None
    votes = []   # (decision, cap_km, msg)

    if recovery and is_hard:
        votes.append(("MODIFY", EASY_CAP_KM,
                      f"🟡 ฟื้นหลังแข่ง {recovery[0]} วันที่ {recovery[1]}/{recovery[2]} "
                      f"(JD: easy 1 วันต่อ {JD_RECOVERY_KM_PER_DAY:g} km ที่แข่ง)"))
    if no_quality and is_q:
        votes.append(("MODIFY", EASY_CAP_KM, f"🟡 Taper T-{taper_days} — ช่วงท้าย taper: Quality → Easy"))
    if pain in ("mild", "moderate") and is_hard:
        votes.append(("MODIFY", EASY_CAP_KM if pain == "mild" else 5,
                      f"🟡 มีอาการบาดเจ็บ ({pain}) — ลดระดับเป็น Easy Run เท่านั้น"))
    if not planning:
        if bb is None:
            votes.append(("CAUTION", None, "⚠️ ไม่พบข้อมูล Body Battery — ใช้ความรู้สึกตัวเองตัดสิน"))
        if rhr_d is not None:
            if rhr_d >= RHR_HIGH_DELTA:
                votes.append(("MODIFY", SHORT_CAP_KM, f"🟡 RHR {rhr} สูงกว่า baseline +{rhr_d:g} bpm — "
                                                      "ยังไม่ฟื้น/อาจป่วย: Easy สั้น ≤40 นาที หรือพัก"))
            elif rhr_d >= RHR_WARN_DELTA and is_hard:
                votes.append(("MODIFY", EASY_CAP_KM,
                              f"🟡 RHR {rhr} สูงกว่า baseline +{rhr_d:g} bpm — ตัด Quality/Long → Easy"))
        if hrv == "unbalanced":
            if bb is not None and bb < BB_STRENGTH_MIN:
                votes.append(("REST", 0, "🔴 HRV ต่ำกว่าช่วงปกติ + BB ต่ำมาก — ร่างกายล้าสะสม พักผ่อน 100%"))
            else:
                votes.append(("MODIFY", EASY_CAP_KM if is_hard else SHORT_CAP_KM,
                              "🟡 HRV เมื่อคืนต่ำกว่าช่วงปกติ — ระบบประสาทไม่พร้อม ปรับลดความหนักลง"))
        if bb is not None:
            if bb < BB_REST:
                votes.append(("REST", 0, "🔴 BB ต่ำมาก — พักผ่อน อย่าฝืน"))
            elif bb < BB_LOW:
                votes.append(("MODIFY", SHORT_CAP_KM, "🟡 BB ต่ำ — Easy สั้นเท่านั้น"))
            elif (bb < BB_QUALITY_OK or hrv != "balanced") and is_q:
                votes.append(("MODIFY", EASY_CAP_KM, "🟡 BB ปานกลาง/HRV ไม่ balanced — ลด Quality → Easy Run แทน"))

    rank = {"REST": 3, "MODIFY": 2, "CAUTION": 1}
    if votes:
        votes.sort(key=lambda v: (-rank[v[0]], v[1] if v[1] is not None else 99))
        d, cap, msg = votes[0]
        return {"decision": d, "reason": msg, "cap_km": cap,
                "others": [v[2] for v in votes[1:]]}
    if is_q:
        return {"decision": "GO", "reason": "✅ พร้อมเต็มที่ — วิ่ง Quality ตามแผน", "cap_km": None, "others": []}
    return {"decision": "GO", "reason": "✅ พร้อม — วิ่งตามแผน", "cap_km": None, "others": []}


def readiness_decision(bb, hrv_status, day_type, pain="none", taper_days=None,
                       rhr=None, rhr_baseline=None):
    """Back-compat wrapper → (decision, reason)."""
    a = readiness_assess(bb, hrv_status, day_type, pain, taper_days, rhr, rhr_baseline)
    return a["decision"], a["reason"]
