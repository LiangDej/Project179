"""
nutrition_calculator.py — Race nutrition plan from sweat science
Sources:
  Sweat rate : ACSM Position Stand (Sawka et al. 2007, Med Sci Sports Exerc 39:377)
  Sweat [Na+]: Shirreffs & Maughan 1997; trained athlete mid-range 40–55 mmol/L
  Replacement: ACSM 2016 — 40–80% of losses (avoid hyponatremia)
  Body-mass calibration mode: Montain et al. 2007 (J Athl Train 42:333)

Usage:
    python3 nutrition_calculator.py --race sponsor21 --temp 27 --humidity 82 --dew 21 --wind 1.5
    python3 nutrition_calculator.py --race sponsor21 --temp 27 --humidity 82 --duration 115
    python3 nutrition_calculator.py --calibrate --pre_weight 71.6 --post_weight 70.1 --fluid_ml 500 --duration 60
"""
import sys, os, argparse, math
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
_mcp = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "skills", "garmin_coach_mcp"))
if os.path.isdir(_mcp):
    sys.path.insert(0, _mcp)

from config import ATHLETE, NUTRITION as _NUT  # noqa: E402
WEIGHT_KG = ATHLETE.get("weight_kg")

# ---------------------------------------------------------------------------
# Athlete products (can override via --prevo-na / --gel-na)
# ---------------------------------------------------------------------------
# Na per product — SINGLE SOURCE: athlete.json → config.NUTRITION. No literal
# fallbacks (the old 650/90 were the wrong salt-compound reading — a silent
# fallback would overstate sodium 4×). Missing → main() asks for the values.
PREVO_NA_MG      = _NUT["prevo_na_mg"]
AMINOVITAL_NA    = _NUT["aminovital_na_mg"]
GEL_CARB_G       = _NUT["aminovital_carb_g"]
IN_RACE_SCHEDULE = _NUT.get("in_race_schedule")
SWEAT_NA_MMOL    = float(_NUT.get("sweat_na_mmol") or 45.0)
ELEC             = _NUT.get("electrolyte_name") or "Electrolyte cap"   # display names from athlete.json
GEL              = _NUT.get("gel_name") or "Gel"

# SINGLE SOURCE: races.json via race_registry (no hardcoded Fuji/stations).
def _build_race_config() -> dict:
    try:
        from race_registry import load_races
        out = {}
        for k, r in load_races().items():
            if not r.get("active", True):
                continue
            # rough default duration: marathon ~250min, HM ~115min, scale by dist
            goal = r.get("goal_min")
            dur = goal if goal else round(r["dist_km"] * 5.9)
            out[k] = {
                "name": f"{r['name']} {r['dist_km']:.0f}km",
                "dist_km": r["dist_km"],
                "stations": r.get("stations_km") or [6, 12, 18],
                "default_duration_min": dur,
            }
        return out
    except Exception:
        # Fallback only fires if race_registry itself is broken — must NOT
        # point at an archived race (old "atm") or the plan silently targets
        # a race that no longer exists.
        return {}

RACE_CONFIG = _build_race_config()

# ---------------------------------------------------------------------------
# Sweat rate model — ACSM / Sawka 2007
# Base: 1.0–1.2 L/hr at 75-80% VO2max, mild conditions
# Heat adjustment: +0.035 L/hr per °C above 20°C (Sawka linear approx)
# Humidity factor: ×1.15 when RH > 70% (evaporative cooling impaired)
# ---------------------------------------------------------------------------
def sweat_rate_lhr(temp_c: float, humidity_pct: float) -> tuple[float, float, float]:
    base       = 1.1                                    # L/hr — trained male runner race intensity
    heat_adj   = max(0.0, (temp_c - 20.0) * 0.035)
    humid_fac  = 1.15 if humidity_pct > 70 else 1.0
    sr         = (base + heat_adj) * humid_fac
    sr_low     = (0.9 + heat_adj * 0.8) * humid_fac
    sr_high    = (1.3 + heat_adj * 1.2) * humid_fac
    return round(sr, 2), round(sr_low, 2), round(sr_high, 2)


# ---------------------------------------------------------------------------
# Sweat sodium concentration — Shirreffs & Maughan 1997
# Trained, heat-adapted athlete: 40–55 mmol/L. Personal value: athlete.json →
# sweat_na_mmol (45 = mid-range default until a sweat-patch test). 23 mg/mmol.
# ---------------------------------------------------------------------------
def sweat_na_mgl(na_mmol: float | None = None) -> float:
    return (na_mmol if na_mmol is not None else SWEAT_NA_MMOL) * 23.0   # mg/L


