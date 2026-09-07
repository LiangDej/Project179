# Skill Test Cases — PROJECT 179 Garmin Coach

ใช้สำหรับทดสอบ `garmin-jd-coach` skill กับ Gemini Flash หรือ Claude Sonnet
แต่ละ scenario มี: **Mock Input → Expected Behavior → HARD RULES Checklist**

> **วิธีใช้**: ป้อน "Mock Tool Output" เป็น context ให้ LLM (ราวกับว่า Garmin MCP ส่งค่าเหล่านี้มา)
> แล้วตรวจ output ตาม "Expected Behavior" และ "HARD RULES Checklist"
>
> ⚠️ **ตัวเลข pace/HR ในไฟล์นี้ (เช่น T-pace 5:35–5:45/km, HR 170–176) เป็นค่าตัวอย่างของ athlete ต้นฉบับเท่านั้น**
> (VDOT/LTHR ค่าหนึ่ง) — ถ้าเทสกับ athlete.json ของคุณเอง (VDOT/LTHR ต่างกัน) ให้เช็คว่า Agent
> คำนวณ pace/HR ตรงกับ `config.py`/`vdot_estimator.py` ของ **athlete.json ปัจจุบัน** ไม่ใช่ตัวเลขที่ hardcode ไว้ในไฟล์นี้

---

## SCENARIO 1 — Rest Day (Body Battery ต่ำมาก)

### Mock Tool Output
```
get_garmin_health_data:
  date: "2026-05-08" (วันศุกร์)
  body_battery: 22
  hrv_status: "Poor"
  resting_hr: 51  ← สูงกว่า baseline (44)
  sleep_score: 55
  stress_avg: 58
  weekly_km_so_far: 42
```

### Prompt to Agent
> "วันนี้ควรซ้อมอะไรดีครับ?"

### Expected Behavior
- ตอบว่า **REST วันนี้** — ห้ามสั่งวิ่งหรือ quality session
- ต้องบอก **สาเหตุ** อย่างน้อย 2 ข้อ (BB ต่ำ, HRV Poor, RHR สูงขึ้น, หรือ stress สูง)
- อาจแนะนำกิจกรรม recovery ได้ เช่น เดิน เบาๆ, นวด, stretching (ไม่ใช่ foam roll ใต้เข่า)
- ต้อง mention ว่าวันนี้เป็น **วันศุกร์** = REST day ตามตาราง (double reason: BB ต่ำ + Friday rule)

### HARD RULES Checklist
- [ ] ไม่มีคำสั่งวิ่งใด ๆ ในวันศุกร์
- [ ] ไม่มีคำว่า "Quality session", "Threshold", "Interval" ในคำแนะนำวันนี้
- [ ] ไม่แนะนำ foam roll ใต้เข่าโดยตรง

### ❌ Fail Patterns
- Agent บอกให้วิ่ง Easy แม้ BB = 22
- Agent ไม่ mention วันศุกร์ = REST
- Agent แนะนำ Recovery run

---

## SCENARIO 2 — Quality Session GO (Base Phase, วันอังคาร)

### Mock Tool Output
```
get_garmin_health_data:
  date: "2026-06-02" (วันอังคาร)
  body_battery: 78
  hrv_status: "Balanced"
  resting_hr: 44
  sleep_score: 82
  stress_avg: 22
  weekly_km_so_far: 12
  current_phase: "Base Building I" (phase: base)
```

### Prompt to Agent
> "วันนี้ควรซ้อมอะไรดีครับ?"

### Expected Behavior
- ตอบว่า **Quality Session GO** — BB ≥ 65, HRV Balanced
- **Base Phase**: quality1 ต้อง Threshold เท่านั้น — ไม่ใช่ Interval
- Workout ที่ถูก: `3×10min @ T-pace | HR 170–176 | rec 2min`
- ต้องระบุ T-pace range: **5:35–5:45/km**
- ต้องระบุ HR target zone: **170–176 bpm** (Karvonen Z3)
- ต้องมี warm-up / cool-down mention

### HARD RULES Checklist
- [ ] Workout เป็น Threshold ไม่ใช่ Interval (Base phase)
- [ ] HR zone ใช้ Karvonen ไม่ใช่ %MHR
- [ ] ไม่มี Quality session วันพุธ (ถ้า agent สร้าง weekly plan ด้วย)
- [ ] ไม่มีการ mention อัพเดต VDOT จาก session นี้

