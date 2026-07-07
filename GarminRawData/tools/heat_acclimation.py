#!/usr/bin/env python3
"""
heat_acclimation.py — TRIMP heat-adjusted training load + Bangkok→Fuji transition tracker

Science:
  - TRIMP (Bannister 1991): duration_min × HR_ratio × e^(b × HR_ratio)
    where HR_ratio = (avgHR - RHR) / (maxHR - RHR), b=1.92 (men)
  - Heat-adjusted TRIMP: TRIMP × WBGT_multiplier
    WBGT from Bangkok monthly climate (Stull 2011 approximation)
  - Heat acclimation: substantial adaptation ~10–14 days heat exposure
    (Nielsen & Nybo 2003; Lorenzo et al. 2010)
  - Acclimation decay: ~50% retained at 2 weeks, lost by 4 weeks
    (Pandolf 1988; Armstrong & Maresh 1991)
  - Plasma volume: ~8–10% expansion (Convertino 1991). Performance benefit is
    solid IN THE HEAT (~4-7%, Periard 2015); cool-condition carry-over (e.g. Fuji
    in Dec) is CONTESTED (Lorenzo 2010 found ~3%; Karlsen/Keiser 2015 found none).
    (Convertino 1991; Lorenzo et al. 2010)

Usage:
    python3 heat_acclimation.py                  # current acclimation status
    python3 heat_acclimation.py --days 60        # 60-day window
    python3 heat_acclimation.py --race-plan      # Bangkok→Fuji transition advice
"""

import sys, os, json, math, argparse
from datetime import date, timedelta
from pathlib import Path

TOOLS_DIR = Path(__file__).parent
BASE_DIR  = TOOLS_DIR.parent
COACH_MCP = BASE_DIR.parent / "skills" / "garmin_coach_mcp"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from config import ATHLETE  # noqa: E402

ACTS_FILE  = BASE_DIR / "running_activities_all.json"
CACHE_DIR  = Path(__file__).resolve().parent.parent / "wellness"

RHR = ATHLETE["rhr"]
MHR = ATHLETE["mhr"]

# Race date — SINGLE SOURCE: races.json (active race). ATM Nov 29 ในกรุงเทพ = ร้อน →
# heat acclimation relevant ยิ่งกว่า Fuji (cold) เสียอีก. (ชื่อตัวแปรคงไว้เพื่อ backward-compat)
try:
    from race_registry import active_race
    FUJI_RACE_DATE = date.fromisoformat(active_race()["date"])
except Exception:
    FUJI_RACE_DATE = date(2026, 11, 29)

# ---------------------------------------------------------------------------
# Bangkok monthly WBGT (6 AM training time — Stull 2011 approx)
# ---------------------------------------------------------------------------
BANGKOK_WBGT = {
    1: 21.0, 2: 22.5, 3: 24.0, 4: 25.5,
    5: 26.5, 6: 26.8, 7: 26.5, 8: 26.5,
    9: 26.2, 10: 25.0, 11: 23.5, 12: 21.5,
}

# WBGT → heat stress multiplier for TRIMP
# Below 21°C (temperate): no heat stress bonus
# Above 28°C: significant cardiovascular strain
def wbgt_multiplier(wbgt: float) -> float:
    if wbgt < 21:
        return 1.0
    elif wbgt < 24:
        return 1.08 + (wbgt - 21) / 3 * 0.07   # 1.08–1.15
    elif wbgt < 27:
        return 1.15 + (wbgt - 24) / 3 * 0.10   # 1.15–1.25
    else:
        return min(1.35, 1.25 + (wbgt - 27) * 0.05)