# ---------------------------------------------------------------------------
# Calibration mode — Montain et al. 2007
# SweatRate (L/hr) = (BM_loss_kg + fluid_intake_L) / duration_hr
# ---------------------------------------------------------------------------
def calibrate_sweat_rate(pre_kg: float, post_kg: float,
                         fluid_ml: float, duration_min: float) -> float:
    bm_loss   = pre_kg - post_kg           # kg ≈ L
    fluid_L   = fluid_ml / 1000
    dur_hr    = duration_min / 60
    return round((bm_loss + fluid_L) / dur_hr, 2)


# ---------------------------------------------------------------------------
# Na requirement
# ---------------------------------------------------------------------------
def na_requirement(sweat_lhr: float, duration_min: float,
                   replace_pct_low: float = 0.40,
                   replace_pct_high: float = 0.80) -> tuple[float, float]:
    total_sweat = sweat_lhr * duration_min / 60
    na_loss     = total_sweat * sweat_na_mgl()
    return round(na_loss * replace_pct_low), round(na_loss * replace_pct_high)


# ---------------------------------------------------------------------------
# Build nutrition plan
# ---------------------------------------------------------------------------
def plan_caps(na_loss_mg: float, na_min: float, na_max: float,
              n_stations: int, prevo_pre=None) -> dict:
    """Solve for a Prevo-cap plan that lands inside the ACSM 40–80% band.

    Gels are treated as fixed (taken for energy at every station; each adds
    AMINOVITAL_NA mg). We pick the TOTAL number of caps that puts the plan
    nearest the 60% midpoint, then clamp so it never exceeds the 80% ceiling
    (GI distress / needless excess) nor drops below the 40% floor when feasible.
    Caps are then allocated: a pre-race preload + remainder spread across
    stations (0 allowed — no forced minimum). This is the fix for the old
    `max(1, …)` bug that forced ≥1 cap/station and blew past 80%.
    """
    gel_na_total = n_stations * AMINOVITAL_NA          # fixed (energy gels)
    na_target    = (na_min + na_max) / 2               # 60% midpoint

    def plan_total(c):
        return c * PREVO_NA_MG + gel_na_total

    caps_total = max(0, round((na_target - gel_na_total) / PREVO_NA_MG))
    # Clamp under the 80% ceiling
    while caps_total > 0 and plan_total(caps_total) > na_max:
        caps_total -= 1
    # Raise toward the 40% floor only while still under the ceiling
    while plan_total(caps_total + 1) <= na_max and plan_total(caps_total) < na_min:
        caps_total += 1

    # Allocate caps: preload + even spread across stations (no forced minimum)
    if prevo_pre is not None:
        pre_caps = min(prevo_pre, caps_total)
    else:
        pre_caps = 1 if caps_total >= 1 else 0
    remaining = caps_total - pre_caps
    station_caps = [remaining // n_stations] * n_stations
    for i in range(remaining % n_stations):
        station_caps[i] += 1

    plan_na = plan_total(caps_total)
    return {
        "caps_total":   caps_total,
        "pre_caps":     pre_caps,
        "station_caps": station_caps,
        "pre_na":       pre_caps * PREVO_NA_MG,
        "gel_na_total": gel_na_total,
        "plan_na":      plan_na,
        "replace_pct":  round(plan_na / na_loss_mg * 100, 1) if na_loss_mg else 0,
    }


def schedule_plan(dist_km: float, na_loss_mg: float, sched: dict) -> dict:
    """Fixed-interval plan from the athlete's own dosing rule (v1.7.1).

    The station solver above bundles every cap into the water stops (2–3
    Prevo at once at km 6/12/18) — that contradicts the athlete's rule of ONE
    Prevo every 4 km + one gel every 6 km, which is what actually worked in
    training. Here the rule is the plan; the tool only places the doses,
    sums the sodium/carbs, and VERIFIES the total against ACSM 40–80%.
    """
    p_every = sched.get("prevo_every_km") or 4
    g_every = sched.get("gel_every_km") or 6
    max_per = sched.get("max_prevo_per_stop", 1)
    skip    = sched.get("skip_last_km", 2)
    last_ok = dist_km - skip

    def marks(every):
        out, k = [], every
        while k <= last_ok + 1e-9:
            out.append(k)
            k += every
        return out

    prevo_km, gel_km = marks(p_every), marks(g_every)
    stops = []
    for km in sorted(set(prevo_km) | set(gel_km)):
        stops.append({"km": km,
                      "prevo": min(max_per, 1) if km in prevo_km else 0,
                      "gel": 1 if km in gel_km else 0})
    pre_prevo = sched.get("pre_prevo", 0)
    pre_gel   = sched.get("pre_gel", 0)
    n_prevo   = pre_prevo + sum(x["prevo"] for x in stops)
    n_gel     = pre_gel + sum(x["gel"] for x in stops)
    plan_na   = n_prevo * PREVO_NA_MG + n_gel * AMINOVITAL_NA
    return {
        "stops":       stops,
        "pre_prevo":   pre_prevo,
        "pre_gel":     pre_gel,
        "n_prevo":     n_prevo,
        "n_gel":       n_gel,
        "in_race_gels": sum(x["gel"] for x in stops),
        "max_prevo_at_once": max([x["prevo"] for x in stops] + [pre_prevo]),
        "plan_na":     plan_na,
        "replace_pct": round(plan_na / na_loss_mg * 100, 1) if na_loss_mg else 0,
    }


def build_schedule_plan(race_key: str, temp_c: float, humidity_pct: float,
                        duration_min: float, sched: dict) -> dict:
    rc = RACE_CONFIG[race_key]
    sr, sr_low, sr_high = sweat_rate_lhr(temp_c, humidity_pct)
    total_sweat_l       = sr * duration_min / 60
    na_loss_mg          = total_sweat_l * sweat_na_mgl()
    na_min, na_max      = na_requirement(sr, duration_min)
    p = schedule_plan(rc["dist_km"], na_loss_mg, sched)

    print("=" * 60)
    print(f"💊 NUTRITION CALCULATOR — {rc['name']}  (โหมด: กฎของนักวิ่ง)")
    print(f"   {date.today()}  |  {temp_c}°C / {humidity_pct}%RH  |  Est. {duration_min:.0f} min")
    print("=" * 60)
    print(f"💦 Sweat: {sr} L/hr (range {sr_low}–{sr_high}) → {round(total_sweat_l, 1)} L, "
          f"Na loss {round(na_loss_mg)} mg")
    print(f"🎯 ACSM 40–80%: {na_min}–{na_max} mg")
    print()
    print(f"📋 กฎ: {ELEC} 1 เม็ดทุก {sched.get('prevo_every_km')} km + {GEL} 1 ซองทุก "
          f"{sched.get('gel_every_km')} km  (ไม่กิน {ELEC} พร้อมกันเกิน "
          f"{sched.get('max_prevo_per_stop', 1)} เม็ด, งด {sched.get('skip_last_km', 2)} km สุดท้าย)")
    pre = []
    if p["pre_gel"]:
        pre.append(f"{GEL} {p['pre_gel']}")
    if p["pre_prevo"]:
        pre.append(f"{ELEC} {p['pre_prevo']}")
    print(f"   T-30min : {' + '.join(pre) if pre else '—'}")
    print(f"   {'km':<6} {ELEC:>6} {GEL:>5} {'Na':>7}")
    print("   " + "-" * 28)
    for x in p["stops"]:
        na = x["prevo"] * PREVO_NA_MG + x["gel"] * AMINOVITAL_NA
        print(f"   {x['km']:<6g} {(str(x['prevo']) if x['prevo'] else '—'):>6} "
              f"{(str(x['gel']) if x['gel'] else '—'):>5} {na:>5} mg")
    print()
    hrs = duration_min / 60
    carb_hr = round(p["in_race_gels"] * GEL_CARB_G / hrs) if hrs else 0
    print(f"   รวม: {ELEC} {p['n_prevo']} + {GEL} {p['n_gel']} = {p['plan_na']} mg Na "
          f"= {p['replace_pct']}% of loss")
    print(f"   คาร์บระหว่างวิ่ง: {p['in_race_gels']} ซอง × {GEL_CARB_G} g ≈ {carb_hr} g/hr")
    print()
    print("📊 Verdict:")
    if p["replace_pct"] < 40:
        ok_short = duration_min <= 120
        print(f"   🟡 {p['replace_pct']}% — ต่ำกว่า 40% ACSM. "
              + ("ใช้เวลา ≤2 ชม. รับได้ถ้าไม่ใช่ salty sweater"
                 if ok_short else f"เกิน 2 ชม. ({duration_min:.0f} min) — ขาด Na สะสมมากขึ้น")
              + f" — ถ้าเคยตะคริว/เหงื่อเป็นคราบขาว ลดช่วง {ELEC} เป็นทุก 3 km (ยังทีละ 1 เม็ด)")
    elif p["replace_pct"] > 80:
        print(f"   ⚠️  {p['replace_pct']}% — เกิน 80%: ยืดช่วง {ELEC} ออก (เสี่ยง GI)")
    else:
        print(f"   ✅ {p['replace_pct']}% — อยู่ใน ACSM zone (40–80%)")
    if p["max_prevo_at_once"] > 1:
        print(f"   ⚠️  มีจุดที่ {ELEC} >1 เม็ดพร้อมกัน — ขัดกฎ")
    print("   💧 น้ำเปล่าเป็นหลัก ดื่มตามกระหาย (hyponatremia มาจากดื่มน้ำเกิน ไม่ใช่ Na)")
    print("=" * 60)
    p.update({"sweat_lhr": sr, "na_loss_mg": round(na_loss_mg),
              "na_min": na_min, "na_max": na_max, "carb_g_hr": carb_hr})
    return p


def build_plan(race_key: str, temp_c: float, humidity_pct: float,
               dew_c: float, duration_min: float,
               prevo_pre=None) -> None:
    rc = RACE_CONFIG[race_key]
    sr, sr_low, sr_high = sweat_rate_lhr(temp_c, humidity_pct)
    total_sweat_l       = sr * duration_min / 60
    na_loss_mg          = total_sweat_l * sweat_na_mgl()
    na_min, na_max      = na_requirement(sr, duration_min)

    n_stations = len(rc["stations"])
    p = plan_caps(na_loss_mg, na_min, na_max, n_stations, prevo_pre)
    pre_na       = p["pre_na"]
    station_caps = p["station_caps"]
    plan_na      = p["plan_na"]
    replace_pct  = p["replace_pct"]

    print("=" * 60)
    print(f"💊 NUTRITION CALCULATOR — {rc['name']}")
    print(f"   {date.today()}  |  {temp_c}°C / {humidity_pct}%RH  |  Est. {duration_min} min")
    print("=" * 60)
    print()
    print(f"💦 Sweat Model (ACSM / Sawka 2007):")
    print(f"   Rate      : {sr} L/hr  (range {sr_low}–{sr_high})")
    print(f"   Total     : {round(total_sweat_l, 1)} L over {duration_min} min")
    print(f"   Na loss   : {round(na_loss_mg)} mg  (@ {SWEAT_NA_MMOL:g} mmol/L · 23 mg/mmol)")
    print()
    print(f"🎯 ACSM Replacement Target (40–80% of loss):")
    print(f"   Min (40%) : {na_min} mg")
    print(f"   Max (80%) : {na_max} mg")
    print()
    print(f"📋 Nutrition Plan:")
    pre_str = f"{ELEC} {p['pre_caps']} แคป = {pre_na} mg Na" if p["pre_caps"] else "— (ไม่ต้อง preload)"
    print(f"   Pre-race  : {pre_str}")
    print(f"   Stations  : {rc['stations']} km  ({n_stations} stops)")
    print()

    print(f"   {'km':<8} {ELEC:>7} {'Na':>8}  {GEL}")
    print("   " + "-" * 38)
    for idx, km in enumerate(rc["stations"]):
        c = station_caps[idx]
        na = c * PREVO_NA_MG + AMINOVITAL_NA
        cap_str = f"{c} แคป" if c else "—"
        print(f"   km {km:<4}  {cap_str:>7}  {na:>5} mg  + {GEL}")

    print()
    print(f"   Plan total: {plan_na} mg Na  ({p['caps_total']} caps + {n_stations} gels)")
    print(f"   = {replace_pct}% of loss")

    # Verdict — plan is clamped into band, so this just reports & explains
    print()
    print(f"📊 Verdict:")
    if replace_pct < 40:
        verdict = (f"🟡 {replace_pct}% — ต่ำกว่า 40% (gels เพียว Na ไม่พอ "
                   f"แต่ HM สั้น ยอมรับได้ถ้าไม่ใช่ salty sweater)")
    elif replace_pct > 80:
        verdict = (f"⚠️  {replace_pct}% — เกิน 80%: เสี่ยง GI distress + กระหายน้ำ "
                   f"(ไม่ใช่ hyponatremia — นั่นเกิดจากดื่มน้ำมากเกิน)")
    else:
        verdict = f"✅ {replace_pct}% — อยู่ใน ACSM zone (40–80%) พอดี"
    print(f"   {verdict}")

    # True hyponatremia driver = over-drinking water, not sodium
    print(f"   💧 Hyponatremia ป้องกันโดย: ดื่มน้ำตามกระหาย ไม่ over-drink "
          f"(Na ที่กินช่วยป้องกัน ไม่ใช่สาเหตุ)")
    if humidity_pct > 80:
        print(f"   ⚠️  Humidity {humidity_pct}% — เหงื่อระเหยยาก ระวัง overheating > Na")

    print()
    print(f"💡 ถ้าต้องการ calibrate sweat rate จริง:")
    print(f"   ชั่งน้ำหนัก pre/post run แล้วรัน:")
    print(f"   python3 nutrition_calculator.py --calibrate \\")
    print(f"     --pre_weight [kg] --post_weight [kg] --fluid_ml [ml] --duration [min]")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _require_products():
    missing = [k for k, v in (("prevo_na_mg", PREVO_NA_MG), ("aminovital_na_mg", AMINOVITAL_NA),
                              ("aminovital_carb_g", GEL_CARB_G)) if v is None]
    if missing:
        print(f"❌ ใส่ค่าสินค้าใน athlete.json → nutrition_products: {', '.join(missing)} (ดูฉลาก)")
        sys.exit(1)


def main():
    _require_products()
    parser = argparse.ArgumentParser(description="Race nutrition calculator")
    _rc = list(RACE_CONFIG.keys())
    parser.add_argument("--race", default=(_rc[0] if _rc else None), choices=_rc)
    parser.add_argument("--temp",       type=float, default=27.0)
    parser.add_argument("--humidity",   type=float, default=82.0)
    parser.add_argument("--dew",        type=float, default=21.0)
    parser.add_argument("--duration",   type=float, default=None,
                        help="Estimated race duration (min) — defaults to race preset")
    parser.add_argument("--mode", choices=["auto", "schedule", "stations"], default="auto",
                        help="schedule = กฎของนักวิ่ง (athlete.json in_race_schedule); "
                             "stations = solver แบ่งเม็ดตามจุดน้ำ; auto = schedule ถ้ามีกฎ")
    parser.add_argument("--prevo-pre",  type=int,   default=None,
                        help="Force pre-race electrolyte caps (default: auto — solver picks)")
    # Calibration mode
    parser.add_argument("--calibrate",      action="store_true")
    parser.add_argument("--pre_weight",  type=float, default=None)
    parser.add_argument("--post_weight", type=float, default=None)
    parser.add_argument("--fluid_ml",    type=float, default=0)
    args = parser.parse_args()

    if args.calibrate:
        if args.pre_weight is None or args.post_weight is None or args.duration is None:
            print("❌ --calibrate ต้องการ --pre_weight / --post_weight / --duration")
            sys.exit(1)
        sr = calibrate_sweat_rate(args.pre_weight, args.post_weight,
                                  args.fluid_ml, args.duration)
        print(f"🏃 Calibrated sweat rate: {sr} L/hr  (Montain et al. 2007)")
        print(f"   บันทึกค่านี้ไว้เปรียบเทียบกับ model estimate ใน build_plan()")
        return

    duration = args.duration or RACE_CONFIG[args.race]["default_duration_min"]
    mode = args.mode
    if mode == "auto":
        mode = "schedule" if IN_RACE_SCHEDULE else "stations"
    if mode == "schedule":
        if not IN_RACE_SCHEDULE:
            print("❌ ไม่มี race_day_nutrition.in_race_schedule ใน athlete.json — ใช้ --mode stations")
            sys.exit(1)
        build_schedule_plan(args.race, args.temp, args.humidity, duration, IN_RACE_SCHEDULE)
    else:
        build_plan(args.race, args.temp, args.humidity, args.dew, duration, args.prevo_pre)


if __name__ == "__main__":
    main()
