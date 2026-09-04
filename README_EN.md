# Project 179 — Garmin JD Running Coach Agent

An AI running coach that connects to your Garmin data and coaches you using Jack Daniels' Running Formula. Talk to any AI agent — it reads your data, runs the analytics tools, and coaches you with real numbers.

![CI](https://github.com/LiangDej/Project179/actions/workflows/ci.yml/badge.svg)
![Version](https://img.shields.io/badge/version-1.2-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Compatible AI Agents

| Agent | Live Garmin data? | How to connect |
|---|---|---|
| **Custom agent (recommended)** | ✅ Full | Clone repo, set `PYTHONPATH=skills/garmin_coach_mcp`, run tools via bash — the most self-contained path |
| **Claude Code / Cowork** | ✅ Full | Open project folder — agent runs tools via bash just like a custom agent, no MCP server needed |
| **Gemini via Google Colab** | ✅ Full | Colab has real internet + pip install — `!git clone`, set credentials, pull live Garmin data just like a custom agent |
| **Cursor / GitHub Copilot** | ✅ Full | Open repo in IDE (runs on your real machine, normal internet access) — agent reads README as context |
| **ChatGPT** (Code Interpreter) | ❌ **No** | Code Interpreter's sandbox has **no internet access** — it cannot call the Garmin Connect API or OpenWeather, so `daily_brief.py`, `post_session_analyzer.py`, `hrv_trend.py`, and anything needing live data won't work |
| **Gemini app** (not via Colab) | ❌ **No** | Same reason as ChatGPT — no code execution path that reaches the internet |

> **ChatGPT/the Gemini app still work in a limited way:** upload the `.py` files for pure-calculator tools that don't need live data — `nutrition_calculator.py`, `race_pace_planner.py`, `training_planner.py`, `vdot_math.py` — along with `athlete.json`/`races.json`, and type inputs manually (e.g. `--temp 27 --humidity 82`). Calculations work fine; you just can't pull real Garmin data on either platform.
>
> **A note on MCP:** `skills/garmin_coach_mcp/` in this repo only contains `config.py`/`db_helper.py` (the shared zone/pace calculation logic every tool imports) — it is **not** a full FastMCP server (the real server runs in a separate, still-private repo). If your agent only speaks MCP tool calls, use the "run tools via bash" path instead — it covers everything and is the path this repo actually tests.

---

## Setup (one time)

### 1 · Install (3 commands)

```bash
git clone https://github.com/LiangDej/Project179.git
cd Project179
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

> **Windows:** the venv/interpreter path differs from Mac/Linux — use `.venv\Scripts\python.exe` instead of `.venv/bin/python3.13` everywhere in this doc (e.g. `.venv\Scripts\python.exe -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt`)
>
> **⚠️ Garmin login may fail on a Cloud VM/VPS** (AWS EC2, DigitalOcean, a GitHub Actions runner, Replit, **including Google Colab**) — Garmin Connect often blocks datacenter IP ranges via its Cloudflare WAF (login fails even with correct credentials). This is a commonly-reported issue with unofficial Garmin API libraries in general, but **not verified to affect every provider** (including Colab, despite the table above listing it as fully working — if login fails on any cloud provider, suspect this first). Safest is running from a residential IP at home (a real Mac/PC/WSL machine).

### 2 · Garmin credentials

```bash
mkdir -p ~/.config/garmin-coach
echo 'GARMIN_USERNAME=your@email.com' >> ~/.config/garmin-coach/.env
echo 'GARMIN_PASSWORD=yourpassword'   >> ~/.config/garmin-coach/.env
```

(the variable is named `GARMIN_USERNAME` even though the value is your email — see [`.env.example`](.env.example))

### 3 · Paste this prompt to your AI agent (that's it)

```
I just cloned Project 179 — Garmin JD Running Coach.
Please set me up as a new athlete. Ask me the following one by one:

1. Personal bests for each distance you have (5K / 10K / Half Marathon / Full Marathon) — only distances you've actually raced
2. LTHR (Lactate Threshold HR) — if you've done a field test before (e.g. a Friel 30-min TT), give the value.
   If not, say "none" — do not guess it or leave the example file's default value in place.
3. Resting HR (morning, before getting out of bed) and Max HR you've actually recorded (from a hard interval or hill sprint)
   — this matters a lot: using someone else's default could prescribe HR zones beyond what your body can safely do.
4. Body weight (kg) and age — used for nutrition calculations, and age as a rough MHR fallback only if you have no real MHR.
5. How many days/week do you actually run? Which day(s) are full rest (no training at all)? Do you lift — which day?
6. Any current injury or area you need to be careful with? (Say "none" if not — do not copy any example injury text.)
7. Main goal race (A-race): distance, event name, date, goal time
8. Do you have a B-race (tune-up)? If so: distance, date
9. Do you primarily run on a treadmill or outdoors?
10. Do you use an electrolyte or gel product during training/racing? If so, name the brand + Na/carb per unit from
    the label (check the label if you don't know). If you don't use anything, say "none" clearly — do not leave the
    example file's placeholder text in place.

From the PBs provided, estimate an initial VDOT and check whether it's consistent across all distances.
(e.g. 5K implies VDOT 44 but FM implies VDOT 38 → not consistent → which distance should we trust?)
Then recommend the first Time Trial to run (distance + target pace) to confirm VDOT before locking training zones.

Once all info is collected, copy athlete.example.json → athlete.json and races.example.json → races.json, then
**overwrite every relevant field with my real answers — leave no default/placeholder from the example file in
place, not even one field** (including pain_status, training_days_per_week, rest_days, strength_day, and
quality_nutrition/race_day_nutrition/long_run_nutrition — write real answers or "none"/"not used" over all of them).
Then run the test suite to verify.
```

The agent will ask questions one by one → write every field in your files (no leftover defaults from someone else) → run the test suite → report results. **You don't touch any files yourself.**

---

## After Setup — just talk to your agent

No commands to memorize, no flags to type — talk to the agent in plain language like you would a real coach. For example:

- "Give me my morning summary"
- "How did my run just now go?"
- "How many km have I run this week?"

The agent picks the right tool, pulls your Garmin data, and answers with real numbers and coaching advice — see [Tool Reference](#tool-reference) below for more example phrases.

**Not sure what to say first?** Try these three:
1. **"Give me my morning summary"** → body status + what to run today
2. **"Analyze my run just now"** → after every run
3. **"How many km have I run this week?"** → check you're on track with your plan

---

## Background Concepts (1 minute read before continuing)

The tool list below uses a lot of technical terms — knowing these 4 first makes everything much easier to follow:

| Term | Plain-language meaning |
|---|---|
| **VDOT** | A single number for "how fit are you" — higher means you can run faster/farther. Only confirmed from an actual race result or Time Trial (never guessed from how you feel) |
| **Training Zones (E/M/T/I/R)** | 5 levels of running intensity, from Easy (conversational pace) to Repetition (fastest) — calculated from your VDOT |
| **CTL / ATL / TSB** | "Long-term fitness" / "recent fatigue" / "freshness" — used to decide whether today should be a hard day or a rest day |
| **Body Battery** | A Garmin-specific term — your remaining daily energy reserve (0-100), similar to a phone's battery percentage |

No need to memorize these — the agent explains them every time it uses them. Just knowing roughly what they mean is enough.

---

## Tool Reference

> Agents that clone this repo use these tools automatically based on what you say.

---

### Daily Coaching

**`daily_brief.py`** — Morning dashboard before your run

| Say | You get |
|---|---|
| "Give me my morning summary" | Body Battery, HRV, sleep score, today's recommended session, days to race |
| "Morning summary, no internet" | Same but uses cached data (no live Garmin fetch) |
| "Morning summary, left leg a bit sore" | Adjusted prescription based on pain level |

---

**`session_prescriber.py`** — Weekly training plan

| Say | You get |
|---|---|
| "What should I run today?" | 7-day plan: session type (E/T/I/R/Long), distance, pace target, HR ceiling |
| "Plan this week, my BB is 70" | Plan using Body Battery 70 as readiness input |
| "BB only 35, slight leg pain, can I run?" | Auto-reduce intensity → Easy instead of Quality |

---

**`post_session_analyzer.py`** — Post-run analysis

| Say | You get |
|---|---|
| "Analyze my run just now" | Zone breakdown, cardiac decoupling %, JD verdict (session pass/fail) |
| "Analyze activity 22826276241" | Analysis for specified activity ID |
| "Analyze and save to log" | Analysis + save to sessions_master.json |

---

**`weekly_load_report.py`** — Weekly load report

| Say | You get |
|---|---|
| "How many km have I run this week?" | Total km, zone distribution, Easy/Quality ratio, vs target |
| "Show last 4 weeks" | 4-week trend: volume, intensity, consistency |

---

**`detect_session.py`** — Auto-classify session type

| Say | You get |
|---|---|
| "What type of session was that run?" | E / M / T / I / R / Race with confidence score |

---

### Training Load & Planning

**`training_load.py`** — PMC (ATL/CTL/TSB)

| Say | You get |
|---|---|
| "How's my fitness right now?" | ATL (fatigue), CTL (fitness), TSB (form) for last 42 days + chart |
| "What CTL do I need before race day?" | Target CTL path to A-race with weekly km guidance |
| "Am I accumulating too much fatigue?" | ACWR ratio + overreaching risk |

---

**`training_planner.py`** — Full season plan

| Say | You get |
|---|---|
| "Plan my whole season" | Phase-by-phase breakdown to race day: Base/Quality/Race-Specific/Taper + km targets |
| "Plan my last 8 weeks before the race" | Race-specific block: long run schedule, quality types, weekly volume |

---

**`daily_aggregator.py`** — Merge multiple sessions in one day

| Say | You get |
|---|---|
| "I ran twice today, what's my total?" | Daily + weekly total from multi-session, no double counting |

---

**`season_summary.py`** — Season overview

| Say | You get |
|---|---|
| "What phase am I in, am I ready to race?" | Current phase, CTL vs target, recent quality sessions, readiness summary |

---

### VDOT & Performance

**`vdot_estimator.py`** — Estimate VDOT from training

| Say | You get |
|---|---|
| "What's my VDOT right now?" | Training-based VDOT estimate from quality sessions (last 8 weeks) + disclaimer that race confirmation is needed |
| "Have I improved over the last 3 months?" | VDOT trend + session HR drift over 3 months |

---

**`race_predictor.py`** — Predict race time

| Say | You get |
|---|---|
| "If I raced a HM today, what would I run?" | Predicted HM + FM finish time from training fitness + gap to goal |
| "What VDOT do I need for Sub-4 FM?" | VDOT required + how many points still needed |

---

**`post_race_updater.py`** — Update VDOT after a race

| Say | You get |
|---|---|
| "Just ran HM in 1:52:30, update my VDOT" | Calculates new VDOT → updates athlete.json → recalculates zones/paces for all tools |
| "FM 3:58 in 27°C heat, should I adjust?" | Heat-adjusted VDOT + recommendation on whether to apply or wait for TT confirmation |

---

**`energy_efficiency_scorer.py`** — Gel/electrolyte efficiency

| Say | You get |
|---|---|
| "Are my gels actually working?" | Efficiency score from HR response after intake in last 6 sessions |

---

### Race Preparation

**`race_pace_planner.py`** — Per-km pace strategy

| Say | You get |
|---|---|
| "Give me a FM race pace plan" | Pace target every 5km, HR ceiling per segment, negative split strategy |
| "What pace do I need to run Sub-4 FM?" | Per-km breakdown + HR zone based on current VDOT |

---

**`weather_adjuster.py`** — Adjust pace for race day weather

| Say | You get |
|---|---|
| "Race day is 27°C 80% humidity, how much should I slow down?" | Heat-adjusted pace + HR ceiling + estimated time penalty vs goal |
| "Pull the weather forecast for race day" | Fetches forecast → calculates adjustment automatically |

---

**`taper_monitor.py`** — Monitor pre-race taper

| Say | You get |
|---|---|
| "How is my taper going?" | Mileage reduction %, BB trend, freshness score, readiness verdict |
| "My BB is 65 one week out, is that enough?" | Compare against target taper metrics + recommendations |

---

**`nutrition_calculator.py`** — Race nutrition plan

| Say | You get |
|---|---|
| "Plan my FM race nutrition for 27°C" | Gel + electrolyte schedule per km, total Na mg, % of ACSM ceiling |
| "Pre-run 72kg post-run 70.5kg, drank 500ml — calculate my sweat rate" | Sweat rate L/hr + calibrated Na replacement for race plan |

---

**`fuji_race_pacer.py`** — Hilly course pace strategy

| Say | You get |
|---|---|
| "Race has 900m elevation gain, build a pace plan" | Grade-adjusted pace per segment based on elevation profile |

---

### Health & Recovery

**`hrv_trend.py`** — HRV trend + overtraining warning

| Say | You get |
|---|---|
| "How has my HRV been the last 2 months?" | HRV baseline, weekly trend, deviation from personal norm |
| "Am I at risk of overtraining?" | Overtraining score + list of warning signs if present |

---

**`sleep_correlator.py`** — Sleep → performance correlation

| Say | You get |
|---|---|
| "Does poor sleep affect my running?" | Correlation: sleep score vs HR drift in next-day sessions |
| "Give me a sleep insight" | Summary pattern: sleeping under X hours → HR elevated by Y bpm in quality sessions |

---

**`injury_risk_detector.py`** — Injury risk assessment

| Say | You get |
|---|---|
| "Am I at risk of injury right now?" | ACWR ratio (ATL/CTL), risk level (LOW/MODERATE/HIGH), cause if elevated |
| "Did my load spike in the last 3 weeks?" | EWMA ATL trend + spike detection |

---

**`heat_acclimation.py`** — Heat training load

| Say | You get |
|---|---|
| "Has my body adapted to the heat yet?" | Heat-adjusted TRIMP, acclimation score, weeks until fully adapted |
| "Plan my heat prep for a hot-weather race" | Transition plan: current conditions → race day conditions |

---

### Form & Testing

**`form_tracker.py`** — Cadence/form trend

| Say | You get |
|---|---|
| "Has my cadence improved over the last 3 months?" | Cadence trend, vertical ratio, HR correlation at same pace |

---

**`lt2_analyzer.py`** — LTHR field test analyzer

| Say | You get |
|---|---|
| "Just finished a 30-min TT, analyze my LTHR" | Avg HR in final 10 minutes = LTHR estimate + confidence level |

---

### Session Logging

**`session_logger.py`** — Session log

| Say | You get |
|---|---|
| "Log today's session" | Interactive: asks session type, pace, HR, notes → saves to sessions_master.json |
| "Import all easy runs from the last 3 months" | Batch import easy sessions without manual entry |

---

**`skill_sync.py`** — Sync coach skill file

| Say | You get |
|---|---|
| "Update the coach skill to reflect my latest sessions" | Updates garmin-coach-analyzer.md to reflect latest quality progress |

---

**`stamina_patcher.py`** — Patch stamina data

| Say | You get |
|---|---|
| "Stamina data in my log is incomplete, patch it" | Backfills stamina_drain_pct into sessions that are missing it |

---

### Infrastructure (used by the agent in the background)

| Tool | Role |
|---|---|
| `garmin_client.py` | Garmin Connect client + health cache (2h TTL) |
| `activity_loader.py` | Load activities from file history + live overlay |
| `race_registry.py` | Read races.json → single source of truth for all tools |
| `vdot_math.py` | Jack Daniels VDOT formulas — imported by every tool |
| `treadmill_pace_model.py` | HR→pace interpolation for treadmill sessions |
| `bangkok_climate.py` | Monthly morning temperature by city |
| `garmin_workout_pusher.py` | Push workout plan to Garmin Connect device |
| `tm_patch.py` | Fix session type/pace errors in sessions_master.json |

---

## For Developers

```bash
# Run after any change to tools, config.py, athlete.json, or races.json
.venv/bin/python3.13 GarminRawData/tests/test_suite.py
# 30 checks: UNIT + CONSISTENCY + FUNCTIONAL — exit 0 = all green
```

**Protected files** (a convention for AI coding assistants during a live coaching session — see [CLAUDE.md](CLAUDE.md); human contributors should just open a PR normally):
- `GarminRawData/tools/*.py` (all 35 tools)
- `skills/garmin_coach_mcp/config.py` + `db_helper.py`

CI runs automatically on every push and pull request via GitHub Actions.

Two `requirements.txt` files exist: the **root one is authoritative** (full set, used by CI + setup step 1); `GarminRawData/tools/requirements.txt` is a lighter subset for the tools-only path.

---

## Methodology

- **Jack Daniels' Running Formula** — VDOT system, 5 pace zones (E/M/T/I/R)
- **Karvonen HR zones** — derived from LTHR (Friel 30-min field test)
- **ACWR via EWMA** — injury risk from ATL/CTL ratio
- **ACSM Sawka 2007** — sweat-rate calibrated Na replacement
- Paces and zones update automatically when `athlete.json` changes — no code edits needed

---

## ⚠️ Disclaimer

This project is a **training-planning tool** based on established sports-science principles (Jack Daniels, ACSM, EWMA) — it is **not medical advice**.

- If you experience pain, injury, or anything abnormal during training, stop and consult a doctor or physiotherapist before continuing.
- VDOT/HR zone numbers should be confirmed with an actual race or Time Trial result — don't make health decisions based on estimated values alone.
- You are solely responsible for your own training and racing decisions.

---

## License

[MIT](LICENSE) — free to use, modify, and redistribute, with attribution.

*[อ่านภาษาไทย →](README.md)*