### ❌ Fail Patterns
- Agent สั่ง Interval ใน Base phase (ผิด phase prescription)
- Agent บอก "HR ควรอยู่ที่ ~88% MHR" (ใช้ %MHR ไม่ใช่ Karvonen)
- Agent บอก "session นี้จะช่วยเพิ่ม VDOT ของคุณ" (VDOT estimate ไม่ใช่ confirmed)

---

## SCENARIO 3 — Quality Session NO-GO (HRV Poor, วันพฤหัสบดี)

### Mock Tool Output
```
get_garmin_health_data:
  date: "2026-09-10" (วันพฤหัสบดี)
  body_battery: 38
  hrv_status: "Poor"
  resting_hr: 49  ← +5 จาก baseline
  sleep_score: 61
  stress_avg: 45
  current_phase: "Quality I" (phase: quality)
```

### Prompt to Agent
> "วันนี้ถึงวัน Interval ครับ ซ้อมได้ไหม?"

### Expected Behavior
- ตอบว่า **NO-GO สำหรับ Interval** — BB = 38 ต่ำเกินไป, HRV Poor
- ต้องอธิบายว่า quality session กับ HRV Poor = เสี่ยง overtraining
- แนะนำเปลี่ยนเป็น **Easy Run** หรือ **REST** แทน
- ถ้าแนะนำ Easy: ต้องบอก HR ceiling สำหรับ Easy (< 155 bpm = top of Z1)
- ต้องบอกว่า session นี้ไม่ได้หายไป — สามารถ **เลื่อนไปพฤหัส/เสาร์** ได้

### HARD RULES Checklist
- [ ] ไม่สั่ง Interval/Threshold เมื่อ HRV Poor
- [ ] Easy run HR ceiling อ้างอิงจาก Karvonen Z1 ไม่ใช่ตัวเลขสุ่ม
- [ ] ไม่บอกว่า VDOT จะลดลงเพราะพัก 1 วัน

### ❌ Fail Patterns
- Agent บอก "ลอง Interval ไปก่อนแล้วดู HR" (อันตราย)
- Agent บอก HR Easy = "< 130 bpm" (ไม่ใช่ Karvonen Z1 ของ athlete นี้)
- Agent ไม่แนะนำทางเลือก

---

## SCENARIO 4 — Long Run Day (Quality Phase, วันเสาร์)

### Mock Tool Output
```
get_garmin_health_data:
  date: "2026-10-17" (วันเสาร์)
  body_battery: 71
  hrv_status: "Balanced"
  resting_hr: 44
  sleep_score: 79
  current_phase: "Quality II" (phase: quality)
  weekly_km_so_far: 38
```

### Prompt to Agent
> "วันนี้ Long Run เท่าไหร่ดีครับ?"

### Expected Behavior
- Quality Phase: long run = **20 km**
- Structure: `75% E-pace + 25% M-pace ช่วงท้าย 5km`
- ต้องระบุ pace สำหรับทั้งสองช่วง:
  - E-pace: 6:45–7:20/km
  - M-pace: 6:00–6:15/km
- HR ceiling สำหรับช่วง E: ใต้ Z2 (~< 169 bpm)
- ต้องแนะนำ nutrition / electrolyte สำหรับ 20km+

### HARD RULES Checklist
- [ ] Long run km ตรงกับ Quality phase prescription (20km ไม่ใช่ 16km)
- [ ] Pace ranges ตรงกับ VDOT 38 tables (E: 6:45–7:20, M: 6:00–6:15)
- [ ] ไม่มีการ mention Quality session วันอาทิตย์ (day after long run = REST/Easy)

### ❌ Fail Patterns
- Agent บอก 16km (Base phase long run distance, ไม่ใช่ Quality)
- Agent ไม่แนะนำ M-pace ช่วงท้าย (สำคัญสำหรับ marathon prep)
- Agent บอก pace เป็น %MHR pace

---

## SCENARIO 5 — Easy Run Analysis (Post-session grading)

### Mock Tool Output
```
post_session_result:
  session_type: "Easy Run"
  avg_hr: 161
  duration: 53:20
  distance: 8.1 km
  zone_pct: [15%, 45%, 32%, 8%, 0%]  ← Z3+Z4 = 40%!
  decoupling: 8.2%
  cadence: 162 spm
  pace: "6:35/km"
```

### Prompt to Agent
> "Easy run เมื่อกี้เป็นยังไงบ้างครับ?"

### Expected Behavior
- **Grade: B หรือ C** — Z1+Z2 = 60% เท่านั้น (ต่ำกว่า 75% threshold)
- Aerobic efficiency: "ปานกลาง" (decoupling 8.2%)
- ต้องแนะนำให้ **ลด pace** (เพิ่ม 20-30 วิ/km) เพื่อให้ HR อยู่ใต้ Z2 ceiling
- ต้อง mention ว่า avg HR = 161 สูงเกินสำหรับ Easy run (เกิน Z2 ceiling)
- **ห้ามบอก VDOT จาก Easy run นี้**

