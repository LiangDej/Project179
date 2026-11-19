#!/usr/bin/env python3
"""
test_suite.py — PROJECT 179 self-contained test runner (no pytest needed).

Prioritized Test Architecture:
  1. UNIT P0 — Daily & Session Core (PMC Banister math, ACWR injury thresholds, speed classification)
  2. UNIT P1 — Core Physiology & VDOT Math (Daniels limits, round-trip inversion, Karvonen dynamics, treadmill)
  3. UNIT P2 — Macro & Weekly Planning (Prescriptions, 80/20 rule, periodization progression)
  4. UNIT P3 — Race Tactics & Nutrition (ACSM sodium solver, post-race heat guards, taper windows)
  5. UNIT P4 — Environmental & Registry (Ely penalty, WBGT TRIMP, climate tables, race loader)
  6. CONSISTENCY — Single-source invariants & static source scans
  7. FUNCTIONAL  — Real tools end-to-end (CLI regression suite)

Run:  .venv/bin/python3.13 GarminRawData/tests/test_suite.py
Exit code 0 = all pass, 1 = any fail.
"""
import sys
import os
import re
import math
import json
import subprocess
from datetime import date, timedelta, datetime
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent.parent          # AntiGravity/
TOOLS     = ROOT / "GarminRawData" / "tools"
COACH_MCP = ROOT / "skills" / "garmin_coach_mcp"
PYBIN     = ROOT / ".venv" / "bin" / "python3.13"

sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(COACH_MCP))

# ---------------------------------------------------------------------------
# Tiny test harness
# ---------------------------------------------------------------------------
_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    mark = "✅" if cond else "❌"
    line = f"  {mark} {name}"
    if not cond and detail:
        line += f"\n       └─ {detail}"
    print(line)