# ---------------------------------------------------------------------------
# TRIMP (Bannister 1991, gender-corrected for male)
# ---------------------------------------------------------------------------
def calc_trimp(avg_hr: float, duration_sec: float) -> float:
    if avg_hr <= RHR or duration_sec <= 0:
        return 0.0
    hrr_denom = MHR - RHR
    if hrr_denom <= 0:
        return 0.0
    hr_ratio = (avg_hr - RHR) / hrr_denom
    hr_ratio  = max(0.0, min(hr_ratio, 1.0))
    dur_min  = duration_sec / 60.0
    b = 1.92  # male coefficient (Bannister 1991)
    return round(dur_min * hr_ratio * math.exp(b * hr_ratio), 1)


# ---------------------------------------------------------------------------
# Acclimation score: rolling 14-day weighted sum of heat TRIMP
# Weights decay linearly with age (day 1 = weight 1.0, day 14 = 0.07)
# Threshold for "well acclimated": score ≥ 200 heat-TRIMP points
# ---------------------------------------------------------------------------
ACCL_THRESHOLD_GOOD = 200   # well acclimated
ACCL_THRESHOLD_FAIR = 100   # partial acclimation

def acclimation_score(daily_heat_trimp: dict[str, float], as_of: date, window: int = 14) -> float:
    total = 0.0
    for i in range(window):
        d     = as_of - timedelta(days=i)
        ds    = str(d)
        htrimp = daily_heat_trimp.get(ds, 0.0)
        weight = (window - i) / window   # linear decay, today=1.0, oldest=1/14
        total += htrimp * weight
    return round(total, 1)


# ---------------------------------------------------------------------------
# Acclimation decay after last heat exposure
# Pandolf 1988: ~50% loss at 14 days, near-complete at 28+ days
# ---------------------------------------------------------------------------
def acclimation_retained(days_since_heat: int, peak_score: float) -> float:
    if days_since_heat <= 0:
        return peak_score
    decay = math.exp(-days_since_heat / 20.0)  # τ≈20 days
    return round(peak_score * decay, 1)


# ---------------------------------------------------------------------------
# Load activities and compute daily heat-adjusted TRIMP
# ---------------------------------------------------------------------------
def load_heat_trimp(days: int = 90) -> dict[str, dict]:
    today  = date.today()
    cutoff = today - timedelta(days=days)

    try:
        acts = json.loads(ACTS_FILE.read_text())
    except Exception:
        return {}

    daily: dict[str, dict] = {}

    for a in acts:
        ts = a.get("startTimeLocal", "")[:10]
        if not ts:
            continue
        try:
            d = date.fromisoformat(ts)
        except ValueError:
            continue
        if d < cutoff:
            continue

        avg_hr   = a.get("averageHR", 0) or 0
        dur_sec  = a.get("duration", 0) or 0
        trimp    = calc_trimp(avg_hr, dur_sec)

        # WBGT from Bangkok monthly climate (training location = Bangkok)
        wbgt     = BANGKOK_WBGT.get(d.month, 25.0)
        mult     = wbgt_multiplier(wbgt)
        h_trimp  = round(trimp * mult, 1)
        bonus    = round(h_trimp - trimp, 1)

        ds = str(d)
        if ds not in daily:
            daily[ds] = {"trimp": 0.0, "h_trimp": 0.0, "wbgt": wbgt,
                         "mult": mult, "bonus": bonus, "sessions": 0}
        daily[ds]["trimp"]    += trimp
        daily[ds]["h_trimp"]  += h_trimp
        daily[ds]["bonus"]    += bonus
        daily[ds]["sessions"] += 1

    return daily


