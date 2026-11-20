# Changelog

รูปแบบอิง [Keep a Changelog](https://keepachangelog.com/) — เรียงจากใหม่ไปเก่า ทุกเวอร์ชันมี git tag คู่กัน (`git tag -l`)

## [1.8.2] — 2026-11-20

### Changed
- **2027 marathon long-run plan aligned with the JD cap (athlete's choice 2026-11-19: keep JD, no longer-than-150-min long runs).** The plan data (`training_plan.json`, local/gitignored; `training_plan.example.json` unchanged) previously asked for 30–32 km long runs that `jd_rules` silently clamped to ~23 km (150 min at E pace, VDOT 40), so the plan displayed numbers that never happened. Now the spec states what is actually run: Marathon Build 20→23 km with the last 8 km @ M (was 5); Race Specific 23 km with the last 11 km @ M (was 8) — within JD's M limits (≤½ of the run, ≤20 % of a 60 km week). Rationale: for a sub-4 target, more marathon-pace volume inside a JD-legal long run is more race-specific than a slow 32 km, and lower risk for the right knee. Base-phase long peak 24→22 km (no real effect — already capped by the 30 %-of-week rule). No tool code changed.

## [1.8.1] — 2026-11-19

Fixes from a full architectural review of v1.8 (all findings reproduced live
before fixing) + the treadmill/outdoor venue label. Every finding has a
regression check (`v181_tests`, 43 checks).

### Fixed — P0 (would have broken the 2027 season)
- **Archived Fuji Marathon (13 Dec) forced 15 days of easy-only from 14 Dec.** `post_race_recovery` counted archived/inactive races. Now only races actually raced count; `races.json → "recovery": false` marks a race run as training (dongtan10 pacer duty).
- **Strength days ignored everything but BB** — day 1 after a marathon, a moderate knee, an HRV crash or RHR +8 still said "GO squat". Now → mobility.
- **JD long-run cap had a cliff at 64 km/wk** (63 km → 18.9 km, 64 km → 16.0 km) and capped the 2027 marathon peak at 17 km. The %-of-week cap is now monotonic, and phases with `long_rule: "marathon"` (2027 Marathon Build / Race Specific) use JD's marathon-plan limit: ≤150 min, no %-of-week cap (≈23 km at VDOT 40) — athlete's choice 2026-11-19. The M-pace finish is now capped by the JD M rule (20 % / 29 km).
- **`garmin_workout_pusher` uploaded "Quality T 3×10min" on a readiness REST day** (REST kept type quality1, no spec → legacy text parser). REST/RACE never build a workout; REST sessions carry no spec. Also: long runs now push the M-pace finish; `stepOrder`/`childStepId` collided after repeat groups; combo reps cut by JD caps were not pushed; `input()` hung non-interactive runs.
- **`post_race_updater` still wrote `vdot_paces_sec`** (would fail the suite on the next `--apply`), kept a 4th pace formula and its own Ely copy, and a hot-race dry-run silently wrote `athlete.json`. Now: `vdot_math.jd_paces`, shared Ely, writes only with `--apply`.
- **Skill file / AGENTS.md contradicted v1.8** (Karvonen-only zones, `daily_brief.readiness_assess`, taper ≤10 d, decoupling 3/8 %, "elliptical not counted"). Synced to v7.1.

### Fixed — P1
- Off-season recovery block reused the taper prescription → T 2×10 min on 8 Dec. New `recovery` phase (quality_max 0) in `training_plan.json`.
- `post_session_analyzer`: easy grade used Z1+Z2 (170 bpm easy run = "A Truly aerobic") → Z1 only; HR ≥ MHR dropped from zones; Garmin watch zones labelled as JD zones; cross-training "counted in PMC" claimed for strength/walk; personal ACL / "HM PB ref 1.76" text moved to data (`injury_watch`, `form_baselines.power_hr_*`).
- `injury_risk_detector`: ACWR silently off whenever day −14 was a rest day (confidence from a 14-day slice); ACWR mapped to no injury site; ACWR could be CRITICAL (now a soft warning).
- `detect_session`: rule 1 never matched (display strings vs lowercase master types).
- `training_planner`: `--chart` still ran the pre-v1.8 dynamic volume; the sessions column was built but never printed.
- `season_summary`: blank injury lines (`site/reason` vs `signal/detail`), hardcoded "Popliteus", `--race` ignored, FM prediction without endurance factor.
- `sleep_correlator`: paired a night's sleep with the run two days later (off by one).
- `weather_adjuster`: HM HR ladder (to LTHR+2) applied to marathons → M-zone ceilings for ≥30 km; sweat Na over the heat-adjusted finish time.
- `race_pace_planner`: aid stations at dist/len (7/14/21 instead of 6/12/18); segment HR drifted into Z4 on a marathon (now `race_hr_plan`); start/finish offsets made a flat 4:00 goal project 4:01; default race = first key (past) → active race. Same default fix in `nutrition_calculator` (+ "HM สั้น" text for any distance).
- `taper_monitor` read evening BB (false AT RISK); `training_load` warned "overreaching" on a CTL **drop**; `heat_acclimation` credited AC-gym treadmill runs as heat exposure, kept its own WBGT table, double-counted the bonus, assumed 100 % retention for any race; `form_tracker` / `lt2_analyzer` still used the pre-v1.8 easy ceiling; `lt2_analyzer` main-block floor 11 km/h hardcoded.

### Fixed — P2/P3
- Readiness: missing HRV (watch off) downgraded quality → CAUTION only. HRV crash streak stitched non-consecutive nights. `ZONE_PCT` rounding flipped HR at a bound into the wrong zone.
- `garmin_client` overwrote good morning sleep/HRV with a later partial fetch's None; `energy_efficiency_scorer` fell back to whole-day BB for `bb_source: unavailable`; `activity_loader` re-hit Garmin per limit, didn't dedupe, non-atomic write; `race_registry` / `training_planner` didn't fall back to example files; `load_athlete_context` didn't refresh nutrition/pain/pace overrides.
- `daily_brief`: run tips/post-run drink on REST days; treadmill tip said "HR" on pace days; "(7-day rolling)" was Mon–today; TSB projection labelled as the all-rest upper bound.
- Session types: Repetition and Marathon Pace keep their identity in the log; weekly report maps tempo → M and keys by activity (two runs a day).
- `run_post_run.sh` crashed on macOS bash 3.2 (`${VAR^^}`) and called `detect_session` (live Garmin) 4×; `run_post_long.sh` didn't sync first; `check_before_push.sh` didn't guard the v1.8 personal files or run unit tests.

### Added
- **Venue label** (`athlete.json → training_venue`): 🏠 ลู่ / 🌳 outdoor on every plan day — easy + E-only long on the treadmill, quality + long with M outdoor (athlete's rule 2026-11-19).
- `athlete.json`: `training_venue`, `injury_watch`, `form_baselines.grade_cadence_*`/`power_hr_*`; `races.json`: per-race `recovery`; `training_plan.json`: `recovery` prescription, phase `long_rule`.
- 43 regression checks → 251 suite checks; 70 unit tests.

### Docs
- Skill file v7.1, AGENTS.md and CLAUDE.md list `training_plan.json`, venue/injury data and the v1.8 rules.

## [1.8] — 2026-11-19

Single sources of truth, Jack Daniels + sport-science rules enforced in code,
and no hardcoded athlete/race/product values in any tool. Tools: 31 → 33.

### Changed (behaviour — read this)
- **E zone follows JD: ceiling = 79 % HRmax (154 at MHR 195), was 163 (%HRR 0.79 = Friel, not JD).** Easy runs averaging 155–163 now count as M in zone reports; weekly "E %" drops accordingly. M zone becomes 154–174.
- **Paces are derived from VDOT** (`vdot_math.jd_paces`: E 59–74 % VO2max, M = VDOT-predicted marathon pace, T 86–88 %, I 97.5–100 %, R = 1500 m–mile pace). The hand-typed `athlete.json → vdot_paces_sec` had drifted (VDOT 40: M 5:16–5:25 vs JD 5:23–5:29, E fast end 5:37 vs 5:51). Optional `pace_overrides_sec` remains.
- **JD per-session limits enforced when planning, warned after the fact** (`jd_rules.py`): T ≤ 10 % of weekly volume (a 20-min tempo always allowed), I ≤ min(8 %, 10 km), R ≤ min(5 %, 8 km), M ≤ min(20 %, 29 km), long run ≤ 25 % (30 % under 64 km/wk) and ≤ 150 min. Reps are cut to fit and the cut is shown; `weekly_load_report` warns when a completed long run broke the limit. The 2026 block had 30–32 km long runs on ~46 km weeks.
- **Taper follows the research (Bosquet 2007, Mujika & Padilla 2003): keep intensity, cut volume.** A-race keeps a short maintenance T until T-5 (was: no quality from T-10); volume cut 40 %/60 %. `taper_monitor` uses the same tier profiles (it had its own table and said "no quality at all" in race week).
- **JD post-race recovery**: one easy day per 3 km raced — quality/long become easy (e.g. 8 days after a HM, 15 after a marathon). Nothing enforced this before.
- **T/I/R prescribed by pace (+ treadmill km/h), HR only as a ceiling for T/M.** HR lags in 3–5 min reps and wrist HR can lock onto cadence.
- **Cardio cross-training counts as training load.** Elliptical/bike/swim/row enter PMC and ACWR (hrTSS is HR-based); cached in gitignored `cross_training_all.json`. CTL rose 35 → 44 once 15 elliptical sessions were counted.
- **ACWR = EWMA 7/28 (Williams 2017)**, the windows its 0.8–1.3/1.5 thresholds come from (was ATL/CTL with a 42-day chronic window). Shown as a soft warning (Impellizzeri 2020).
- **Volume progression check uses JD's rule** (≤ ~1 mile per running session per week, hold 3–4 weeks) instead of the "10 % rule".
- `daily_brief --type` removed (it injected hardcoded T/I workouts).

### Fixed — single sources
- **Season plan moved from `config.py` to `GarminRawData/training_plan.json`** (gitignored; `training_plan.example.json` template): phases with `weekly_km` and workout **specs** (reps/rep_min/rep_km/rest, combo parts), not text with HR/pace typed in. Changing the plan no longer touches protected code.
- **One week computation** (`training_planner.week_plan`): season table, weekly plan and brief used three copies of the volume logic; phase now comes from the plan by date (the week-offset rule is only a fallback), volume from the plan's `weekly_km` (the dynamic fallback had put 70 km/wk on the post-injury base block).
- **One day prescription** (`session_prescriber.prescribe_day`) used by the weekly plan AND the daily brief — the brief had its own `WEEKLY_PLAN`/`build_action_plan` (non-progressive long run, own strides distance). **One readiness engine** (`readiness.py`): the prescriber ignored RHR and used other BB cut-offs; today's BB/HRV/RHR now apply only to today, not to every day of the week.
- One weekly template from `athlete.json` (rest/strength days) instead of hardcoded day maps in two tools.
- One heat model (`bangkok_climate.ely_penalty_fraction`): `race_pace_planner` had its own table (+4.6 % vs +5.2 % at 26 °C); one Bangkok climate table (weather_adjuster had a second copy).
- One activity loader for every tool (10 tools read `running_activities_all.json` directly and missed live, not-yet-synced runs); the live Garmin call is made once per process.
- One nutrition protocol (`config.DAY_NUTRITION`) — two diverging copies in daily_brief/session_prescriber; `weather_adjuster` now shows the athlete's dosing rule (was the per-station solver: 11 caps at 3 stations).
- Race B goals from `races.json → goal_tiers` (were hardcoded Sub 1:55 / Sub 4:15); race locations only from `races.json` (hardcoded Bangsaen/Sponsor21/Fuji coordinates and a Fuji climate table removed); no fallback race in the prescriber (it pointed at Bangsaen42 2026-11-15).
- `config.py`: no personal fallbacks (rhr 44/mhr 195/vdot 40/lthr 183/dates) — falls back to `athlete.example.json`; no product fallbacks (650/90 were the pre-2026-08-02 wrong label reading — would overstate sodium 4×). Duplicate `calculate_paces_from_vdot` removed.

### Fixed — hardcode moved to data
- `athlete.json`: `lt2_test.tm_hr_anchors` (treadmill HR↔pace, was in `treadmill_pace_model.py`), `form_baselines` (incl. grade cadence floors that were typed into `grade_session`), `sweat_na_mmol` (was 45 in code), `session_nutrition` (post-run/carb-load/coffee text), `strength_routine`, product display names.
- `garmin_workout_pusher`: all paces from VDOT (warm-up "7:00–6:30", strides "5:30–5:00", LT2 test "5:20–5:05", VDOT-40 fallbacks); quality workouts built from the session's structured `spec` instead of regex on text.
- `race_pace_planner` HR guide from zones (was "HR < 175", "178"); `hrv_trend` advice uses the E ceiling (was "HR < 140"); `post_session_analyzer` VDOT-40 pace fallbacks removed; `session_logger --batch-easy` default = last 90 days (was 2026-03-26).

### Fixed — bugs
- `post_session_analyzer --id <elliptical>`: date "custom", wellness fetch errors, running metrics of 0 and an attempted run-log write. Non-running activities now get a cross-training report (duration, HR, zones, hrTSS, TE) and are never written to the running log.
- `training_planner` marked every week after race day as "🏁 RACE".
- Taper volume cut twice inside a taper phase (plan volume already cut) — 3 km easy runs.

### Added
- `jd_rules.py`, `readiness.py` (protected tools 31 → 33); `training_plan.example.json`.
- 33 test_suite checks incl. an AST scan that fails on literal HR/pace/race/product/date values in tool code; CI fixtures include `training_plan.json`; unit tests for phases are data-driven (were pinned to 2026 dates). 208 checks / 69 unit tests.
- `GarminRawData/fetch_partner_wellness.py` — raw wellness fetch (BB/HRV/RHR/sleep/stress) for a second Garmin account, fully separate from the athlete pipeline: own creds file (`~/.config/garmin-coach/.env.partner`), own tokenstore, output to gitignored `wellness_partner/`. No coaching logic.

## [1.7.2] — 2026-11-19

Readiness reads last night's HRV and a rolling RHR baseline instead of two
lagging/static numbers.

### Fixed
- **Readiness used Garmin's 7-day HRV status as if it were today's.** `hrv_status` judges the 7-day *average*, so after a race it stays UNBALANCED for days after the body has recovered (2026-11-19: last night 94 ms inside the 85–121 range, weekly avg 79 → "UNBALANCED"). daily_brief downgraded the day and hrv_trend fired "HRV CRASH 3 days" for what was one marathon counted three times. The wellness master now stores last night's HRV + the personal balanced range (`hrv_last_night`, `hrv_weekly_avg`, `hrv_balanced_low/high`); readiness (daily_brief, session_prescriber) and the crash streak use last night vs range, and fall back to the status only when the numbers are missing. The 7-day status is still shown as the trend.
- **Readiness RHR baseline was athlete.json `rhr` (44).** That value is the Karvonen input for HR zones and sits near the yearly minimum; a normal morning is ~48–50, so ordinary days read "+4/+5". daily_brief and hrv_trend now share `hrv_trend.rhr_baseline()` = median morning RHR of the 28 days before today (today excluded; falls back to athlete.json with <7 nights). HR zones still use athlete.json `rhr` — unchanged.
- `season_summary` "HRV today" shows last night's value next to the 7-day status.

### Added
- `GarminRawData/backfill_hrv_nightly.py` — one-off, fills the nightly HRV fields into existing wellness files (run for the last 35 days).
- 11 test_suite checks (`v172_tests`) — 172 total.

## [1.7.1] — 2026-11-18

Bug-fix round after v1.7: one session log, taper by race tier, nutrition that
follows the athlete's own dosing rule. Tools: 32 → 31.

### Fixed
- **`nutrition_calculator.py` contradicted the athlete's dosing rule.** The solver spreads all Prevo caps over the water stations, so an HM plan said 2–3 caps at once at km 6/12/18 — the athlete's rule (what worked in training) is ONE cap every 4 km + one gel every 6 km. New schedule mode reads `athlete.json → race_day_nutrition.in_race_schedule`, places each dose at its km (none in the last `skip_last_km`), and only *verifies* total Na vs ACSM 40–80% + carbs g/hr. It is the default when the rule exists; `--mode stations` keeps the old solver. The "<40% is fine" verdict now depends on duration (≤2 h) instead of always assuming an HM.
- **`session_prescriber.py` gave every race the A-race taper.** A B tune-up or C race also got a 14-day window, no quality ≤10 days and long ≤12 km — two lost weeks of quality. Taper now comes from the race tier (A 14/10 d, B 7/3 d, C 3/2 d; optional `races.json → taper_days` override) and is evaluated per day, not from Monday for the whole week (a Thursday quality 10 days before an A race used to survive because Monday was T-13).
- **Two session logs drifted apart.** `post_session_analyzer --update-log` wrote `sessions.json` while the lap log was `sessions_master.json`, and `tm_patch.py` had to copy type/treadmill/pace corrections across after every run. They had already diverged (a 2026-08-11 threshold existed only in sessions.json, a 2026-06-16 one only in master). Now the graded result is stored on the master record (`analysis` block, `session_logger.save_analysis`), and `session_logger.load_quality_log()` rebuilds the old row shape on read with the treadmill-pace correction (belt speed > HR-inferred laps > HR-inferred avg). race_predictor, skill_sync, injury_risk_detector, weekly_load_report and energy_efficiency_scorer give byte-identical output on the migrated data. The analyzer now logs laps first, then attaches the analysis; if lap logging is skipped, a `_stub` record holds the analysis and a later lap log upgrades it in place.
- Treadmill pace for 2026-06-14 corrected 6:22 → 6:44/km: tm_patch only looked for belt phases on the master record, where they were missing, and fell back to an HR guess (belt was 8.6–9.0 km/h).
- **`weekly_load_report.py` printed "E:101%".** Zone km (unrounded) were divided by the 0.1-rounded weekly total.
- **`season_summary.py` "last 4 weeks" skipped weeks with no runs**, so an injury week vanished and the 4 rows spanned 6 weeks. It now reuses `weekly_load_report.analyze_week` (one weekly-volume calculation).
- 4 old sessions whose run-window Body Battery Garmin no longer has (one stored a sync-time "69 → 5") are marked `bb_source: "unavailable"` with empty values instead of misleading numbers; the quality-log view never falls back to the old copy.

### Removed
- **`tm_patch.py`** — see the single-log fix. Pipelines (`run_post_quality.sh`, `run_post_long.sh`) drop the step; step counters fixed (quality printed "Step 4/4" of 3).

### Changed
- One-off `GarminRawData/migrate_single_log.py` folded `sessions.json` into the master log (backups `*.pre-v1.7.1.bak`, old file kept as `sessions.json.migrated`, all gitignored).
- `backfill_session_metrics.py` marks unrecoverable BB as unavailable instead of skipping forever.
- `config.NUTRITION` exposes `in_race_schedule` and gel carbs; `athlete.example.json` shows the optional rule.
- flow.html, CLAUDE.md (31 tools, 161 checks, single log), README/README_EN (tool table, nutrition row, badge 1.7.1).

### Prevention
- test_suite section 9 (21 checks): tier windows ordered; B-race keeps quality at T-5 and drops it at T-3; C-race doesn't taper at T-5; `taper_days` override; HM dosing = Prevo 4/8/12/16 + gel 6/12/18, never >1 cap at once, Na total matches labels, nothing in the last 2 km; stub → full record keeps analysis; belt-speed TM pace; master BB beats the analysis copy; duplicate analysis refused; "unavailable" BB never falls back; no tool opens `sessions.json`; zone km sum = unrounded volume; season_summary uses `analyze_week`. `tm_patch.py` added to the stays-removed list. 161 checks; passes with no personal logs (CI).

## [1.7] — 2026-11-18

Tool consolidation: 36 → 32 tools. Principle: one question = one tool, and
records are written right at log time instead of patched afterwards.

### Removed
- **`fuji_race_pacer.py`** — Fuji archived; hilly-course pacing is `race_pace_planner.py --elevation course.csv` (same Minetti grade model).
- **`daily_aggregator.py`** — never wired in; `training_load` / `weekly_load_report` / `season_summary` already sum multi-session days via `activity_loader`.
- **`stamina_patcher.py`** and **`fix_session_bb.py`** (post-run patchers) — see Fixed.

### Fixed
- **Session records stored the wrong Body Battery until a patcher overwrote them.** `session_logger._build_record` saved `bb_start` = the day's peak and `bb_end` = BB at sync time (an afternoon 25 km read "89 → 17, drop 72"; Garmin's figure was −27), so every pipeline ran `fix_session_bb.py` + `stamina_patcher.py` afterwards. Both values are in the activity-details dict already: new `session_logger.run_window_bb()` (activity `differenceBodyBattery` + 3-min stress timeline) and `stamina_from_details()` fill them at log time, stamped with `bb_source`. Verified identical to the patched values on 5 cached sessions. With no drop info the end value stays empty instead of being fabricated.
- One-off `GarminRawData/backfill_session_metrics.py` (replaces both patchers, not in the pipeline) backfilled stamina for 42 older records that the patcher had missed (no cache at patch time); 122/122 now have stamina.

### Not changed (deliberately)
- `tm_patch.py` stays: it syncs user-confirmed type / treadmill pace from `sessions_master.json` into `sessions.json`. Removing it properly means retiring the two-log design (sessions.json vs sessions_master.json) — a larger change for a later version.

### Changed
- **`vdot_estimator.py` merged into `race_predictor.py` and removed (35 tools).** Two tools answered "what's my VDOT?" with different methods and contradictory results on the same day (race_predictor: training estimate +1.1; vdot_estimator: "−8.9 gap" from a top-3 pace-only average). A pace-only average of *training* sessions systematically under-reads (training pace < race effort), so it was dropped. Its one useful part — the **breakthrough flag** (an outdoor quality session whose heat-adjusted quality-lap pace alone implies VDOT ≥ confirmed + 1) — now lives in `race_predictor.find_breakthroughs()` as a list of sessions, not a second number. Interval reps get the same +15 s/km continuous-effort equivalent as the estimator (a 4:16 5×1 km had read as "VDOT 46"), and results beyond the ±5 plausibility band are dropped as artefacts.
- `race_predictor.py --json` (human report → stderr): confirmed VDOT, training estimate + staleness, sessions used, predictions (`fm_vdot_only` / `fm_personal`), goals, breakthroughs — for agents to narrate.
- `run_post_quality.sh` / `run_post_race.sh` call `race_predictor.py`; skill file, README, CLAUDE.md, flow.html updated.

### Prevention
- test_suite: vdot_estimator must stay merged (file absent, no script references); breakthrough pace-VDOT must lie within confirmed +1…+5; `--json` carries the required keys.
- test_suite: removed patchers/duplicates stay removed; run-window BB uses the activity drop and never fabricates an end value; stamina parsing. 140 checks.

### Fixed
- **race_predictor FM goal VDOT ignored the endurance factor** — the "need VDOT X" line used `compute_vdot(42195, goal)` (pure prediction) while the gap beside it used the personal (× `fm_endurance_factor`) prediction, printing e.g. "ยังขาด 22:41 | ต้องการ VDOT 37.9 (gap +-3.2)" for sub-4 at VDOT 41 — a goal that looked already met. New `fm_goal_requirements()` solves for goal ÷ factor, and also reports the endurance factor that would hit the goal at the current VDOT. HM/FM gap now formats with sign (`+4.7`, not `+-3.2`).
- **training_planner plan start picked the first base phase in the whole list** (May 2026) — with a 2027 season added, a Sep-2027 race would be planned from week 70+. Now uses the first base phase within 52 weeks before the race (ATM21 week numbering unchanged: wk 27).

### Added
- `TRAINING_PHASES`: 2027 season (Base Rebuild → Quality Development → Marathon Build → Race Specific → Taper) toward a placeholder sub-4 FM; post-ATM recovery shortened to end 13 Dec. Phases after Feb are provisional until a real race is chosen.

### Prevention
- test_suite (v1.7 pre-merge): 2 plausibility checks for FM goal requirements (VDOT needed must rise with factor; factor needed lies between 1.0 and current). Phase check "race_specific does not cover race day" now only looks at the active race's own season, so a future season's block is not a false positive. 133 checks.

## [1.6] — 2026-11-17

Theme: **reduce coach-agent hallucination by moving judgment and arithmetic into tested code** — every recurring agent mistake this cycle traced back to the agent computing or remembering something a tool should own.

### Fixed
- **Readiness returned on the first matching rule** — mild knee pain produced "Easy 6–8 km" while RHR was +11 over baseline and HRV was unbalanced (both stricter). `daily_brief.readiness_assess()` now lets every rule vote: REST > MODIFY (smallest distance cap) > CAUTION > GO, and prints the other triggered reasons as `↳ ร่วมด้วย`. `readiness_decision()` kept as a back-compat wrapper.
- **A downgraded long-run day still printed a full 12–16 km long run** — `build_action_plan` only honoured MODIFY on quality days and never applied caps. Any MODIFY running day → Easy, capped by the strictest rule.
- **23 rows of personal training data were committed in `skills/garmin-coach-analyzer.md`** (skill_sync appended them after the v1.x sanitize). `skill_sync.py` now writes to gitignored `GarminRawData/session_table.md`; rows removed from the public skill file.

### Added
- **`form_analysis.py`** (new tool, pure functions): per-km pace/HR/cadence/GCT/VR/stride, first-vs-last-third form drift with fatigue flags, and a wrist-HR **cadence-lock** check (share of |HR−cadence| ≤2 samples + Pearson r). Previously the agent wrote a throw-away script for this on every "post run analyze".
- `post_session_analyzer.py --belt-kmh X` (treadmill: belt pace replaces unreliable watch pace in the form table) and `--json` (machine-readable summary to stdout, human report to stderr; pace emitted as `m:ss` strings with source labels, never raw decimals).
- `run_post_easy.sh`: `BELT_KMH=9.0 bash run_post_easy.sh` passthrough.
- `athlete.json → gear` (roles, first-use date, mileage estimate, explicit `unconfirmed` list) — not committed (personal).

### Changed
- **`skills/garmin-coach-analyzer.md` v7.0: 45 KB → ~11 KB.** Rules only. Removed every personal or mutable fact (product names, injury-specific advice, paces, readiness table) that had drifted from `athlete.json`. Added a "code decides, agent narrates" golden rule: numbers only from tools, no ad-hoc analysis scripts, unknown → ask.

### Prevention
- 11 new checks (131 total): strictest-rule-wins, REST beats MODIFY, long-day downgrade, cap applied, and form_analysis behaviour on synthetic streams (steady vs fatiguing GCT, locked vs free HR, belt override).

---

## [1.5] — 2026-11-17

Full-project review (sports-science / data / agent) → fixes below. Root theme: the suite was 107/107 green while a core output was physiologically absurd — tests checked formula *consistency*, not output *plausibility*.

### Fixed
- **`race_predictor.py` reported training-estimate VDOT 33.3 for a VDOT-40 athlete** — an I-session's `pace` field is the whole-activity average (WU + jog recoveries + CD, e.g. 6:21/km), which was treated as 5K race pace → single-session estimate 27.5; the 4:2:1 recency weighting over only 3 sessions let that one artefact dominate. Now: uses `rep_pace`/`quality_pace` when logged, and rejects any single-session estimate > ±5 VDOT from the race-confirmed value (printed, not silent).
- **T-session estimator could only go UP** (`max(0, …)`) — biased by construction. Now symmetric when HR is above T-mid at a pace not faster than T pace; HR-only inference capped at ±3 VDOT.
- **Contradictory label** "Fitness ใกล้เคียง baseline" printed for a −6.7 gap → tri-state (up / near / below) with the delta shown.
- **Finished races kept appearing as goals** (Sponsor21 sub-1:52, Bangsaen sub-4) — goal lookup now takes only *upcoming* active races, nearest first; no FM goal section when none is upcoming.
- **hrTSS anchored at the T-zone floor (174) instead of LTHR (183)** — every TSS was inflated ×((LTHR−RHR)/(174−RHR))² ≈ +14%, putting CTL/ATL on a private scale not comparable with hrTSS norms. `T_HR = ATHLETE["lthr"]`. Trends unchanged; absolute CTL drops ~12–13%.
- **Readiness ignored resting HR** — `daily_brief.readiness_decision()` used only Body Battery + HRV status (BB is itself derived from HRV/stress → one channel counted twice). Added RHR vs `athlete.json` baseline: ≥ +5 bpm → no quality/long; ≥ +8 bpm → easy ≤40 min or rest. Brief shows the delta next to RHR.
- **7 unit tests in `tools/tests/test_tools.py` had silently rotted** (hardcoded VDOT 38, T-zone 170, old phase dates) because that file never ran in CI → now derive from config.

### Added
- `athlete.json` → `fm_endurance_factor` (actual FM / VDOT-predicted FM; read via `config.ATHLETE`). Pure Daniels VDOT assumes full marathon endurance; `race_predictor` now prints both the raw and the personal-calibrated FM prediction.
- `athlete.json` → `coaching_constraints` (list) — standing athlete preferences printed at the end of every daily brief so an LLM coach can't drift from them.
- `race_predictor`: STALE warning when the newest usable quality session is > 28 days old.
- `fix_session_bb.py` committed (was untracked but already called by `run_post_easy.sh` / `run_post_quality.sh` — would have broken fresh clones).
- `garmin_workout_pusher.py --plan-file PATH` — push an arbitrary agreed plan instead of the prescriber's default.
- `config.TRAINING_PHASES`: phase split so taper ends on the A-race date, + ATM21 recovery phase.

### Prevention
- **New PLAUSIBILITY section in `test_suite.py`** (13 checks → 120 total): artefact session can't drag the estimate outside the band, estimator not up-only, ±3 cap, goals are upcoming, hrTSS IF=1 at LTHR, RHR-based downgrades, endurance-factor bounds.
- **CI now also runs `tools/tests/test_tools.py`** (72 unit tests).

---

## [1.4] — 2026-09-09

### Fixed
- **Python <3.10 compat**: `str | None` (PEP 604 union syntax) ทำให้ทุก tool crash ทันทีที่ import บน Python เก่ากว่า 3.10 (เช่น macOS default `python3` = 3.9.6) — เพิ่ม `from __future__ import annotations` ใน 20 ไฟล์
- **Hardcoded-archived-race "zombie fallback"** หลุดอยู่ใน 8 tools (`training_load.py`, `season_summary.py`, `weather_adjuster.py`, `heat_acclimation.py`, `taper_monitor.py`, `race_pace_planner.py`, `training_planner.py`, `session_prescriber.py`) — exception fallback เคย hardcode ชื่อ/วันที่ race ที่ archived ไปแล้ว (ATM Bangkok Marathon, Fuji) แทนที่จะอ่านจาก `races.json` จริง แก้ให้ fail ดังๆ แทนการเงียบๆ โชว์ race ผิด
- **Nutrition SSOT drift**: `daily_brief.py` hardcode "iRun 1 เม็ด" ไม่ตรงกับ `athlete.json` — เปลี่ยนเป็นอ่าน dynamic
- **Injury-risk key mismatch**: `daily_brief.py` อ่าน `site`/`reason` แต่ `injury_risk_detector.py` คืนค่าเป็น `signal`/`detail` — ทำให้ risk warning โชว์เป็นบรรทัดว่างเปล่า
- **False CRITICAL บน cold-start**: ACWR check ใน `injury_risk_detector.py` ไม่เช็ค confidence ของ CTL/TSB ก่อน ทำให้ athlete ที่เพิ่ง sync ข้อมูล 2 วันได้ CRITICAL ปลอม
- **Multi-user LTHR bug**: `config.py`'s `load_athlete_context()` (DB-loaded profile path) hardcode T-zone ratio ที่ 0.921 แทนที่จะ derive จาก LTHR จริงของ profile
- **Headless/agent hang**: `session_logger.py` เรียก `input()` แบบไม่เช็ค `isatty()` — ทำให้รันผ่าน AI agent (ไม่มี TTY) ค้างหรือ silent-skip
- `garmin_workout_pusher.py`: เพิ่ม `--no-warmup` (ตัด warm-up jog + stride wake-up ออกจาก Quality workout ที่ push เข้า Garmin สำหรับคนที่อุ่นเครื่องเอง)
- `race_pace_planner.py`: ทำความสะอาด `_default_race` ternary ที่ไม่จำเป็น (RACES filter active-only อยู่แล้ว)

### Docs
- README/README_EN: แก้ hardcode `python3.13` → generic `python3` + Python 3.10+ requirement note, เตือน macOS default python3 = 3.9 พร้อม fix
- README/README_EN: เพิ่ม step ใหม่ — first-time history backfill (`fetch_incremental.py --lookback-days 90`) และ 2FA/MFA setup note (`--mfa` flag ที่เพิ่มใน v1.3 แต่ยังไม่เคย document)
- README/README_EN: แก้ path ที่ `cd`-dependent ใน onboarding step ที่ทำให้คำสั่งถัดไปหาไฟล์ไม่เจอถ้า copy-paste ตามลำดับ
- README/README_EN: ลบ `PYTHONPATH=skills/garmin_coach_mcp` ที่ไม่จำเป็นแล้วออกจาก CLAUDE.md และตาราง Compatible AI Agents (โค้ด resolve path เองแล้ว)
- README/README_EN: เพิ่ม link ไปยัง `TRAINING_PLAN_METHODOLOGY.md`
- `skills/garmin-coach-analyzer.md`: แก้ตัวอย่างคำสั่ง `--race hm` (invalid, races.json ไม่ active) → `--race sponsor21`, เพิ่ม `--update-log` ในตัวอย่าง, sync nutrition product text ให้ตรง `athlete.json`
- `skill_test_cases.md`: เพิ่มคำเตือนว่า pace/HR ในไฟล์เป็นค่าตัวอย่างเฉพาะ athlete ต้นฉบับ

### Prevention
- เพิ่ม **STATIC source-scan test ใหม่** ใน `test_suite.py` — scan ทุก tool หา "zombie fallback" pattern (`"atm":`, `date(2026,11,29)`, `FUJI MARATHON RACE PLAN` ฯลฯ) ป้องกันไม่ให้บั๊กคลาสนี้ถูก copy-paste กลับเข้ามาโดยไม่มีใครรู้ (113/113 checks, จาก 112)

---

## [1.3] — 2026-09-04

### Fixed
- **MFA/2FA login support**: `garmin_client.py` เพิ่ม `--mfa <code>` สำหรับ one-time interactive setup เมื่อบัญชี Garmin เปิด 2FA
- **Cold-start warnings**: `training_load.py` เพิ่ม `pmc_confidence()` เตือนเมื่อ CTL/TSB ยังไม่น่าเชื่อถือ (ประวัติ <14/42 วัน) — `daily_brief.py` แสดง warning นี้ในรายงาน
- **Stale-data warning**: `daily_brief.py` เช็ค mtime ของ `running_activities_all.json` เตือนถ้า sync ค้างเกิน 1 วัน
- Deployment caveats: เพิ่มคำเตือน Cloud VM/VPS (รวม Colab) อาจโดน Garmin Cloudflare WAF บล็อก IP

---

## [1.2] — 2026-09-04

### Fixed
- **Round-2 UAT multi-runner compat**: ลบ hardcode ACL/อาการบาดเจ็บเฉพาะบุคคลออกจาก `session_prescriber.py`, แก้ VDOT/race-name test coupling, LTHR math trap, taper/weekly-report false positives
- Sanitize `skills/garmin-coach-analyzer.md` — ลบข้อมูลส่วนตัว/ประวัติซ้อมจริงออกจาก skill prompt
- เพิ่ม `AGENTS.md` (pointer file สำหรับ AI agent ที่ไม่อ่าน `CLAUDE.md`) + Firm Refusal Protocol

---

## [1.1] — 2026-09-04

### Added
- `README_EN.md` (English README)
- Dynamic `training_planner.py` (คำนวณ weekly km จาก training history จริง แทน hardcode table)
- Energy efficiency auto-calc + workout-push safety gate

### Fixed
- `weather_adjuster.py` hardcoded HM distance (21.097km) — ตอนนี้ scale ตาม `dist_km` จริงของ race ใดก็ได้
- `session_prescriber.py` hardcoded injury text
- `hrv_trend.py` crash เมื่อ wellness record มี field เป็น null
- BB readiness fallback, cadence/lap-grouping bugs
- ลบชื่อจริง/username ของเจ้าของโปรเจกต์ที่หลุดอยู่ใน public files
- ลบโปรเจกต์ส่วนตัวอื่น (ไม่เกี่ยวกับการวิ่ง) ที่หลุดเข้ามาใน public repo
- Onboarding prompt ครอบคลุมทุก field ใน `athlete.json` (ไม่ใช่แค่ 6 field แรก)

### Changed
- Migrate health cache ไปที่ `GarminRawData/wellness/` (single source)
- README ปรับให้เข้าใจง่ายสำหรับคนที่ไม่ใช่สาย tech

---

## [1.0] — 2026-06-15

Initial public release — Garmin-connected Jack Daniels running coach agent (35 tools, `athlete.json`/`races.json` single-source-of-truth architecture, test suite).
