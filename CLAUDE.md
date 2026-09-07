# PROJECT 179 — Cowork & Claude Code Instructions

> **Note for human contributors:** the section below is written for *AI coding
> assistants* (Claude Code, Cowork, etc.) reading this file during a live
> coaching session — it tells the assistant to ask before touching tool code,
> so a chat request like "fix my pace" can't accidentally rewrite `daily_brief.py`.
> It is **not** a rule against human pull requests. If you're a person wanting
> to fix a bug or add a tool, go ahead and open a PR normally — this file just
> keeps an AI assistant from doing it unsupervised mid-conversation.

## ⚠️ FILE PROTECTION — READ BEFORE ANY ACTION (applies to AI assistants)

**ALL Python files in `GarminRawData/tools/` are PROTECTED**, plus the MCP server/config. These are **managed by the system architect** and must **NEVER be modified, overwritten, or recreated** during coaching sessions — including any *new* tool added to the directory in the future.

```
GarminRawData/tools/**/*.py     ← EVERY tool in this directory (current + future)
skills/garmin_coach_mcp/config.py
skills/garmin_coach_mcp/db_helper.py
```

**Rule of thumb: EVERY `*.py` in `GarminRawData/tools/` is protected — current (35) + any added later.**
Full enumeration (35):

```
GarminRawData/tools/daily_brief.py
GarminRawData/tools/post_session_analyzer.py
GarminRawData/tools/training_load.py
GarminRawData/tools/race_predictor.py
GarminRawData/tools/weekly_load_report.py
GarminRawData/tools/taper_monitor.py
GarminRawData/tools/session_prescriber.py
GarminRawData/tools/injury_risk_detector.py
GarminRawData/tools/race_pace_planner.py
GarminRawData/tools/vdot_estimator.py
GarminRawData/tools/energy_efficiency_scorer.py
GarminRawData/tools/garmin_client.py
GarminRawData/tools/session_logger.py
GarminRawData/tools/nutrition_calculator.py
GarminRawData/tools/fuji_race_pacer.py
GarminRawData/tools/training_planner.py
GarminRawData/tools/weather_adjuster.py
GarminRawData/tools/heat_acclimation.py
GarminRawData/tools/season_summary.py
GarminRawData/tools/hrv_trend.py
GarminRawData/tools/sleep_correlator.py
GarminRawData/tools/detect_session.py
GarminRawData/tools/vdot_math.py
GarminRawData/tools/treadmill_pace_model.py
GarminRawData/tools/tm_patch.py
GarminRawData/tools/stamina_patcher.py
GarminRawData/tools/skill_sync.py
GarminRawData/tools/post_race_updater.py
GarminRawData/tools/garmin_workout_pusher.py
GarminRawData/tools/activity_loader.py        # shared file+live overlay loader
GarminRawData/tools/lt2_analyzer.py            # Friel LTHR field test
GarminRawData/tools/form_tracker.py            # cadence/VR trend + HR correlation
GarminRawData/tools/daily_aggregator.py        # same-day multi-session merge
GarminRawData/tools/race_registry.py           # single-source race loader (reads races.json)
GarminRawData/tools/bangkok_climate.py         # month-aware morning temp (Ely heat-adj)
skills/garmin_coach_mcp/config.py
skills/garmin_coach_mcp/db_helper.py
```

## 📌 SINGLE SOURCES OF TRUTH — แก้ค่าที่ data file (ไม่ใช่ protected Python)

| ค่าที่เปลี่ยนได้ | แก้ที่ (data, NOT protected) | tools อ่านผ่าน |
|---|---|---|
| **VDOT, LTHR, RHR, MHR, weight, paces, HR-zone %, nutrition products** | `GarminRawData/athlete.json` | `config.py` (derive zones/paces) |
| **Race targets (date/dist/goal/stations/course)** | `GarminRawData/races.json` | `race_registry.py` |

หลัง TT/แข่งใหม่ → แก้ `athlete.json` ที่เดียว (หรือรัน `post_race_updater.py`) → ทุก tool ตามทันที ไม่ต้อง review โค้ด.
`python3 race_registry.py --set-active <key>` สลับ A-race.

**If asked to run, test, or analyze output from any of these tools → run them via bash, do NOT edit the source.**

**If you believe one of the protected files above (the 35 tools, `config.py`, or `db_helper.py`) needs updating → STOP and tell the user what change you'd like to make, then wait for explicit approval.**

**⚠️ This approval requirement applies ONLY to the protected files listed above. It does NOT apply to `athlete.json`, `races.json`, or any other data file — those are meant to be edited freely, with no confirmation needed.** This matters most during first-time setup: when a user runs the onboarding flow (copying `athlete.example.json`/`races.example.json` to `athlete.json`/`races.json` and filling in their values), just write the files directly — do not ask for permission first, and do not treat this as "modifying a protected file." That's the entire point of the single-source-of-truth design: `athlete.json`/`races.json` are plain data, not code, and editing them is the *normal, expected, everyday* way this project is configured.

---

## Project Overview

PROJECT 179 — Garmin-connected Jack Daniels running coach agent.