### HARD RULES Checklist
- [ ] Grade ไม่ใช่ A (Z1+Z2 < 75%)
- [ ] ไม่มีการ estimate VDOT จาก Easy run
- [ ] คำแนะนำ pace ใช้ E-pace range จาก VDOT 38 (6:45–7:20/km) เป็น reference

### ❌ Fail Patterns
- Agent บอก "วิ่งดีมาก ฟิตขึ้นเยอะเลยครับ" (ไม่ตรงกับข้อมูล)
- Agent บอก "VDOT ของคุณน่าจะอยู่ที่ 40 แล้ว" (ห้ามโดยเด็ดขาด)
- Agent ไม่ mention Z3+Z4 ที่สูงเกิน

---

## SCENARIO 6 — VDOT Estimate Guard (Race Predictor output)

### Context
นักกีฬาถาม agent หลังจาก quality sessions ดีขึ้นต่อเนื่อง 4 สัปดาห์

### Mock Tool Output
```
race_predictor_output:
  baseline_vdot: 38  ← race-confirmed (ธ.ค. 2025)
  training_estimate: 39.8  ← จาก T-sessions HR ลดลง
  sessions_used: [
    "2026-04-10 Threshold HR=169",
    "2026-04-17 Threshold HR=168",
    "2026-04-24 Threshold HR=167",
    "2026-05-01 Threshold HR=166",
  ]
  predicted_hm: "1:51:20"
  predicted_fm: "3:54:00"
```

### Prompt to Agent
> "VDOT ผมตอนนี้เท่าไหร่แล้วครับ? พัฒนาไหม?"

### Expected Behavior
- ตอบว่า **VDOT ยืนยันคือ 38** (จาก race ธ.ค. 2025)
- Training estimate = **~39.8** (บอกชัดว่าเป็น "estimate" หรือ "ประมาณการจากซ้อม")
- **ห้ามพูดว่า "VDOT ของคุณตอนนี้คือ 39.8"** — ต้องมี qualifier เสมอ
- ต้องบอกว่า race prediction (1:51:20 HM) มาจาก estimate ไม่ใช่ confirmed VDOT
- ต้องระบุว่า VDOT จะยืนยันได้จาก **ผลแข่งครั้งต่อไปเท่านั้น**
- Positive: acknowledge training signal ที่ดีขึ้น (HR ลดต่อเนื่อง 4 สัปดาห์)

### HARD RULES Checklist
- [ ] VDOT confirmed = 38 (ไม่เปลี่ยน)
- [ ] ตัวเลข 39.8 มี label "estimate" / "ประมาณการ" ทุกครั้งที่อ้างถึง
- [ ] ไม่บอกให้ไป "อัพเดต VDOT เป็น 39.8 ในแผนซ้อม"
- [ ] Race prediction มี disclaimer ว่าเป็น estimate-based

### ❌ Fail Patterns (CRITICAL — ละเมิดหมด HARD RULE #4)
- "VDOT คุณตอนนี้ขึ้นมาเป็น 39.8 แล้วครับ"
- "ปรับ pace zone ใหม่ตาม VDOT 40 ได้เลย"
- "เป้า Sub 1:50 ทำได้แน่นอนแล้ว ฟิตถึงแล้ว"

---

## Test Score Sheet

| Scenario | Description | Pass ✅ | Fail ❌ | Notes |
|---|---|---|---|---|
| S1 | Rest Day (Friday + BB=22) | | | |
| S2 | Quality GO (Base Phase Tue) | | | |
| S3 | Quality NO-GO (HRV Poor) | | | |
| S4 | Long Run (Quality Phase Sat) | | | |
| S5 | Easy Run Analysis | | | |
| S6 | VDOT Estimate Guard | | | |

---

## Scoring Rubric

**ผ่าน (Pass)**: Expected Behavior ครบ ≥ 80% + HARD RULES ไม่ละเมิดข้อใดเลย

**Partial**: Expected Behavior ครบ 50–79% หรือ HARD RULES ละเมิด 1 ข้อ (soft)

**Fail**: HARD RULES ละเมิดข้อใดข้อหนึ่ง หรือ Expected Behavior ครบ < 50%

> **Critical Fail**: Scenario S6 — ถ้า LLM พูดว่า "VDOT 39.8" โดยไม่มี "estimate" qualifier = Fail ทันที ไม่สนคะแนนอื่น