# ---------------------------------------------------------------------------
# Main analysis function for API and CLI
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Main analysis function for API and CLI
# ---------------------------------------------------------------------------
def analyze(days: int = 60) -> dict:
    today = date.today()
    daily = load_heat_trimp(days)

    # Build daily heat_trimp lookup (date_str -> h_trimp)
    daily_ht = {ds: v["h_trimp"] for ds, v in daily.items()}

    # Current acclimation score (14-day window)
    score_now = acclimation_score(daily_ht, today, window=14)

    # Status label
    if score_now >= ACCL_THRESHOLD_GOOD:
        accl_status = "Well acclimated"
    elif score_now >= ACCL_THRESHOLD_FAIR:
        accl_status = "Partially acclimated"
    else:
        accl_status = "Minimal acclimation"

    # Rolling 7-day plain TRIMP and heat TRIMP
    trimp_7d   = sum(daily.get(str(today - timedelta(days=i)), {}).get("trimp",   0) for i in range(7))
    h_trimp_7d = sum(daily.get(str(today - timedelta(days=i)), {}).get("h_trimp", 0) for i in range(7))
    heat_bonus_7d = round(h_trimp_7d - trimp_7d, 1)

    pv_benefit = min(10.0, score_now / ACCL_THRESHOLD_GOOD * 10)

    # Pace protection = how much of the heat pace penalty acclimation offsets.
    # Evidence: heat acclimation reliably improves performance IN THE HEAT by ~4-7%
    # and lowers cardiovascular strain (Periard 2015; Lorenzo 2010). We scale to a
    # conservative max ~4% "protection" (offsetting part of the 6-8% heat penalty),
    # NOT 7% — the larger figures and any cool-condition carry-over are contested
    # (Karlsen 2015 / Keiser 2015 found no cool-weather transfer once PV normalized).
    pv_perf    = round(pv_benefit * 0.4, 1)   # up to ~4% heat pace protection

    # Weekly history (8 weeks)
    history = []
    for w_offset in range(7, -1, -1):
        ws     = today - timedelta(days=today.weekday() + w_offset * 7)
        w_trimp = sum(daily.get(str(ws + timedelta(days=i)), {}).get("trimp",   0) for i in range(7))
        w_htrimp= sum(daily.get(str(ws + timedelta(days=i)), {}).get("h_trimp", 0) for i in range(7))
        w_wbgt  = BANGKOK_WBGT.get(ws.month, 25.0)
        w_bonus = round(w_htrimp - w_trimp, 1)
        history.append({
            "week_start": str(ws),
            "trimp": round(w_trimp, 1),
            "h_trimp": round(w_htrimp, 1),
            "bonus": w_bonus,
            "wbgt": w_wbgt,
            "is_current": w_offset == 0
        })

    days_to_race  = (FUJI_RACE_DATE - today).days

    # Hot Weather Model: 100% of adaptation is retained on race day since we stay in the heat!
    score_race_day  = score_now
    pv_race = min(10.0, score_race_day / ACCL_THRESHOLD_GOOD * 10)
    pv_race_perf = round(pv_race * 0.4, 1)  # heat pace protection (conservative)

    # Phase recommendations for Hot Weather Maintenance
    phases = []
    if days_to_race > 28:
        phases.append({
            "range": f"Now–{str(FUJI_RACE_DATE - timedelta(days=28))[:10]}",
            "desc": "Train normally — maintain heat exposure passively/actively",
            "status": "normal"
        })
        phases.append({
            "range": "Last 4 weeks in BKK",
            "desc": "Maintain 1-2 saunas/week (20min post-run) to protect stroke volume",
            "status": "sauna_active"
        })
    else:
        phases.append({
            "range": f"Now–{str(FUJI_RACE_DATE - timedelta(days=7))[:10]}",
            "desc": "Active heat maintenance — 1-2 saunas/week post-run",
            "status": "sauna_active"
        })

    phases.append({
        "range": f"{str(FUJI_RACE_DATE - timedelta(days=7))[:10]}–{FUJI_RACE_DATE}",
        "desc": "Taper & Maintain — light heat run, stay highly hydrated",
        "status": "taper"
    })
    phases.append({
        "range": str(FUJI_RACE_DATE),
        "desc": "Race day — Full heat pace protection active, lower cardiovascular strain",
        "status": "race"
    })

    return {
        "as_of": str(today),
        "score": score_now,
        "status": accl_status,
        "trimp_7d": round(trimp_7d, 1),
        "h_trimp_7d": round(h_trimp_7d, 1),
        "heat_bonus_7d": heat_bonus_7d,
        "pv_benefit": round(pv_benefit, 1),
        "pv_perf": pv_perf,
        "days_to_race": days_to_race,
        "last_bkk_day": str(FUJI_RACE_DATE),
        "days_to_last_bkk": days_to_race,
        "score_race_day": score_race_day,
        "pv_race": round(pv_race, 1),
        "pv_race_perf": pv_race_perf,
        "history": history,
        "phases": phases
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",      type=int, default=60)
    parser.add_argument("--race-plan", action="store_true",
                        help="Show Thailand Local Race transition timeline")
    args = parser.parse_args()

    r = analyze(args.days)

    if r["score"] >= ACCL_THRESHOLD_GOOD:
        status_emoji = "🟢"
    elif r["score"] >= ACCL_THRESHOLD_FAIR:
        status_emoji = "🟡"
    else:
        status_emoji = "🔴"

    print("=" * 65)
    print(f"🌡️  HEAT ACCLIMATION TRACKER — {r['as_of']}")
    print(f"   Bangkok training → Race Day ({FUJI_RACE_DATE})")
    print("=" * 65)
    print()

    print(f"📊 Current Status:")
    print(f"   Acclimation score (14d) : {r['score']}  {status_emoji} {r['status']}")
    print(f"   TRIMP 7d (plain)        : {r['trimp_7d']:.0f}")
    print(f"   TRIMP 7d (heat-adj)     : {r['h_trimp_7d']:.0f}  (+{r['heat_bonus_7d']:.0f} heat bonus)")
    print()

    print(f"💓 Cardiovascular Protection:")
    print(f"   Estimated PV expansion  : ~{r['pv_benefit']:.1f}%  (target ~8–10%)")
    print(f"   Heat pace protection    : ~+{r['pv_perf']:.1f}% in hot races (offsets part of 6–8% heat penalty)")
    print(f"      ℹ️  benefit ชัดเจนเฉพาะแข่งร้อน — cool race (Fuji ธ.ค.) carry-over ยังถกเถียง (~0–3%)")
    print()

    print(f"📅 Weekly Heat Load (last 8 weeks):")
    print(f"   {'สัปดาห์':<12} {'TRIMP':>7} {'Heat-TRIMP':>11} {'Bonus':>7}  WBGT")
    print(f"   {'─'*50}")
    
    # Display in reverse for correct chronology
    for item in reversed(r["history"]):
        cur_tag = " ◄ current" if item["is_current"] else ""
        print(f"   {item['week_start']:<12} {item['trimp']:>7.0f} {item['h_trimp']:>11.0f} {item['bonus']:>7.1f}  {item['wbgt']}°C{cur_tag}")

    print()

    if args.race_plan or True:   # always show transition plan
        print(f"🏃‍♂️ Thailand Local Race Acclimation Plan:")
        print(f"   Days to race          : {r['days_to_race']}d ({FUJI_RACE_DATE})")
        print(f"   Acclimation score now : {r['score']}")
        print(f"   Score on race day     : ~{r['score_race_day']:.0f}  (100% adaptation retained)")
        print()

        print(f"   Pace protection on race day : ~+{r['pv_race_perf']:.1f}% pace protection")
        print()

        # Phase recommendations
        print(f"   📋 Phase Recommendations:")
        for phase in r["phases"]:
            print(f"   {phase['range']:<21} : {phase['desc']}")
        print()

        print(f"   ⚠️  Key insight (Lorenzo 2010):")
        print(f"      Heat training protects against cardiovascular drift & sweat rate dehydration in a hot local race.")
        print(f"      This adaptation keeps heart rate stable and prevents severe performance decay under heat.")

    print("=" * 65)


if __name__ == "__main__":
    main()
