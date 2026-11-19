# Changelog

รูปแบบอิง [Keep a Changelog](https://keepachangelog.com/) — เรียงจากใหม่ไปเก่า ทุกเวอร์ชันมี git tag คู่กัน (`git tag -l`)

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