- **Athlete**: configured in `GarminRawData/athlete.json` (VDOT, LTHR, HR zones, paces, nutrition)
- **A-race**: configured in `GarminRawData/races.json` (see `races.example.json` for template)
- **Stack**: Python tools + FastMCP server + Garmin Connect API
- **Skill file**: `skills/garmin-coach-analyzer.md`
- **Config**: `config.py` derives paces/HR-zones from `athlete.json` (the editable single source). Edit athlete.json, not config.py.

## ✅ Test Suite — รันหลังแก้ tool/config/data ทุกครั้ง

```bash
.venv/bin/python3.13 GarminRawData/tests/test_suite.py   # 30 checks, exit 0 = ผ่าน
```
3 ชั้น: **UNIT** (สูตรแต่ละ tool) + **CONSISTENCY** (single-source invariants — รวม STATIC source-scan ที่จับ stale literal ทุก branch เช่น HR<155/170–176/VDOT38) + **FUNCTIONAL** (รัน flow จริง เทียบ output กับ config). จับ regression class: race-pace≠vdot_math, nutrition>80%, _classify_speed, ACWR, Fuji leak, Easy-ceiling≠config. แก้ค่าใน athlete.json/races.json แล้วรัน suite ต้องเขียวก่อนถือว่าเสร็จ.

## Daily Bash Scripts (use these — don't run tools manually)

```bash
cd GarminRawData/tools
bash run_morning.sh                  # ทุกเช้าก่อนวิ่ง → daily_brief
bash run_post_easy.sh                # หลังวิ่ง Easy → post_session_analyzer (auto)
bash run_post_easy.sh [activity_id]  # ระบุ ID เฉพาะ
bash run_post_quality.sh             # หลังวิ่ง Quality → analyze + VDOT + skill_sync
```

## Running Tools (individual)

> ⚠️ **ใช้ `.venv/bin/python3.13` เสมอ — ห้าม system `python3`.**
> System python ไม่มี Garmin library → fetch Body Battery/HRV fail → ไปประเมินจากความรู้สึกแทน + PMC คำนวณจาก data ไม่ครบ → **TSB/ตัวเลขเพี้ยน** (เคยเจอ +14.4 แทน +6.5). ตัวเลขที่เพี้ยนแบบนี้ถ้าหลุดไปคลิป/อัดช่อง = ผิดต่อหน้าคนดู.
> รันให้ถูก (จาก `GarminRawData/tools/`):
> ```bash
> ../../.venv/bin/python3.13 daily_brief.py
> ```
> ถ้า output ขึ้น "ดึง Garmin ไม่ได้/ติด library" หรือ "[cache]" ทั้งที่ควร live → แปลว่ารันผิด interpreter, อย่าเชื่อตัวเลข ให้รันใหม่ด้วย venv.

All tools live in `GarminRawData/tools/`. Run from that directory:

```bash
cd GarminRawData/tools
../../.venv/bin/python3.13 daily_brief.py
../../.venv/bin/python3.13 post_session_analyzer.py --latest
../../.venv/bin/python3.13 session_logger.py --summary
../../.venv/bin/python3.13 race_pace_planner.py --race sponsor21
../../.venv/bin/python3.13 taper_monitor.py --race sponsor21 --bb 72
../../.venv/bin/python3.13 session_prescriber.py --week current
../../.venv/bin/python3.13 weekly_load_report.py
../../.venv/bin/python3.13 injury_risk_detector.py --json
../../.venv/bin/python3.13 vdot_estimator.py
../../.venv/bin/python3.13 energy_efficiency_scorer.py
# Analysis tools:
../../.venv/bin/python3.13 weather_adjuster.py --race sponsor21 --manual --temp 27 --humidity 82 --wind 1.5 --dew 21 --base-pace 5:07
../../.venv/bin/python3.13 hrv_trend.py --days 60
../../.venv/bin/python3.13 sleep_correlator.py --insight
../../.venv/bin/python3.13 training_planner.py --race bangsaen
../../.venv/bin/python3.13 nutrition_calculator.py --race sponsor21 --temp 27 --humidity 82 --duration 115
../../.venv/bin/python3.13 nutrition_calculator.py --calibrate --pre_weight 72.0 --post_weight 70.5 --fluid_ml 500 --duration 60
```

## Data Files

- `GarminRawData/athlete.json` — ⭐ SINGLE SOURCE: VDOT/LTHR/RHR/MHR/weight/paces/HR-zone%/nutrition (config.py reads this)
- `GarminRawData/races.json` — ⭐ SINGLE SOURCE: race targets (race_registry.py reads this)
- `GarminRawData/running_activities_all.json` — synced activity history (all runs)
- `GarminRawData/QualitySessionLog/sessions.json` — quality session log (aggregate, used by race_predictor/skill_sync)
- `GarminRawData/QualitySessionLog/sessions_master.json` — lap-level session log (48 sessions, easy laps=null, quality laps slimmed to 7 fields)
- `~/.config/garmin-coach/health_cache/` — daily health cache (2h TTL)
- `~/.config/garmin-coach/lap_cache/` — lap data cache (7d TTL)
- `~/.config/garmin-coach/.env` — Garmin credentials (never in Google Drive)

## Coach Skill

Invoke via the `garmin-jd-coach` skill in Cowork. The skill uses Garmin MCP tools (server ฝั่ง Cowork plugin; local มีแค่ `skills/garmin_coach_mcp/config.py` + `db_helper.py`) to fetch live Garmin data, then runs analytics tools for context.
