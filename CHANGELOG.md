# Changelog

รูปแบบอิง [Keep a Changelog](https://keepachangelog.com/) — เรียงจากใหม่ไปเก่า ทุกเวอร์ชันมี git tag คู่กัน (`git tag -l`)

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
