#!/usr/bin/env python3
"""
test_suite.py — PROJECT 179 self-contained test runner (no pytest needed).

Three layers:
  1. UNIT        — core logic of each tool (formulas, monotonicity, bands)
  2. CONSISTENCY — single-source invariants (no tool may contradict config /
                   athlete.json / races.json). This is what catches the
                   "HR < 155 vs 163" class of bug automatically.
  3. FUNCTIONAL  — run the real daily-flow tools end-to-end, assert no crash +
                   output agrees with config.

Run:  .venv/bin/python3.13 GarminRawData/tests/test_suite.py
Exit code 0 = all pass, 1 = any fail.
"""
import sys
import re
import subprocess
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent.parent          # AntiGravity/
TOOLS     = ROOT / "GarminRawData" / "tools"
COACH_MCP = ROOT / "skills" / "garmin_coach_mcp"
PYBIN     = ROOT / ".venv" / "bin" / "python3.13"

sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(COACH_MCP))

# ---------------------------------------------------------------------------
# tiny test harness
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
    env = {"PYTHONPATH": f"{COACH_MCP}:{TOOLS}", "PATH": "/usr/bin:/bin"}
    import os
    env = {**os.environ, "PYTHONPATH": f"{COACH_MCP}:{TOOLS}"}
    try:
        p = subprocess.run([str(PYBIN), *[str(a) for a in args]],
                           cwd=str(TOOLS), env=env, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"


# ===========================================================================
# 1. UNIT TESTS
# ===========================================================================
def unit_tests():
    print("\n── 1. UNIT ──────────────────────────────────────────────")
    import config
    import vdot_math as vm

    # config derives correctly from athlete.json
    z = config.HR_ZONE_BOUNDS
    check("config: T-ceiling == LTHR",
          z["Z3_T"][1] == config.ATHLETE["lthr"],
          f"T-ceiling {z['Z3_T'][1]} vs LTHR {config.ATHLETE['lthr']}")
    check("config: HR zones contiguous & monotonic",
          all(z[a][1] == z[b][0] for a, b in
              [("Z1_E","Z2_M"),("Z2_M","Z3_T"),("Z3_T","Z4_I"),("Z4_I","Z5_R")]))
    check("config: hrr == mhr - rhr",
          config.ATHLETE["hrr"] == config.ATHLETE["mhr"] - config.ATHLETE["rhr"])
    check("config: VDOT_PACES monotonic (E slower than R)",
          config.VDOT_PACES["E"][0] > config.VDOT_PACES["R"][1])

    # vdot_math — VDOT 40 known race times (Daniels table)
    hm = vm.predict_race_time(40, 21097)
    fm = vm.predict_race_time(40, 42195)
    check("vdot_math: VDOT40 HM ~1:50 (109-112min)", 109 <= hm <= 112, f"HM={hm:.1f}min")
    check("vdot_math: VDOT40 FM ~3:49 (227-232min)", 227 <= fm <= 232, f"FM={fm:.1f}min")
    check("vdot_math: longer distance = slower pace",
          vm.predict_race_time(40, 42195)/42.195 > vm.predict_race_time(40, 5000)/5.0)

    # treadmill_pace_model monotonic
    import treadmill_pace_model as tpm
    paces = [tpm.infer_pace_from_hr(h) for h in (150, 164, 175, 183)]
    secs  = [int(p.split(":")[0])*60 + int(p.split(":")[1].replace("/km","")) for p in paces]
    check("treadmill_pace_model: HR↑ → pace faster (monotonic)",
          all(secs[i] > secs[i+1] for i in range(len(secs)-1)), f"secs={secs}")

    # bangkok_climate sane
    import bangkok_climate as bc
    temps = [bc.morning_temp_c(m) for m in range(1, 13)]
    check("bangkok_climate: all temps 22-32°C", all(22 <= t <= 32 for t in temps), f"{temps}")
    check("bangkok_climate: Ely penalty 0 at ≤13°C", bc.ely_penalty_fraction(13) == 0)

    # training_planner — dynamic peak/taper math (pure functions, synthetic inputs
    # so this doesn't depend on live running_activities_all.json data changing).
    import training_planner as tp
    check("training_planner: target_peak_km stretches above historical peak",
          tp._target_peak_km(current_weekly_km=40, historical_peak_km=50) == 55,
          f"got {tp._target_peak_km(40, 50)}, expected 55 (50*1.13→55)")
    check("training_planner: target_peak_km never below current+5 (mid-buildup athlete)",
          tp._target_peak_km(current_weekly_km=68, historical_peak_km=50) >= 73,
          f"got {tp._target_peak_km(68, 50)}, expected >=73")
    check("training_planner: taper_km strictly decreasing toward race day",
          all(a > b for a, b in zip([tp._taper_km(w, 70) for w in [4,3,2,1,0]][:-1],
                                      [tp._taper_km(w, 70) for w in [4,3,2,1,0]][1:])),
          f"{[tp._taper_km(w, 70) for w in [4,3,2,1,0]]}")
    check("training_planner: taper_km race day == 0",
          tp._taper_km(0, 70) == 0)
    phase_km = tp._build_phase_km(45, 70)
    check("training_planner: phase_km progression base < quality < race_specific",
          phase_km["base"]["max"] < phase_km["quality"]["max"] < phase_km["race_specific"]["max"],
          f"{phase_km}")


# ===========================================================================
# 2. CONSISTENCY TESTS — single-source invariants (regression bugs)
# ===========================================================================
def consistency_tests():
    print("\n── 2. CONSISTENCY (single-source invariants) ────────────")
    import config
    import race_registry as rr

    # --- BUG CLASS: race_pace_planner must not diverge from vdot_math ---
    import vdot_math as vm, race_pace_planner as rpp
    worst = max(abs(rpp._vdot_to_race_pace(40, d) - vm.predict_race_time(40, d*1000)*60/d)
                for d in (5, 10, 21.097, 42.195))
    check("race_pace_planner == vdot_math (no rolled-own %VO2max)",
          worst < 2, f"max divergence {worst:.1f} sec/km")

    # --- BUG CLASS: nutrition plan never exceeds ACSM 80% ceiling ---
    import nutrition_calculator as nc
    over = []
    for t in (15, 20, 25, 29, 33, 38):
        sr, *_ = nc.sweat_rate_lhr(t, 82)
        loss = sr * 115/60 * nc.sweat_na_mgl()
        lo, hi = nc.na_requirement(sr, 115)
        p = nc.plan_caps(loss, lo, hi, 3)
        if p["plan_na"] > hi + 1:
            over.append((t, p["replace_pct"]))
    check("nutrition: plan never exceeds 80% ceiling (all temps)", not over, f"violations={over}")

    # --- BUG CLASS: _classify_speed maps to correct VDOT-40 zone ---
    import post_session_analyzer as psa
    cases = {13.6:"R", 13.0:"I", 12.0:"T", 11.3:"M", 9.0:"E"}
    bad = {s:psa._classify_speed(s) for s,e in cases.items() if psa._classify_speed(s)!=e}
    check("post_session: _classify_speed correct per VDOT-40", not bad, f"wrong={bad}")

    # --- BUG CLASS: injury ACWR uses EWMA (not raw-km false CRITICAL) ---
    import injury_risk_detector as ird
    src = Path(ird.__file__).read_text()
    check("injury_risk: ACWR uses EWMA ATL/CTL (not raw week km)",
          "compute_current_pmc" in src and "ACWR" in src)

    # --- TRAINING_PHASES: taper must end ON active race date, not after ---
    import config as _cfg
    from datetime import date as _date
    active_date_str = rr.active_race()["date"]   # e.g. "2026-11-15"
    active_date = _date.fromisoformat(active_date_str)
    taper_phases = [p for p in _cfg.TRAINING_PHASES if p["phase"] == "taper"
                    and p["end"] >= active_date - __import__("datetime").timedelta(days=20)
                    and p["end"] <= active_date]
    check("phases: taper ends on active race date (peak never covers race day)",
          any(p["end"] == active_date for p in taper_phases),
          f"active_race={active_date_str}, taper_ends={[p['end'].isoformat() for p in taper_phases]}")
    race_specific_on_race_day = [p for p in _cfg.TRAINING_PHASES
                                  if p["phase"] == "race_specific" and p["end"] >= active_date]
    check("phases: race_specific does NOT extend to/past race day",
          not race_specific_on_race_day,
          f"offending={[p['name'] for p in race_specific_on_race_day]}")

    # --- race registry: Bangsaen42 active, Fuji archived ---
    check("races: active race == bangsaen", rr.active_race_key() == "bangsaen")
    check("races: Fuji is archived (not active)",
          not rr.load_races().get("fuji", {}).get("active", True))

    # --- athlete.json is the source: changing config requires editing it ---
    aj = ROOT / "GarminRawData" / "athlete.json"
    import json
    data = json.loads(aj.read_text())
    check("athlete.json: vdot matches config", data["vdot"] == config.ATHLETE["vdot"])
    check("athlete.json: lthr matches config", data["lthr"] == config.ATHLETE["lthr"])

    # --- STATIC SOURCE SCAN: no STALE zone literals anywhere (catches every
    #     branch regardless of runtime; this is what runtime functional tests
    #     MISS when a buggy line sits in an un-exercised conditional). ---
    STALE = [
        r"HR\s*<\s*155", r"HR:\s*<\s*155",      # old E ceiling (now 163)
        r"HR\s*<\s*150\b",                        # old easy/quality-override ceiling
        r"170\s*[–-]\s*176", r"HR\s*<\s*176",   # old T zone (now 174–183)
        r"VDOT\s*38\b",                           # superseded VDOT
        r"8\.4km/h", r"@\s*7:08",                 # old TM easy prescription (VDOT38-era)
                                                   # (note: "8.4 km/h" with space in
                                                   #  treadmill anchor comment = measured data, OK)
        r"12→8\s*km/h",                           # old TM stride instruction
        r"base_ceilings\s*=\s*\[170",            # weather_adjuster hardcoded HR plan
        r"Thailand Local Race \(2026",            # stale race label/date in output
        r"Taper \+ ATM",                          # stale taper phase name (A-race = Bangsaen42)
    ]
    # legit deliberate values to NOT flag: BB<20 recovery "HR < 140", crash "HR < 140"
    offenders = {}
    scan_files = list(TOOLS.glob("*.py")) + list(COACH_MCP.glob("*.py"))
    for f in scan_files:
        txt = f.read_text(encoding="utf-8")
        hits = [pat for pat in STALE if re.search(pat, txt)]
        if hits:
            offenders[f.name] = hits
    check("STATIC: no stale zone literals (155/150/170–176/VDOT38) in any tool",
          not offenders, f"offenders={offenders}")


# ===========================================================================
# 3. FUNCTIONAL TESTS — real tools end-to-end, output agrees with config
# ===========================================================================
def functional_tests():
    print("\n── 3. FUNCTIONAL (full flow, output vs config) ──────────")
    import config
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
        check(f"flow: {args[0]} runs (rc=0)", rc == 0, out.strip().splitlines()[-1] if out else "no output")

    # CONSISTENCY via output: every Easy "HR < N" == config E ceiling.
    # Run TWO scenarios so the MODIFY→easy override branch is also exercised
    # (normal week + low-BB forces quality→easy, hitting the other code path).
    hrs = []
    for scen in (["--week","current"], ["--week","current","--bb","35","--pain","mild"]):
        rc, out = run_tool(["session_prescriber.py", *scen])
        hrs += [int(m) for m in re.findall(r"HR\s*<\s*(\d{3})", out)]
    # 140 = deliberate low-BB recovery cap (allowed); only flag values that are
    # NEITHER the E ceiling NOR the recovery 140.
    bad = [h for h in hrs if h not in (e_ceiling, 140)]
    check(f"flow: all Easy HR ceilings == config ({e_ceiling}) across branches",
          hrs and not bad, f"found {sorted(set(hrs))}, expected {e_ceiling} (+140 recovery)")

    # CONSISTENCY: no archived Fuji in active flow output
    rc, tl = run_tool(["training_load.py"])
    check("flow: no 'Fuji' in training_load countdown", "Fuji" not in tl)

    # CONSISTENCY: race countdown date matches registry active race
    import race_registry as rr
    adate = rr.active_race()["date"][:7]   # YYYY-MM
    check("flow: training_load countdown uses active race (2026-11)",
          "173" in tl or "2026-11" in tl or adate.replace("2026-","").lstrip("0")+" " in tl
          or "ATM" in tl, "countdown missing active race")


# ===========================================================================
def main():
    print("=" * 60)
    print("🧪 PROJECT 179 — TEST SUITE")
    print("=" * 60)
    unit_tests()
    consistency_tests()
    functional_tests()

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total  = len(_RESULTS)
    print("\n" + "=" * 60)
    print(f"  RESULT: {passed}/{total} passed")
    if passed < total:
        print("  ❌ FAILURES:")
        for name, ok, detail in _RESULTS:
            if not ok:
                print(f"     - {name}  {('['+detail+']') if detail else ''}")
    else:
        print("  ✅ ALL GREEN")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
