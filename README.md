# Project 179 — Garmin JD Running Coach Agent

An AI running coach that connects to your Garmin data and coaches you using Jack Daniels' Running Formula. Talk to any AI agent — it reads your data, runs the analytics tools, and coaches you with real numbers.

![CI](https://github.com/LiangDej/Project179/actions/workflows/ci.yml/badge.svg)
![Version](https://img.shields.io/badge/version-1.0-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Compatible AI Agents

| Agent | วิธีเชื่อม |
|---|---|
| **Custom agent (recommended)** | clone repo, ตั้ง `PYTHONPATH=skills/garmin_coach_mcp`, รัน tools ผ่าน bash — วิธีนี้ self-contained ที่สุด ไม่ต้องพึ่งอะไรนอก repo นี้ |
| **ChatGPT** (Code Interpreter) | upload `athlete.json` + `races.json` แล้วคุยได้เลย |
| **Gemini** (file browsing / Colab) | ชี้ไปที่ project folder หรือ paste tool output |
| **Cursor / GitHub Copilot** | เปิด repo ใน IDE — agent อ่าน README เป็น context |
| **Claude Code / Cowork** | เปิด project folder — agent รัน tools ผ่าน bash เหมือน custom agent ได้ทันที (ไม่ต้องมี MCP server) |

> agent ไหนก็ได้ที่อ่านไฟล์และรัน `python3` ได้ — ใช้เป็น coach ได้ทันที ไม่ต้อง MCP server เลย
>
> **หมายเหตุเรื่อง MCP:** `skills/garmin_coach_mcp/` ใน repo นี้มีแค่ `config.py`/`db_helper.py` (โค้ดคำนวณ zones/paces ที่ tools ทุกตัวใช้ร่วมกัน) — **ไม่ใช่** FastMCP server ตัวเต็ม (server จริงรันแยกอยู่คนละ repo ส่วนตัว, ยังไม่ public) ถ้า agent ของคุณรองรับแค่ MCP tools โดยเฉพาะ ให้ใช้เส้นทาง "รัน tools ผ่าน bash" แทน — เป็นเส้นทางที่ทำได้ครบทุกอย่างและเป็น path หลักที่ repo นี้ทดสอบไว้

---

## Setup (ทำครั้งเดียว)

### 1 · Install (3 คำสั่ง)

```bash
git clone https://github.com/LiangDej/Project179.git
cd Project179
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### 2 · Garmin credentials

```bash
mkdir -p ~/.config/garmin-coach
echo 'GARMIN_USERNAME=your@email.com' >> ~/.config/garmin-coach/.env
echo 'GARMIN_PASSWORD=yourpassword'   >> ~/.config/garmin-coach/.env
```

(ตัวแปรชื่อ `GARMIN_USERNAME` แม้ค่าจะเป็น email ก็ตาม — ดู [`.env.example`](.env.example))

### 3 · Paste prompt นี้ให้ AI agent (แค่นี้พอ)

```
I just cloned Project 179 — Garmin JD Running Coach.
Please set me up as a new athlete. Ask me the following one by one:

1. PB ของแต่ละระยะที่มี (5K / 10K / Half Marathon / Full Marathon) — ใส่เฉพาะระยะที่เคยแข่งจริง
2. เป้าหมายแข่งหลัก (A-race): ระยะ, ชื่องาน, วันที่, เวลาเป้าหมาย
3. มี B-race (tune-up) ไหม? ถ้ามี: ระยะ, วันที่
4. น้ำหนักตัว (kg) — ใช้คำนวณ nutrition
5. วิ่ง treadmill หรือ outdoor เป็นหลัก?
6. electrolyte ที่ใช้อยู่คืออะไร? (ถ้าไม่มี บอกว่าไม่มี)

จาก PB ที่ได้ ให้ประเมิน VDOT เบื้องต้น แล้วบอกว่า VDOT นี้สมเหตุสมผลกับ PB ทุกระยะไหม
(เช่น 5K บอก VDOT 44 แต่ FM บอก VDOT 38 → ไม่ consistent → ควรเชื่อระยะไหน)
จากนั้นแนะนำ TT แรกที่ควรวิ่ง (ระยะ + เป้า) เพื่อยืนยัน VDOT ก่อน lock training zones

เมื่อได้ข้อมูลครบ ให้ copy athlete.example.json → athlete.json และ races.example.json → races.json
แล้ว update ค่าทั้งหมดให้ถูกต้อง จากนั้นรัน test suite ยืนยัน
```

agent จะถามทีละข้อ → เขียนไฟล์ให้ → รัน test suite → แจ้งผล — **คุณไม่ต้องแตะไฟล์เลย**

---

## หลัง Setup — แค่คุยกับ agent

ไม่มี command ให้จำ ไม่มี flag ให้พิมพ์ — พูดกับ agent เป็นภาษาปกติเหมือนคุยกับโค้ชจริงๆ ได้เลย เช่น:

- "สรุปเช้านี้ให้หน่อย"
- "วิ่งเมื่อกี้เป็นไงบ้าง"
- "สัปดาห์นี้วิ่งไปเท่าไหร่แล้ว"

agent จะเลือก tool ที่เหมาะสมเอง ดึงข้อมูล Garmin ของคุณ แล้วตอบเป็นคำแนะนำพร้อมตัวเลขจริง — ดู [Tool Reference](#tool-reference) ด้านล่างสำหรับตัวอย่างประโยคเพิ่มเติม

**ยังไม่รู้จะเริ่มพูดอะไรก่อน?** ลอง 3 คำถามนี้ก่อนเลย:
1. **"สรุปเช้านี้ให้หน่อย"** → ภาพรวมร่างกาย + ควรวิ่งอะไรวันนี้
2. **"วิเคราะห์วิ่งเมื่อกี้หน่อย"** → หลังวิ่งเสร็จทุกครั้ง
3. **"สัปดาห์นี้วิ่งไปเท่าไหร่แล้ว"** → เช็คว่าซ้อมตามแผนไหม

---

## แนวคิดเบื้องหลัง (อ่าน 1 นาทีก่อนไปต่อ)

Tool ด้านล่างมีศัพท์เทคนิคเยอะ — 4 คำนี้เข้าใจไว้ก่อนจะช่วยให้อ่านง่ายขึ้นมาก:

| คำ | ความหมายแบบง่าย |
|---|---|
| **VDOT** | ตัวเลขเดียวที่บอก "ฟิตแค่ไหน" — ยิ่งสูงยิ่งวิ่งเร็ว/ไกลได้มากขึ้น ยืนยันได้จากผลแข่งจริงหรือ Time Trial เท่านั้น (ไม่ใช่เดาจากความรู้สึก) |
| **Training Zones (E/M/T/I/R)** | ความหนักในการวิ่ง 5 ระดับ จาก Easy (ชิลๆ คุยได้) ไปถึง Repetition (เร็วสุด) — คำนวณจาก VDOT ของคุณเอง |
| **CTL / ATL / TSB** | "ฟิตเนส" (สะสมนานๆ) / "ความเหนื่อยล่าสุด" / "ความสด" — ใช้เช็คว่าตอนนี้ควรซ้อมหนักหรือพัก |
| **Body Battery** | ศัพท์ของ Garmin เอง — พลังงานร่างกายที่เหลือในแต่ละวัน (0-100) คล้ายเปอร์เซ็นต์แบตมือถือ |

ไม่ต้องท่องจำ — agent จะอธิบายตัวเลขพวกนี้ให้ทุกครั้งที่ตอบ แค่รู้คร่าวๆ ว่ามันคืออะไรก็พอ

---

## Tool Reference

> agent ที่ clone repo นี้จะใช้ tools เหล่านี้อัตโนมัติตาม prompt ที่คุณพูด

---

### Daily Coaching

**`daily_brief.py`** — Morning dashboard ก่อนวิ่ง

| พูดว่า | ได้ |
|---|---|
| "สรุปเช้านี้ให้หน่อย" | Body Battery, HRV, sleep score, วันนี้ควรวิ่งอะไร, เหลือกี่วันถึงแข่ง |
| "สรุปเช้านี้ ไม่ต่อ internet" | เหมือนกันแต่ใช้ข้อมูล cache (ไม่ดึง Garmin live) |
| "สรุปเช้านี้ ขาซ้ายปวดนิดหน่อย" | ปรับ prescription ลดความหนักตาม pain level |

---

**`session_prescriber.py`** — แผนวิ่งรายสัปดาห์

| พูดว่า | ได้ |
|---|---|
| "วันนี้ควรวิ่งอะไร?" | แผน 7 วัน: ประเภท session (E/T/I/R/Long), ระยะ, pace target, HR ceiling |
| "วาง week นี้ให้หน่อย BB ผม 70" | แผนโดยใช้ Body Battery 70 เป็น readiness input |
| "BB ผมแค่ 35 ปวดขาเล็กน้อย วันนี้วิ่งได้ไหม?" | ลด intensity อัตโนมัติ → Easy แทน Quality |

---

**`post_session_analyzer.py`** — วิเคราะห์วิ่งเสร็จแล้ว

| พูดว่า | ได้ |
|---|---|
| "วิเคราะห์วิ่งเมื่อกี้หน่อย" | zone breakdown, cardiac decoupling %, JD verdict (session ผ่านหรือเปล่า) |
| "วิเคราะห์ activity 22826276241" | วิเคราะห์ activity ตาม ID ที่ระบุ |
| "วิเคราะห์แล้วบันทึกลง log ด้วย" | วิเคราะห์ + save ลง sessions_master.json |

---

**`weekly_load_report.py`** — รายงาน load สัปดาห์

| พูดว่า | ได้ |
|---|---|
| "สัปดาห์นี้วิ่งไปเท่าไหร่แล้ว?" | total km, zone distribution, Easy/Quality ratio, เทียบ target |
| "ดู 4 สัปดาห์ย้อนหลัง" | trend 4 สัปดาห์: volume, intensity, consistency |

---

**`detect_session.py`** — จำแนกประเภท session อัตโนมัติ

| พูดว่า | ได้ |
|---|---|
| "วิ่งเมื่อกี้เป็น session ประเภทอะไร?" | E / M / T / I / R / Race พร้อม confidence score |

---

### Training Load & Planning

**`training_load.py`** — PMC (ATL/CTL/TSB)

| พูดว่า | ได้ |
|---|---|
| "ฟิตเนสตอนนี้เป็นยังไง?" | ATL (fatigue), CTL (fitness), TSB (form) 42 วันล่าสุด + กราฟ |
| "CTL ต้องถึงเท่าไหร่ก่อนแข่ง?" | target CTL path ถึง A-race พร้อม weekly km guide |
| "เหนื่อยสะสมมากไหม?" | ACWR ratio + overreaching risk |

---

**`training_planner.py`** — แผน season เต็ม

| พูดว่า | ได้ |
|---|---|
| "วางแผนทั้ง season ให้หน่อย" | phase ทุกสัปดาห์จนถึงแข่ง: Base/Quality/Race-Specific/Taper + km target |
| "แผน 8 สัปดาห์สุดท้ายก่อนแข่ง" | race-specific block: long run schedule, quality types, weekly volume |

---

**`daily_aggregator.py`** — รวม sessions ในวันเดียว

| พูดว่า | ได้ |
|---|---|
| "วันนี้วิ่ง 2 รอบ รวมกันเท่าไหร่?" | daily + weekly total จาก multi-session, ไม่นับซ้ำ |

---

**`season_summary.py`** — ภาพรวม season

| พูดว่า | ได้ |
|---|---|
| "ตอนนี้อยู่ phase อะไร พร้อมแข่งไหม?" | phase ปัจจุบัน, CTL vs target, quality sessions ล่าสุด, readiness summary |

---

### VDOT & Performance

**`vdot_estimator.py`** — ประเมิน VDOT จาก training

| พูดว่า | ได้ |
|---|---|
| "VDOT ผมตอนนี้เท่าไหร่?" | training-based VDOT estimate จาก quality sessions 8 สัปดาห์ + disclaimer ว่าต้องยืนยันจากแข่งจริง |
| "พัฒนาขึ้นไหม 3 เดือนที่ผ่านมา?" | VDOT trend + session HR drift ตลอด 3 เดือน |

---

**`race_predictor.py`** — พยากรณ์เวลาแข่ง

| พูดว่า | ได้ |
|---|---|
| "ถ้าแข่ง HM วันนี้ได้กี่โมง?" | predicted HM + FM time จาก training fitness + gap ถึง goal |
| "ต้องการ VDOT เท่าไหร่ถึง Sub 4 FM?" | VDOT required + กี่ point ต้องพัฒนาอีก |

---

**`post_race_updater.py`** — อัป VDOT หลังแข่ง

| พูดว่า | ได้ |
|---|---|
| "เพิ่ง HM มา 1:52:30 อัป VDOT ให้หน่อย" | คำนวณ VDOT ใหม่ → อัป athlete.json → recalculate zones/paces ทุก tool |
| "FM 3:58 อากาศร้อน 27°C ควร adjust ไหม?" | heat-adjusted VDOT + คำแนะนำว่าควร apply หรือรอ TT ยืนยัน |

---

**`energy_efficiency_scorer.py`** — ประสิทธิภาพ gel/electrolyte

| พูดว่า | ได้ |
|---|---|
| "gel ที่กินอยู่มันได้ผลไหม?" | efficiency score จาก HR response หลัง intake ใน 6 sessions ล่าสุด |

---

### Race Preparation

**`race_pace_planner.py`** — pace strategy รายกม.

| พูดว่า | ได้ |
|---|---|
| "วาง pace plan แข่ง FM ให้หน่อย" | pace target ทุก 5km, HR ceiling แต่ละช่วง, negative split strategy |
| "ถ้าจะ Sub 4 FM ควรวิ่ง pace อะไร?" | breakdown pace/km + HR zone ตาม VDOT ปัจจุบัน |

---

**`weather_adjuster.py`** — ปรับ pace ตามอากาศ

| พูดว่า | ได้ |
|---|---|
| "แข่งวันนี้อากาศ 27°C ความชื้น 80% ควรลด pace เท่าไหร่?" | heat-adjusted pace + HR ceiling + เวลาที่คาดว่าจะช้ากว่า goal กี่นาที |
| "ดึงพยากรณ์อากาศวันแข่ง Bangsaen เลย" | ดึง weather forecast วันแข่ง → คำนวณ adjustment อัตโนมัติ |

---

**`taper_monitor.py`** — monitor taper ก่อนแข่ง

| พูดว่า | ได้ |
|---|---|
| "Taper ผมไปถึงไหนแล้ว?" | mileage reduction %, BB trend, freshness score, readiness verdict |
| "BB ผม 65 สัปดาห์ก่อนแข่ง ดีพอไหม?" | เทียบ target taper metrics + คำแนะนำ |

---

**`nutrition_calculator.py`** — plan nutrition แข่ง

| พูดว่า | ได้ |
|---|---|
| "nutrition plan แข่ง FM Bangsaen อากาศ 27°C ให้หน่อย" | gel + electrolyte schedule ทุก km, Na mg total, % ACSM ceiling |
| "ชั่งก่อน-หลังวิ่ง: 71.6→70.1kg ดื่ม 500ml/h คำนวณ sweat rate ให้หน่อย" | sweat rate L/hr + calibrated Na replacement สำหรับ race plan |

---

**`fuji_race_pacer.py`** — pace strategy สนามเนิน

| พูดว่า | ได้ |
|---|---|
| "แข่ง FM สนามมีเนิน elevation 900m วาง pace ให้หน่อย" | grade-adjusted pace ทุก segment ตาม elevation profile |

---

### Health & Recovery

**`hrv_trend.py`** — HRV trend + overtraining warning

| พูดว่า | ได้ |
|---|---|
| "HRV ผม 2 เดือนที่ผ่านมาเป็นยังไง?" | HRV baseline, weekly trend, deviation จาก personal norm |
| "เสี่ยง overtraining ไหม?" | overtraining score + รายการ warning signs ถ้ามี |

---

**`sleep_correlator.py`** — sleep → performance correlation

| พูดว่า | ได้ |
|---|---|
| "นอนน้อยมีผลกับการวิ่งไหม?" | correlation: sleep score vs HR drift ใน session วันถัดไป |
| "insight เรื่องนอนให้หน่อย" | สรุป pattern: นอนต่ำกว่า X ชม. → HR สูงขึ้นกี่ bpm ใน quality session |

---

**`injury_risk_detector.py`** — ประเมินความเสี่ยง injury

| พูดว่า | ได้ |
|---|---|
| "เสี่ยง injury ไหมตอนนี้?" | ACWR ratio (ATL/CTL), risk level (LOW/MODERATE/HIGH), สาเหตุถ้าเสี่ยง |
| "load 3 สัปดาห์ที่ผ่านมา spike ไหม?" | EWMA ATL trend + spike detection |

---

**`heat_acclimation.py`** — heat training load

| พูดว่า | ได้ |
|---|---|
| "ร่างกายชินกับอากาศร้อนแล้วหรือยัง?" | heat-adjusted TRIMP, acclimation score, กี่สัปดาห์ถึงจะชิน |
| "plan เตรียมตัวสำหรับแข่งเมืองร้อน" | transition plan: Bangkok summer → race day conditions |

---

### Form & Testing

**`form_tracker.py`** — cadence/form trend

| พูดว่า | ได้ |
|---|---|
| "cadence ผมพัฒนาไหม 3 เดือนที่ผ่านมา?" | cadence trend, vertical ratio, HR correlation ที่ pace เดิม |

---

**`lt2_analyzer.py`** — LTHR field test analyzer

| พูดว่า | ได้ |
|---|---|
| "วิ่ง TT 30 นาทีเสร็จแล้ว วิเคราะห์ LTHR ให้หน่อย" | avg HR ช่วง 10 นาทีสุดท้าย = LTHR estimate, confidence level |

---

### Session Logging

**`session_logger.py`** — บันทึก session log

| พูดว่า | ได้ |
|---|---|
| "บันทึก session วันนี้ลง log" | interactive: ถามประเภท session, pace, HR, note → save ลง sessions_master.json |
| "import easy runs ทั้งหมด 3 เดือนที่ผ่านมา" | batch import easy sessions โดยไม่ต้องกรอกทีละอัน |

---

**`skill_sync.py`** — sync coach skill file

| พูดว่า | ได้ |
|---|---|
| "อัป coach skill ให้ตรงกับ session log ล่าสุด" | update garmin-coach-analyzer.md ให้ reflect quality progress ล่าสุด |

---

**`stamina_patcher.py`** — patch stamina data

| พูดว่า | ได้ |
|---|---|
| "Stamina data ใน log ยังไม่ครบ patch ให้หน่อย" | backfill stamina_drain_pct เข้า sessions ที่ยังไม่มีข้อมูล |

---

### Infrastructure (agent ใช้เบื้องหลัง ไม่ต้องเรียกตรงๆ)

| Tool | หน้าที่ |
|---|---|
| `garmin_client.py` | Garmin Connect client + health cache (2h TTL) |
| `activity_loader.py` | โหลด activity จาก file history + live overlay |
| `race_registry.py` | อ่าน races.json → single source สำหรับทุก tool |
| `vdot_math.py` | Jack Daniels VDOT formulas — ทุก tool import จากนี้ |
| `treadmill_pace_model.py` | HR→pace interpolation สำหรับ treadmill |
| `bangkok_climate.py` | อุณหภูมิเช้ากรุงเทพแยกตามเดือน |
| `garmin_workout_pusher.py` | push workout plan ขึ้น Garmin Connect device |
| `tm_patch.py` | แก้ session type/pace ที่ผิดใน sessions_master.json |

---

## For Developers

```bash
# Run after any change to tools, config.py, athlete.json, or races.json
.venv/bin/python3.13 GarminRawData/tests/test_suite.py
# 30 checks: UNIT + CONSISTENCY + FUNCTIONAL — exit 0 = all green
```

**Protected files** (convention for AI coding assistants during a live coaching session — see [CLAUDE.md](CLAUDE.md) — human contributors should open a PR as normal):
- `GarminRawData/tools/*.py` (all 35 tools)
- `skills/garmin_coach_mcp/config.py` + `db_helper.py`

CI runs automatically on every push via GitHub Actions.

Two `requirements.txt` files exist: the **root one is authoritative** (full set, used by CI + setup step 1); `GarminRawData/tools/requirements.txt` is a lighter subset for the tools-only path.

---

## Methodology

- **Jack Daniels' Running Formula** — VDOT system, 5 pace zones (E/M/T/I/R)
- **Karvonen HR zones** — derived from LTHR (Friel 30-min TT)
- **ACWR via EWMA** — injury risk from ATL/CTL ratio
- **ACSM Sawka 2007** — sweat-rate calibrated Na replacement
- Paces and zones update automatically when athlete.json changes — no code edits needed

---

## ⚠️ ข้อควรรู้

Project นี้เป็น **เครื่องมือช่วยวางแผนซ้อม** อิงหลักการที่มีงานวิจัยรองรับ (Jack Daniels, ACSM, EWMA) แต่**ไม่ใช่คำแนะนำทางการแพทย์**

- ถ้ามีอาการเจ็บ ปวด หรือผิดปกติระหว่างซ้อม ให้หยุดและปรึกษาแพทย์/นักกายภาพก่อนเสมอ
- ตัวเลข VDOT/HR zones ควรยืนยันจากผลแข่งหรือ Time Trial จริง ไม่ใช่ใช้ค่าประเมินไปตัดสินใจเรื่องสุขภาพ
- ผู้ใช้เป็นผู้รับผิดชอบการตัดสินใจซ้อม/แข่งขันของตัวเองทั้งหมด

---

## License

[MIT](LICENSE) — free to use, modify, and redistribute, with attribution.

*[Read this in English →](README_EN.md)*
