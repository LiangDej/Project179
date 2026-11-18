# Changelog

รูปแบบอิง [Keep a Changelog](https://keepachangelog.com/) — เรียงจากใหม่ไปเก่า ทุกเวอร์ชันมี git tag คู่กัน (`git tag -l`)

## [Unreleased]

### Fixed
- **race_predictor FM goal VDOT ignored the endurance factor** — the "need VDOT X" line used `compute_vdot(42195, goal)` (pure prediction) while the gap beside it used the personal (× `fm_endurance_factor`) prediction, printing e.g. "ยังขาด 22:41 | ต้องการ VDOT 37.9 (gap +-3.2)" for sub-4 at VDOT 41 — a goal that looked already met. New `fm_goal_requirements()` solves for goal ÷ factor, and also reports the endurance factor that would hit the goal at the current VDOT. HM/FM gap now formats with sign (`+4.7`, not `+-3.2`).
- **training_planner plan start picked the first base phase in the whole list** (May 2026) — with a 2027 season added, a Sep-2027 race would be planned from week 70+. Now uses the first base phase within 52 weeks before the race (ATM21 week numbering unchanged: wk 27).

### Added
- `TRAINING_PHASES`: 2027 season (Base Rebuild → Quality Development → Marathon Build → Race Specific → Taper) toward a placeholder sub-4 FM; post-ATM recovery shortened to end 13 Dec. Phases after Feb are provisional until a real race is chosen.

### Prevention
- test_suite: 2 plausibility checks for FM goal requirements (VDOT needed must rise with factor; factor needed lies between 1.0 and current). Phase check "race_specific does not cover race day" now only looks at the active race's own season, so a future season's block is not a false positive. 133 checks.

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