def run_tool(args, timeout=90):
    """Run a tool via subprocess with PYTHONPATH set. Returns (rc, stdout)."""
    env = {**os.environ, "PYTHONPATH": f"{COACH_MCP}:{TOOLS}"}
    try:
        p = subprocess.run([str(PYBIN), *[str(a) for a in args]],
                           cwd=str(TOOLS), env=env, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"


# ===========================================================================
# 1. UNIT TESTS — PRIORITY 0: DAILY & SESSION CORE (Runs Daily/Post-Run)
# ===========================================================================
def unit_p0_daily_and_session():
    print("\n── 1. UNIT P0: DAILY & SESSION CORE ─────────────────────")
    import training_load as tl
    import injury_risk_detector as ird

    # PMC / Banister Impulse-Response Math
    check("training_load: K_CTL derived from tau=42", abs(tl.K_CTL - (1 - math.exp(-1 / 42))) < 1e-6)
    check("training_load: K_ATL derived from tau=7", abs(tl.K_ATL - (1 - math.exp(-1 / 7))) < 1e-6)

    # Impulse-response behavior on rest days
    pmc = tl.calc_pmc({"2026-01-01": 100.0}, date(2026, 1, 1), date(2026, 1, 5))
    check("training_load: Day 1 TSB == CTL - ATL", abs(pmc[0][4] - (pmc[0][2] - pmc[0][3])) < 0.2)
    check("training_load: Day 2 rest day CTL decays", pmc[1][2] < pmc[0][2])
    check("training_load: Day 2 rest day ATL decays", pmc[1][3] < pmc[0][3])
    check("training_load: ATL decays faster than CTL on rest days",
          (pmc[0][3] - pmc[1][3]) > (pmc[0][2] - pmc[1][2]))
    check("training_load: TSB rises on consecutive rest days (recovery)", pmc[2][4] > pmc[1][4])

    # HR-TSS formula boundaries
    check("training_load: calc_hr_tss is 0 at RHR",
          tl.calc_hr_tss({"averageHR": tl.RHR, "duration": 3600}) == 0.0)
    check("training_load: calc_hr_tss is 0 when duration is 0",
          tl.calc_hr_tss({"averageHR": tl.RHR + 20, "duration": 0}) == 0.0)
    check("training_load: calc_hr_tss below RHR is 0",
          tl.calc_hr_tss({"averageHR": tl.RHR - 5, "duration": 3600}) == 0.0)
    check("training_load: calc_hr_tss at T_HR for 1h == 100 hrTSS",
          abs(tl.calc_hr_tss({"averageHR": tl.T_HR, "duration": 3600}) - 100.0) < 0.5)

    # Injury Risk & ACWR (Gabbett 2016)
    check("injury_risk: ACWR_CAUTION == 1.3 (upper sweet spot)", ird.ACWR_CAUTION == 1.3)
    check("injury_risk: ACWR_DANGER == 1.5 (danger zone)", ird.ACWR_DANGER == 1.5)
    check("injury_risk: MAX_RUN_STREAK == 4 days", ird.MAX_RUN_STREAK == 4)
    check("injury_risk: RISK_LEVELS contains 4 tiers", len(ird.RISK_LEVELS) == 4)

    # Speed-to-pace inversion precision
    check("post_session: speed to pace inversion is exact", round(3600 / (3600 / 12.0), 2) == 12.0)


# ===========================================================================
# 2. UNIT TESTS — PRIORITY 1: CORE PHYSIOLOGY & VDOT MATH
# ===========================================================================
def unit_p1_physiology_and_vdot():
    print("\n── 2. UNIT P1: CORE PHYSIOLOGY & VDOT MATH ──────────────")
    import config
    import vdot_math as vm
    import treadmill_pace_model as tpm

    # config derives correctly from athlete.json
    z = config.HR_ZONE_BOUNDS
    check("config: T-ceiling == LTHR",
          z["Z3_T"][1] == config.ATHLETE["lthr"],
          f"T-ceiling {z['Z3_T'][1]} vs LTHR {config.ATHLETE['lthr']}")
    check("config: HR zones contiguous & monotonic",
          all(z[a][1] == z[b][0] for a, b in
              [("Z1_E", "Z2_M"), ("Z2_M", "Z3_T"), ("Z3_T", "Z4_I"), ("Z4_I", "Z5_R")]))
    check("config: hrr == mhr - rhr",
          config.ATHLETE["hrr"] == config.ATHLETE["mhr"] - config.ATHLETE["rhr"])
    check("config: VDOT_PACES monotonic (E slower than R)",
          config.VDOT_PACES["E"][0] > config.VDOT_PACES["R"][1])

    # Dynamic LTHR auto-derivation consistency across synthetic profiles
    hrr1 = 185 - 55
    t_pct1 = (170 - 55) / hrr1
    check("config: synthetic runner 1 LTHR 170 consistent", 55 + int(hrr1 * t_pct1) == 170)
    hrr2 = 165 - 60
    t_pct2 = (150 - 60) / hrr2
    check("config: synthetic runner 2 LTHR 150 consistent", 60 + int(hrr2 * t_pct2) == 150)

    # vdot_math — Boundary inputs
    check("vdot_math: compute_vdot rejects dist < 1000m", vm.compute_vdot(800, 2.5) is None)
    check("vdot_math: compute_vdot rejects duration == 0", vm.compute_vdot(5000, 0) is None)
    check("vdot_math: compute_vdot rejects duration < 0", vm.compute_vdot(5000, -10) is None)

    # vdot_math — Daniels table benchmarks
    hm = vm.predict_race_time(40, 21097.5)
    fm = vm.predict_race_time(40, 42195)
    check("vdot_math: VDOT40 HM ~1:50 (109-112min)", 109 <= hm <= 112, f"HM={hm:.1f}min")
    check("vdot_math: VDOT40 FM ~3:49 (227-232min)", 227 <= fm <= 232, f"FM={fm:.1f}min")

    # Elite vs Beginner convergence
    fm75 = vm.predict_race_time(75, 42195)
    check("vdot_math: elite VDOT 75 marathon ~2:14-2:16", 133 <= fm75 <= 137, f"got {fm75}")
    fm30 = vm.predict_race_time(30, 42195)
    check("vdot_math: beginner VDOT 30 marathon ~4:40-5:05", 280 <= fm30 <= 305, f"got {fm30}")

    # Round-trip inversion accuracy across standard distances
    for dist in (5000, 10000, 21097.5, 42195):
        t_pred = vm.predict_race_time(40, dist)
        v_calc = vm.compute_vdot(dist, t_pred)
        check(f"vdot_math: round-trip inversion accurate for {dist}m", abs(v_calc - 40) < 0.2)

    # Monotonicity across distances
    p5 = vm.predict_race_time(40, 5000) / 5.0
    p10 = vm.predict_race_time(40, 10000) / 10.0
    phm = vm.predict_race_time(40, 21097.5) / 21.0975
    pfm = vm.predict_race_time(40, 42195) / 42.195
    check("vdot_math: standard race distances monotonic (5K < 10K < HM < FM)",
          p5 < p10 < phm < pfm, f"paces={[p5, p10, phm, pfm]}")

    # VO2max intensity hierarchy
    pe = vm.vdot_to_pace_sec(40, 0.70)
    pm = vm.vdot_to_pace_sec(40, 0.83)
    pt = vm.vdot_to_pace_sec(40, 0.88)
    pi = vm.vdot_to_pace_sec(40, 1.00)
    pr = vm.vdot_to_pace_sec(40, 1.05)
    check("vdot_math: VO2max intensities hierarchy E > M > T > I > R",
          pe > pm > pt > pi > pr, f"paces={[pe, pm, pt, pi, pr]}")

    # Treadmill Pace Model
    paces = [tpm.infer_pace_from_hr(h) for h in (150, 164, 175, 183)]
    secs  = [int(p.split(":")[0]) * 60 + int(p.split(":")[1].replace("/km", "")) for p in paces]
    check("treadmill_pace_model: HR↑ → pace faster (monotonic)",
          all(secs[i] > secs[i + 1] for i in range(len(secs) - 1)), f"secs={secs}")
    check("treadmill_pace_model: infer_pace handles RHR floor safely",
          tpm.infer_pace_from_hr(40) is not None)
    check("treadmill_pace_model: infer_pace handles MHR ceiling safely",
          tpm.infer_pace_from_hr(205) is not None)


# ===========================================================================
# 3. UNIT TESTS — PRIORITY 2: MACRO & WEEKLY PLANNING
# ===========================================================================
def unit_p2_macro_and_weekly():
    print("\n── 3. UNIT P2: MACRO & WEEKLY PLANNING ──────────────────")
    import session_prescriber as sp
    import training_planner as tp

    # Session Prescriber structure & rules
    plan = sp.generate_week_plan(week="current", bb=70, hrv="balanced", pain="none",
                                verbose=False, target_monday=date(2026, 8, 31))
    check("session_prescriber: weekly plan has 7 days", len(plan["sessions"]) == 7)
    qual_count = sum(1 for s in plan["sessions"]
                     if s.get("type") in ("quality", "quality1", "quality2", "T", "I", "R"))
    check("session_prescriber: quality sessions <= 2 per week (80/20 rule)", qual_count <= 2)
    check("session_prescriber: monday is strength day",
          plan["sessions"][0]["weekday"] == "จันทร์" and plan["sessions"][0]["type"] == "strength")
    check("session_prescriber: friday is mandatory rest",
          plan["sessions"][4]["weekday"] == "ศุกร์" and plan["sessions"][4]["type"] == "rest")
    check("session_prescriber: sunday is long run",
          plan["sessions"][6]["weekday"] == "อาทิตย์" and plan["sessions"][6]["type"] == "long")

    # Readiness override: low BB forces quality to easy
    plan_low = sp.generate_week_plan(week="current", bb=30, hrv="unbalanced", pain="none",
                                    verbose=False, target_monday=date(2026, 8, 31))
    check("session_prescriber: low BB modifies quality to easy",
          all(s["decision"] == "MODIFY" and "Easy" in s["workout"]
              for s in plan_low["sessions"] if s["type"] in ("quality1", "quality2")))

    # Training Planner math (pure functions)
    check("training_planner: target_peak_km stretches above historical peak",
          tp._target_peak_km(current_weekly_km=40, historical_peak_km=50) == 55)
    check("training_planner: target_peak_km never below current+5 (mid-buildup athlete)",
          tp._target_peak_km(current_weekly_km=68, historical_peak_km=50) >= 73)
    check("training_planner: taper_km strictly decreasing toward race day",
          all(a > b for a, b in zip([tp._taper_km(w, 70) for w in [4, 3, 2, 1, 0]][:-1],
                                    [tp._taper_km(w, 70) for w in [4, 3, 2, 1, 0]][1:])))
    check("training_planner: taper_km race day == 0", tp._taper_km(0, 70) == 0)

    phase_km = tp._build_phase_km(45, 70)
    check("training_planner: phase_km progression base < quality < race_specific",
          phase_km["base"]["max"] < phase_km["quality"]["max"] < phase_km["race_specific"]["max"])


# ===========================================================================
# 4. UNIT TESTS — PRIORITY 3: RACE TACTICS & NUTRITION
# ===========================================================================
def unit_p3_race_and_nutrition():
    print("\n── 4. UNIT P3: RACE TACTICS & NUTRITION ─────────────────")
    import nutrition_calculator as nc
    import post_race_updater as pru
    import taper_monitor as tm

    # Sweat rate model (Sawka et al. 2007)
    sr20, *_ = nc.sweat_rate_lhr(20, 70)
    sr28, *_ = nc.sweat_rate_lhr(28, 70)
    sr35, *_ = nc.sweat_rate_lhr(35, 70)
    check("nutrition: sweat rate increases with temperature (20°C < 28°C < 35°C)",
          sr20 < sr28 < sr35)

    srh50, *_ = nc.sweat_rate_lhr(28, 50)
    srh85, *_ = nc.sweat_rate_lhr(28, 85)
    check("nutrition: sweat rate increases with high humidity (>70% RH)", srh50 < srh85)

    # Montain 2007 sweat rate calibration equation
    cal = nc.calibrate_sweat_rate(72.0, 70.5, 500, 60)
    check("nutrition: calibrate sweat rate matches Montain 2007", cal == 2.0, f"got {cal}")

    # ACSM replacement target solver
    loss = 1.55 * 4 * nc.sweat_na_mgl()
    req_min, req_max = nc.na_requirement(1.55, 240)
    caps_plan = nc.plan_caps(loss, req_min, req_max, 6)
    check("nutrition: marathon plan strictly within 40–80% ACSM",
          40 <= caps_plan["replace_pct"] <= 80, f"got {caps_plan['replace_pct']}%")
    check("nutrition: all station caps non-negative integers",
          all(c >= 0 for c in caps_plan["station_caps"]))

    # Post-Race Updater parsing & heat guard
    check("post_race_updater: parse_time H:MM:SS format", pru.parse_time("1:45:00") == 6300)
    check("post_race_updater: parse_time MM:SS format", pru.parse_time("45:00") == 2700)
    check("post_race_updater: HOT_THRESHOLD_C == 20.0°C", pru.HOT_THRESHOLD_C == 20.0)
    check("post_race_updater: heat correction 0 at <= 13°C", pru.ely_heat_correction(10.0) == 0.0)
    check("post_race_updater: heat correction at 25°C is 4.8%",
          abs(pru.ely_heat_correction(25.0) - 0.048) < 1e-4)
    check("post_race_updater: cool-equiv duration strictly faster than raw time",
          pru.heat_adjusted_duration(3600, 25.0) < 3600)

    # Taper Monitor window ordering
    check("taper_monitor: window thresholds strictly ordered (far > early > late > final)",
          tm.TAPER_VOLUME["far"]["days_min"] > tm.TAPER_VOLUME["early"]["days_min"]
          > tm.TAPER_VOLUME["late"]["days_min"] > tm.TAPER_VOLUME["final"]["days_min"])


# ===========================================================================
# 5. UNIT TESTS — PRIORITY 4: ENVIRONMENTAL & REGISTRY
# ===========================================================================
def unit_p4_environmental_and_registry():
    print("\n── 5. UNIT P4: ENVIRONMENTAL & REGISTRY ─────────────────")
    import bangkok_climate as bc
    import weather_adjuster as wa
    import heat_acclimation as ha
    import race_registry as rr

    # Bangkok climate & Ely heat correction
    temps = [bc.morning_temp_c(m) for m in range(1, 13)]
    check("bangkok_climate: all monthly morning temps 22-32°C",
          all(22 <= t <= 32 for t in temps), f"{temps}")
    check("bangkok_climate: Ely penalty 0 at <= 13°C", bc.ely_penalty_fraction(13) == 0.0)
    check("bangkok_climate: 20°C penalty == 2.8%", abs(bc.ely_penalty_fraction(20) - 0.028) < 1e-4)
    check("bangkok_climate: 33°C penalty == 8.0%", abs(bc.ely_penalty_fraction(33) - 0.080) < 1e-4)

    # Weather adjuster pacing helpers
    check("weather_adjuster: parse_pace 5:20 to seconds", wa.parse_pace("5:20") == 320.0)
    check("weather_adjuster: fmt_pace 320s to string", wa.fmt_pace(320.0) == "5:20/km")

    # Heat acclimation TRIMP & WBGT weighting
    check("heat_acclimation: WBGT < 21 has multiplier 1.0", ha.wbgt_multiplier(20.0) == 1.0)
    check("heat_acclimation: WBGT >= 24 has multiplier > 1.0", ha.wbgt_multiplier(25.0) > 1.0)
    check("heat_acclimation: TRIMP at RHR is zero", ha.calc_trimp(ha.RHR, 3600) == 0.0)
    check("heat_acclimation: TRIMP at MHR is positive", ha.calc_trimp(ha.MHR, 3600) > 0.0)
    check("heat_acclimation: TRIMP above MHR clamped to max ratio",
          ha.calc_trimp(ha.MHR + 10, 3600) == ha.calc_trimp(ha.MHR, 3600))

    # Race registry invariants
    races = rr.load_races()
    check("race_registry: loads valid races dictionary", isinstance(races, dict) and len(races) >= 1)
    check("race_registry: active race key is valid and active",
          rr.active_race_key() in races and races[rr.active_race_key()].get("active", True))


# ===========================================================================
# 6. CONSISTENCY TESTS — Single-Source Invariants & Static Source Scan
# ===========================================================================
def consistency_tests():
    print("\n── 6. CONSISTENCY (single-source invariants) ────────────")
    import config
    import race_registry as rr
    import vdot_math as vm
    import race_pace_planner as rpp
    import nutrition_calculator as nc
    import post_session_analyzer as psa
    import injury_risk_detector as ird

    # Race pace planner vs vdot_math
    worst = max(abs(rpp._vdot_to_race_pace(40, d) - vm.predict_race_time(40, d * 1000) * 60 / d)
                for d in (5, 10, 21.097, 42.195))
    check("race_pace_planner == vdot_math (no rolled-own %VO2max)",
          worst < 2, f"max divergence {worst:.1f} sec/km")

    # Nutrition plan ACSM 80% ceiling
    over = []
    for t in (15, 20, 25, 29, 33, 38):
        sr, *_ = nc.sweat_rate_lhr(t, 82)
        loss = sr * 115 / 60 * nc.sweat_na_mgl()
        lo, hi = nc.na_requirement(sr, 115)
        p = nc.plan_caps(loss, lo, hi, 3)
        if p["plan_na"] > hi + 1:
            over.append((t, p["replace_pct"]))
    check("nutrition: plan never exceeds 80% ceiling (all temps)", not over, f"violations={over}")

    # Dynamic _classify_speed per athlete VDOT_PACES
    cases = {}
    for zone in ("R", "I", "T", "M", "E"):
        lo_sec, hi_sec = config.VDOT_PACES[zone]
        mid_sec = (lo_sec + hi_sec) / 2
        cases[round(3600 / mid_sec, 2)] = zone
    bad = {s: psa._classify_speed(s) for s, e in cases.items() if psa._classify_speed(s) != e}
    check("post_session: _classify_speed correct per current athlete VDOT", not bad, f"wrong={bad}")

    # Injury ACWR EWMA invariant
    src = Path(ird.__file__).read_text()
    check("injury_risk: ACWR uses EWMA ATL/CTL (not raw week km)",
          "compute_current_pmc" in src and "ACWR" in src)

    # Training phases taper alignment
    active_date_str = rr.active_race()["date"]
    active_date = date.fromisoformat(active_date_str)
    taper_phases = [p for p in config.TRAINING_PHASES if p["phase"] == "taper"
                    and p["end"] >= active_date - timedelta(days=20)
                    and p["end"] <= active_date]
    check("phases: taper ends on active race date (peak never covers race day)",
          any(p["end"] == active_date for p in taper_phases),
          f"active_race={active_date_str}, taper_ends={[p['end'].isoformat() for p in taper_phases]}")

    race_specific_on_race_day = [p for p in config.TRAINING_PHASES
                                  if p["phase"] == "race_specific" and p["end"] >= active_date
                                  and p["start"] <= active_date]   # later seasons (e.g. 2027) are not this race's block
    check("phases: race_specific does NOT extend to/past race day",
          not race_specific_on_race_day,
          f"offending={[p['name'] for p in race_specific_on_race_day]}")

    # Race registry invariants
    _races = rr.load_races()
    _active_key = rr.active_race_key()
    check("races: active_race_key() points to a real, active race",
          _active_key in _races and _races[_active_key].get("active", True))
    _archived_but_active = [k for k, r in _races.items()
                             if not r.get("active", True) and k == _active_key]
    check("races: no archived race is treated as the active race", not _archived_but_active)

    # Athlete data synchronization
    aj = ROOT / "GarminRawData" / "athlete.json"
    data = json.loads(aj.read_text())
    check("athlete.json: vdot matches config", data["vdot"] == config.ATHLETE["vdot"])
    check("athlete.json: lthr matches config", data["lthr"] == config.ATHLETE["lthr"])

    # Static scan for stale zone literals
    STALE = [
        r"HR\s*<\s*155", r"HR:\s*<\s*155",
        r"HR\s*<\s*150\b",
        r"170\s*[–-]\s*176", r"HR\s*<\s*176",
        r"VDOT\s*38\b",
        r"8\.4km/h", r"@\s*7:08",
        r"12→8\s*km/h",
        r"base_ceilings\s*=\s*\[170",
        r"Thailand Local Race \(2026",
        r"Taper \+ ATM",
    ]
    offenders = {}
    scan_files = list(TOOLS.glob("*.py")) + list(COACH_MCP.glob("*.py"))
    for f in scan_files:
        txt = f.read_text(encoding="utf-8")
        hits = [pat for pat in STALE if re.search(pat, txt)]
        if hits:
            offenders[f.name] = hits
    check("STATIC: no stale zone literals (155/150/170–176/VDOT38) in any tool",
          not offenders, f"offenders={offenders}")

    # Static scan for the "zombie fallback" bug class: an except-block (or
    # unconditional default) that hardcodes an ARCHIVED race name/date/key
    # instead of failing loudly or picking from the live active-race list.
    # Round-5/6 UAT found this exact shape independently reintroduced across
    # 7 different tools (daily_brief, nutrition_calculator, race_registry,
    # taper_monitor, race_pace_planner, training_planner, training_load,
    # session_prescriber, heat_acclimation, weather_adjuster, season_summary)
    # each time race_registry.py failed/was unavailable — this check exists
    # so a copy-pasted "except Exception: fall back to X" doesn't quietly
    # reintroduce a hardcoded archived race the next time a tool is added
    # or edited.
    ZOMBIE_FALLBACK = [
        r'"atm"\s*:\s*\{',                       # {"atm": {...}} dict literal
        r'DEFAULT_RACE\s*=\s*"atm"',
        r'_default_race\s*=\s*"atm"',
        r'date\(2026,\s*11,\s*29\)',              # archived ATM race date
        r'ATM Bangkok Marathon',
        r'FUJI MARATHON RACE PLAN',
        r'สำหรับ Fuji Marathon',
        r'Bangkok→Fuji transition tracker',
        r'Thai Race"',
        r'"hm"\s*,\s*"fuji"\]\s*,\s*"hm"',        # old weather_adjuster.py shape
        r'"atm"\s*,\s*\["hm"',                    # old season_summary.py shape
    ]
    zombie_offenders = {}
    for f in scan_files:
        txt = f.read_text(encoding="utf-8")
        hits = [pat for pat in ZOMBIE_FALLBACK if re.search(pat, txt)]
        if hits:
            zombie_offenders[f.name] = hits
    check("STATIC: no hardcoded-archived-race zombie fallback (atm/fuji/2026-11-29) in any tool",
          not zombie_offenders, f"offenders={zombie_offenders}")


# ===========================================================================
# 7. FUNCTIONAL TESTS — Real Tools End-to-End Flow (CLI Regression Suite)
# ===========================================================================
def functional_tests():
    print("\n── 7. FUNCTIONAL (full flow, output vs config) ──────────")
    import config
    import race_registry as rr
    e_ceiling = config.HR_ZONE_BOUNDS["Z1_E"][1]

    flow = [
        ["session_prescriber.py", "--week", "current"],
        ["training_planner.py"],
        ["race_pace_planner.py"],
        ["nutrition_calculator.py", "--race", "bangsaen", "--temp", "27", "--humidity", "75"],
        ["training_load.py"],
        ["race_registry.py", "--all"],
    ]
    for args in flow:
        rc, out = run_tool(args)
        check(f"flow: {args[0]} runs (rc=0)", rc == 0,
              out.strip().splitlines()[-1] if out else "no output")

    # Easy ceiling agreement across branches
    hrs = []
    for scen in (["--week", "current"], ["--week", "current", "--bb", "35", "--pain", "mild"]):
        rc, out = run_tool(["session_prescriber.py", *scen])
        hrs += [int(m) for m in re.findall(r"HR\s*<\s*(\d{3})", out)]
    bad = [h for h in hrs if h not in (e_ceiling, 140)]
    check(f"flow: all Easy HR ceilings == config ({e_ceiling}) across branches",
          hrs and not bad, f"found {sorted(set(hrs))}, expected {e_ceiling} (+140 recovery)")

    # Training load output invariants
    rc, tl_out = run_tool(["training_load.py"])
    check("flow: no 'Fuji' in training_load countdown", "Fuji" not in tl_out)

    adate = rr.active_race()["date"][:7]
    check("flow: training_load countdown uses active race (2026-11)",
          "173" in tl_out or "2026-11" in tl_out or adate.replace("2026-", "").lstrip("0") + " " in tl_out
          or "ATM" in tl_out, "countdown missing active race")

    # Regression guard: tools reject archived race keys
    archived_keys = [k for k, r in rr.load_races().items() if not r.get("active", True)]
    race_tools = ["nutrition_calculator.py", "taper_monitor.py", "season_summary.py",
                  "race_pace_planner.py", "training_planner.py", "weather_adjuster.py"]
    for tool in race_tools:
        for key in archived_keys:
            rc, out = run_tool([tool, "--race", key])
            check(f"flow: {tool} rejects archived --race {key}",
                  rc != 0 and "invalid choice" in out.lower(),
                  f"expected argparse rejection, got rc={rc}")


# ===========================================================================
def _require_athlete_data():
    """Friendly early-exit if athlete.json / races.json haven't been created yet."""
    missing = [p.name for p in
               (ROOT / "GarminRawData" / "athlete.json", ROOT / "GarminRawData" / "races.json")
               if not p.exists()]
    if missing:
        print("=" * 60)
        print("🧪 PROJECT 179 — TEST SUITE")
        print("=" * 60)
        print(f"❌ Missing: {', '.join(missing)}")
        print("\nFix:")
        for name in missing:
            print(f"  cp GarminRawData/{name.replace('.json', '.example.json')} GarminRawData/{name}")
        print("=" * 60)
        sys.exit(1)

# ===========================================================================
# 8. PLAUSIBILITY — outputs must make physiological sense, not just be
#    formula-consistent (added 2026-11-17 after a 107/107-green suite still
#    let race_predictor report VDOT 33.3 for a VDOT-40 athlete).
# ===========================================================================
def plausibility_tests():
    import io, contextlib
    from config import ATHLETE as A
    import training_load as tl
    import race_predictor as rp
    import daily_brief as db

    check("plausibility: hrTSS IF=1.0 anchored at LTHR (not T-zone floor)", tl.T_HR == A["lthr"])
    check("plausibility: 1h at LTHR == 100 hrTSS",
          abs(tl.calc_hr_tss({"averageHR": A["lthr"], "duration": 3600}) - 100) < 0.5)

    base = A["vdot"]
    t_lo, t_hi = rp.T_HR_LO, rp.T_HR_HI
    sessions = [
        {"date": "2026-09-10", "session_type": "Interval (I)", "avg_hr": 159, "pace": "6:21/km"},  # whole-session pace artefact
        {"date": "2026-09-08", "session_type": "Threshold (T)", "avg_hr": t_lo + 2, "pace": "5:05/km"},
        {"date": "2026-09-01", "session_type": "Threshold (T)", "avg_hr": t_lo + 3, "pace": "5:05/km"},
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        est, used = rp.estimate_current_vdot(sessions)
    check("plausibility: artefact session cannot drag estimate outside ±band",
          est is not None and abs(est - base) <= rp.PLAUSIBLE_BAND, f"est={est} base={base}")
    check("plausibility: artefact I-session (6:21/km whole-session pace) rejected",
          all(u[0] != "2026-09-10" for u in used))
    hot = rp.estimate_vdot_from_session(
        {"date": "2026-09-01", "session_type": "Threshold (T)", "avg_hr": t_lo, "pace": "5:05/km"}, quality_hr_avg=t_lo - 0)
    check("plausibility: HR-only T estimate capped at ±3 VDOT",
          hot is None or abs(hot - base) <= 3.0, f"{hot}")
    slow_hot = rp.estimate_vdot_from_session(
        {"date": "2026-09-01", "session_type": "Threshold (T)", "avg_hr": t_hi, "pace": "6:00/km"}, quality_hr_avg=t_hi)
    check("plausibility: high HR at slow pace can signal DOWN (estimator not up-only)",
          slow_hot is not None and slow_hot < base, f"{slow_hot}")

    today = date.today().isoformat()
    check("plausibility: predictor FM goal is an upcoming race (or none)",
          rp._FM_RACE_DATE == "—" or rp._FM_RACE_DATE >= today[:7], rp._FM_RACE_DATE)
    fef = A.get("fm_endurance_factor", 1.0)
    check("plausibility: fm_endurance_factor within 1.0–1.4", 1.0 <= fef <= 1.4, str(fef))
    # FM goal VDOT must account for the endurance factor (2026-11-18: printed
    # "need VDOT 37.9 (gap +-3.2)" for sub-4 at VDOT 41 / factor 1.17)
    r_pure = rp.fm_goal_requirements(240, 40, 1.0)
    r_fef  = rp.fm_goal_requirements(240, 40, 1.17)
    check("plausibility: FM goal VDOT needed rises with endurance factor",
          r_fef["vdot_needed"] > r_pure["vdot_needed"] and r_fef["vdot_gap"] > 0,
          f"pure={r_pure} fef={r_fef}")
    check("plausibility: factor needed at current VDOT is between 1.0 and current factor",
          1.0 <= r_fef["factor_needed_at_current_vdot"] < 1.17, str(r_fef))

    # v1.7: ONE training-VDOT estimate in the project (vdot_estimator merged into
    # race_predictor). Two tools answering "what's my VDOT" gave +1.1 vs −8.9.
    _tools = Path(rp.__file__).parent
    check("consistency: vdot_estimator.py stays merged (single training-VDOT source)",
          not (_tools / "vdot_estimator.py").exists()
          and not any("vdot_estimator" in p.read_text() for p in _tools.glob("*.sh")))
    bts = rp.find_breakthroughs(rp.load_session_log(), weeks=520)
    check("plausibility: breakthrough pace-VDOT within confirmed +1…+band (no artefacts)",
          all(A["vdot"] + rp.BREAKTHROUGH_THRESHOLD <= b["pace_vdot"] <= A["vdot"] + rp.PLAUSIBLE_BAND
              for b in bts), str(bts))
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _res = rp._run(type("A", (), {"goal_hm": None, "goal_fm": 240, "json": True})())
    # CI has no personal session log (gitignored) → the tool must still return a
    # well-formed dict with an explicit "error" instead of the full payload.
    _need = {"vdot_confirmed", "training_estimate"} | (
        set() if "error" in _res else {"breakthroughs", "goals"})
    check("functional: race_predictor --json carries estimate, confirmed VDOT, breakthroughs",
          _need <= set(_res) and _res["vdot_confirmed"] == A["vdot"], str(list(_res)))

    # v1.7: run-window BB + stamina are written at LOG time (no post-run patchers)
    import session_logger as sl
    _removed = ["stamina_patcher.py", "daily_aggregator.py", "fuji_race_pacer.py", "tm_patch.py"]
    check("consistency: removed patchers/duplicates stay removed (no script calls them)",
          not any((_tools / f).exists() for f in _removed)
          and not (_tools.parent / "fix_session_bb.py").exists()
          and not any(x in p.read_text() for p in _tools.glob("*.sh")
                      for x in ("stamina_patcher.py ", "fix_session_bb.py\"", "tm_patch.py")))

    class _NoTimeline:            # offline client: stress timeline unavailable
        def get_stress_data(self, d): return {}
    _act = {"_activitySummary": {"summaryDTO": {"differenceBodyBattery": -27,
            "startTimeLocal": "2026-09-20T15:00:00", "duration": 9000}},
            "metricDescriptors": [{"key": "directAvailableStamina", "metricsIndex": 0}],
            "activityDetailMetrics": [{"metrics": [100]}, {"metrics": [88]}, {"metrics": [74]}]}
    _bb = sl.run_window_bb(_act, fallback_start=53, client=_NoTimeline())
    check("plausibility: run-window BB uses the activity's own drop (−27), not day peak→sync value",
          _bb["bb_start"] == 53 and _bb["bb_end"] == 26 and _bb["bb_source"].startswith("activity_diff"),
          str(_bb))
    check("plausibility: run-window BB with no drop info → no fabricated end value",
          sl.run_window_bb({}, fallback_start=80, client=_NoTimeline())["bb_end"] is None)
    check("unit: stamina_from_details reads first/last directAvailableStamina",
          sl.stamina_from_details(_act) == (100, 74) and sl.stamina_from_details({}) == (None, None))

    rb = A["rhr"]
    d1, _ = db.readiness_decision(80, "balanced", "quality1", rhr=rb + 9, rhr_baseline=rb)
    d2, _ = db.readiness_decision(80, "balanced", "long", rhr=rb + 5, rhr_baseline=rb)
    d3, _ = db.readiness_decision(80, "balanced", "quality1", rhr=rb, rhr_baseline=rb)
    d4, _ = db.readiness_decision(80, "balanced", "easy", rhr=rb + 5, rhr_baseline=rb)
    check("plausibility: readiness downgrades quality when RHR +9 over baseline", d1 == "MODIFY")
    check("plausibility: readiness downgrades long run when RHR +5 over baseline", d2 == "MODIFY")
    check("plausibility: readiness GO at baseline RHR + good BB/HRV", d3 == "GO")
    check("plausibility: easy day still GO at RHR +5", d4 == "GO")
    # Strictest-rule-wins (2026-11-17: mild pain masked RHR +11 / HRV unbalanced)
    ra = db.readiness_assess(48, "unbalanced", "quality1", pain="mild", rhr=rb + 11, rhr_baseline=rb)
    check("readiness: strictest rule wins (pain + RHR+11 + HRV → cap ≤6 km)",
          ra["decision"] == "MODIFY" and ra["cap_km"] == 6, str(ra))
    check("readiness: other triggered reasons are kept", len(ra["others"]) >= 2, str(ra["others"]))
    ra2 = db.readiness_assess(25, "unbalanced", "easy", pain="mild", rhr=rb + 11, rhr_baseline=rb)
    check("readiness: REST beats every MODIFY (HRV unbalanced + BB<30)", ra2["decision"] == "REST")
    plan = db.build_action_plan({"label": "Long", "note": ""}, "MODIFY", "long", None, None, cap_km=6)
    check("readiness: MODIFY on a long-run day no longer prints a long run",
          not any("Long Run\n" in l or l.strip() == "📋 Long Run" for l in plan)
          and any("ลดระดับจาก Long Run" in l for l in plan), " | ".join(plan))
    check("readiness: cap_km applied to the plan distance",
          any("–6.0 km" in l for l in plan), " | ".join(plan))
    # form_analysis (post-run per-km form + cadence lock) — synthetic stream
    import form_analysis as fa
    def _mk(n_km, hr_fn, cad_fn, gct_fn):
        desc = [{"key": k, "metricsIndex": i} for i, k in enumerate(
            ["directHeartRate", "sumDistance", "directSpeed", "directRunCadence",
             "directGroundContactTime", "directVerticalRatio", "directStrideLength", "sumElapsedDuration"])]
        rows, step = [], 10  # one sample per 10 m at 3 m/s
        for i in range(n_km * 100):
            d = i * step
            rows.append({"metrics": [hr_fn(i), d, 3.0, cad_fn(i) / 2, gct_fn(d), 8.5, 100, d / 3.0]})
        return {"metricDescriptors": desc, "activityDetailMetrics": rows}
    held = fa.analyze_form(_mk(6, lambda i: 150, lambda i: 170, lambda d: 250))
    check("form: per-km rows built from watch distance", len(held["per_km"]) == 6, str(len(held["per_km"])))
    check("form: steady run → 'form held'", held["drift"]["verdict"] == "form held", str(held["drift"]))
    tired = fa.analyze_form(_mk(6, lambda i: 150, lambda i: 170, lambda d: 250 + d / 200))
    check("form: GCT rising ≥10 ms → 'form fatigue'", tired["drift"]["verdict"] == "form fatigue", str(tired["drift"]))
    locked = fa.analyze_form(_mk(3, lambda i: 170, lambda i: 170, lambda d: 250))
    check("form: HR == cadence → cadence lock 'suspected'", locked["cadence_lock"]["verdict"] == "suspected")
    free = fa.analyze_form(_mk(3, lambda i: 145, lambda i: 172, lambda d: 250))
    check("form: HR far from cadence → lock 'not detected'", free["cadence_lock"]["verdict"] == "not detected")
    belt = fa.analyze_form(_mk(2, lambda i: 140, lambda i: 160, lambda d: 280), belt_kmh=9.0)
    check("form: --belt-kmh overrides watch pace (9.0 km/h → 6:40)",
          all(r["pace"] == "6:40" and r["pace_source"] == "belt" for r in belt["per_km"]))
    check("plausibility: coaching_constraints is a list",
          isinstance(A.get("coaching_constraints", []), list))


def v171_tests():
    """v1.7.1: tiered taper, athlete dosing rule, single session log, volume source."""
    print("\n── 9. v1.7.1 REGRESSIONS ─────────────────────────────────")
    import tempfile
    import session_prescriber as sp
    import nutrition_calculator as nc
    import session_logger as sl
    import weekly_load_report as wlr
    from datetime import date as _d

    # ── Tiered taper (was: every race got the 14-day A taper) ──
    A_, B_, C_ = (sp.TAPER_PROFILES[t] for t in "ABC")
    check("taper: windows shrink with tier (A > B > C)",
          A_["window"] > B_["window"] > C_["window"] and A_["no_quality"] > B_["no_quality"] > C_["no_quality"])
    _day = _d(2026, 1, 6)
    def _q(days, prof):
        return sp._prescribe_day(1, "quality1", "quality", 80, "balanced", "none",
                                 days, "R", _day, taper_prof=prof)["decision"]
    check("taper: B-race 5 days out keeps the quality session", _q(5, B_) == "GO")
    check("taper: B-race 3 days out → quality becomes easy", _q(3, B_) == "MODIFY")
    check("taper: A-race 10 days out → quality becomes easy (unchanged A rule)", _q(10, A_) == "MODIFY")
    _saved = sp.RACE_DATES
    try:
        sp.RACE_DATES = [{"name": "C", "date": _d(2026, 1, 11), "tier": "C", "taper_days": None}]
        check("taper: C-race 5 days out does not taper the day", sp._active_taper(_d(2026, 1, 6))[0] is None)
        check("taper: C-race 2 days out does", sp._active_taper(_d(2026, 1, 9))[1] == 2)
        sp.RACE_DATES = [{"name": "X", "date": _d(2026, 1, 11), "tier": "B", "taper_days": 10}]
        check("taper: races.json taper_days overrides the tier window",
              sp._active_taper(_d(2026, 1, 2))[1] == 9)
    finally:
        sp.RACE_DATES = _saved

    # ── Athlete dosing rule (was: 2–3 Prevo bundled per water station) ──
    _rule = {"prevo_every_km": 4, "gel_every_km": 6, "max_prevo_per_stop": 1,
             "pre_prevo": 1, "pre_gel": 1, "skip_last_km": 2}
    hm = nc.schedule_plan(21.0975, 3000, _rule)
    check("nutrition: HM rule → Prevo at 4/8/12/16, gel at 6/12/18",
          [x["km"] for x in hm["stops"] if x["prevo"]] == [4, 8, 12, 16]
          and [x["km"] for x in hm["stops"] if x["gel"]] == [6, 12, 18], str(hm["stops"]))
    check("nutrition: never more than 1 Prevo at once", hm["max_prevo_at_once"] == 1)
    check("nutrition: Na total = (pre + in-race) × label values",
          hm["plan_na"] == 5 * nc.PREVO_NA_MG + 4 * nc.AMINOVITAL_NA, str(hm["plan_na"]))
    fm = nc.schedule_plan(42.195, 6000, _rule)
    check("nutrition: no dose inside the last skip_last_km",
          max(x["km"] for x in fm["stops"]) <= 42.195 - 2)

    # ── Single session log (was: sessions.json + tm_patch sync) ──
    _orig = sl.MASTER_PATH
    with tempfile.TemporaryDirectory() as td:
        sl.MASTER_PATH = Path(td) / "sessions_master.json"
        try:
            entry = {"date": "2026-01-02", "activity_id": 900, "session_type": "Threshold (T)",
                     "is_treadmill": True, "pace": "5:00/km", "avg_hr": 170, "max_hr": 180,
                     "grade": "A", "bb_start": 80, "bb_end": 60}
            check("single-log: analysis without lap log creates a stub", sl.save_analysis(entry)
                  and sl._load_master()["sessions"][0].get("_stub") is True)
            check("single-log: a stub does not block the lap-level log", not sl._is_logged(900, sl._load_master()))
            m = sl._load_master()
            sl._insert_record(m, {"date": "2026-01-02", "activity_id": 900, "session_type": "threshold",
                                  "is_treadmill": True, "laps": [],
                                  "treadmill_phases": [{"speed_kmh": 12.0, "distance_km": 5.0},
                                                       {"speed_kmh": 8.0, "distance_km": 2.0}],
                                  "bb_start": 78, "bb_end": 55, "bb_source": "activity_diff"})
            sl._save_master(m)
            rows = sl.load_quality_log()
            check("single-log: full record replaces stub and keeps the analysis",
                  len(sl._load_master()["sessions"]) == 1 and rows and rows[0]["grade"] == "A")
            check("single-log: treadmill pace from belt speed (12 km/h → 5:00)",
                  rows[0]["pace"] == "5:00/km" and rows[0]["pace_source"] == "belt_speed", str(rows[0]))
            check("single-log: master BB wins over the analysis copy", rows[0]["bb_end"] == 55)
            check("single-log: second analysis of same activity is refused", not sl.save_analysis(entry))
            m = sl._load_master()
            m["sessions"][0].update({"bb_start": None, "bb_end": None, "bb_source": "unavailable"})
            sl._save_master(m)
            check("single-log: bb_source=unavailable never falls back to old BB",
                  sl.load_quality_log()[0]["bb_end"] is None)
        finally:
            sl.MASTER_PATH = _orig
    _tools = Path(sl.__file__).parent
    _src = [f for f in _tools.glob("*.py")]
    check("single-log: no tool opens QualitySessionLog/sessions.json any more",
          not any('"sessions.json"' in f.read_text() for f in _src))

    # ── Weekly volume: one source, no >100% zones ──
    acts = [{"startTimeLocal": "2026-01-05 07:00:00", "distance": 2534, "duration": 900,
             "averageHR": 130, "maxHR": 140}]
    wk = wlr.analyze_week(acts, "2026-01-05", "2026-01-11")
    check("weekly: zone km sum equals unrounded volume (no 101%)",
          abs(sum(wk["zones"].values()) - 2.534) < 1e-9)
    check("weekly: season_summary reuses analyze_week (single volume source)",
          "analyze_week" in (_tools / "season_summary.py").read_text())


def v172_tests():
    """v1.7.2 — readiness uses last night's HRV + a rolling RHR baseline."""
    print("\n── v1.7.2: nightly HRV + rolling RHR baseline ──")
    from datetime import date as _d, timedelta as _td
    import hrv_trend as ht
    import daily_brief as db
    import garmin_client as gc
    rng = {"hrv_balanced_low": 85, "hrv_balanced_high": 121}
    ok_night  = {**rng, "hrv_last_night": 94, "hrv_status": "UNBALANCED"}
    low_night = {**rng, "hrv_last_night": 80, "hrv_status": "BALANCED"}
    check("hrv: last night inside range beats a lagging UNBALANCED 7-day status",
          ht.readiness_hrv(ok_night) == "balanced" and not ht.is_suppressed(ok_night))
    check("hrv: last night below range is suppressed even if 7-day says BALANCED",
          ht.readiness_hrv(low_night) == "unbalanced" and ht.is_suppressed(low_night))
    check("hrv: above range → 'high' (no MODIFY vote, still blocks quality)",
          ht.readiness_hrv({**rng, "hrv_last_night": 130}) == "high"
          and db.readiness_assess(80, "high", "easy")["decision"] == "GO"
          and db.readiness_assess(80, "high", "quality1")["decision"] == "MODIFY")
    check("hrv: no nightly numbers → falls back to 7-day status",
          ht.readiness_hrv({"hrv_status": "UNBALANCED"}) == "unbalanced"
          and ht.is_suppressed({"hrv_status": "UNBALANCED"}))
    # post-race: 7-day status red for 3 days but last night recovered → no crash
    recs = [{**rng, "hrv_last_night": v, "hrv_status": "UNBALANCED"} for v in (80, 83, 94)]
    check("hrv: crash streak counts nights, one race no longer = 3-day crash",
          ht.crash_report(recs)["streak"] == 0)
    check("hrv: 3 low nights in a row is still a crash",
          ht.crash_report([low_night] * 3)["severity"] == "crash")
    check("hrv: garmin_client stores nightly numbers in the wellness master",
          {"hrv_last_night", "hrv_balanced_low", "hrv_balanced_high"} <= set(gc._HEALTH_KEYS)
          and gc.hrv_numbers({"lastNightAvg": 94, "weeklyAvg": 79,
                              "baseline": {"balancedLow": 85, "balancedUpper": 121}})
          == {"hrv_last_night": 94, "hrv_weekly_avg": 79,
              "hrv_balanced_low": 85, "hrv_balanced_high": 121})

    t = _d(2026, 11, 19)
    mk = lambda i, r: {"_date": t - _td(days=i), "resting_hr": r}
    recs = [mk(i, 48 + (i % 3)) for i in range(1, 29)] + [mk(0, 60)]
    b = ht.rhr_baseline(recs, today=t)
    check("rhr: baseline = median of the 28 days before today (today excluded)",
          b == 49.0, str(b))
    check("rhr: <7 nights → None (caller falls back to athlete.json)",
          ht.rhr_baseline([mk(i, 50) for i in range(1, 5)], today=t) is None)
    src = (Path(db.__file__)).read_text()
    check("rhr: daily_brief readiness uses rolling baseline, not athlete.json rhr",
          'rhr_baseline=ATHLETE.get("rhr")' not in src and "rhr_baseline(today=today)" in src)
    check("hrv: daily_brief + session_prescriber decide on readiness_hrv",
          "readiness_hrv(health)" in src
          and "readiness_hrv(cached)" in (Path(db.__file__).parent / "session_prescriber.py").read_text())


def main():
    _require_athlete_data()
    print("=" * 60)
    print("🧪 PROJECT 179 — COMPREHENSIVE TEST SUITE")
    print("=" * 60)

    # 1. P0 Daily & Session Core
    unit_p0_daily_and_session()

    # 2. P1 Core Physiology & VDOT Math
    unit_p1_physiology_and_vdot()

    # 3. P2 Macro & Weekly Planning
    unit_p2_macro_and_weekly()

    # 4. P3 Race Tactics & Nutrition
    unit_p3_race_and_nutrition()

    # 5. P4 Environmental & Registry
    unit_p4_environmental_and_registry()

    # 6. Consistency & Static Scans
    consistency_tests()

    # 7. Functional CLI Regression Flows
    functional_tests()

    # 8. Plausibility (outputs make physiological sense)
    plausibility_tests()
    v171_tests()
    v172_tests()

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total  = len(_RESULTS)
    print("\n" + "=" * 60)
    print(f"  RESULT: {passed}/{total} passed")
    if passed < total:
        print("  ❌ FAILURES:")
        for name, ok, detail in _RESULTS:
            if not ok:
                print(f"     - {name}  {('[' + detail + ']') if detail else ''}")
    else:
        print("  ✅ ALL GREEN")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
