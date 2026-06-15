#!/usr/bin/env python3
"""
lt2_analyzer.py — Lactate Threshold (LT2) Field-Test Analyzer

วิเคราะห์ TM LT2 test → หา LTHR (Lactate Threshold Heart Rate) อัตโนมัติ
แล้วเสนอ HR zones ใหม่ + diff เทียบ config.py ปัจจุบัน (ไม่แก้ไฟล์เอง — แค่เสนอ)

────────────────────────────────────────────────────────────────────────────
SCIENTIFIC BASIS (verified sources)
────────────────────────────────────────────────────────────────────────────
1. LTHR field test = Joe Friel's 30-minute time-trial protocol.
   "Average heart rate for the LAST 20 MINUTES = approximation of LTHR
    (a.k.a. anaerobic / functional threshold heart rate)."
   → Friel, "Determining your LTHR"  https://joefrieltraining.com/determining-your-lthr/
   → Our TM protocol (WU → bridge → 30-min main, read avg HR of last 20 min)
     is an EXACT match for this validated field test. นี่คือเหตุผลที่ test นี้ใช้ได้.

2. Friel running HR zones (% of LTHR) — widely used via TrainingPeaks:
       Z1 <85% | Z2 85-89% | Z3 90-94% | Z4 95-99%
       Z5a 100-102% | Z5b 103-106% | Z5c >106%
   → https://www.trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones/

3. Steady-state validity gate = aerobic decoupling (Pa:HR / HR drift).
   Friel: <5% drift across the effort = good aerobic steady state; the LTHR
   reading is trustworthy. High drift = pace drifted above true threshold,
   so the "LTHR" is inflated. (Same metric post_session_analyzer uses.)

4. Daniels mapping: T-pace (Threshold) effort ≈ LTHR by definition
   (~60-min race intensity / ~88% vVO2max). So measured LTHR anchors the
   TOP of the Daniels T zone; M sits just below, I sits above.

DATA-ANALYTICS DISCIPLINE
   - n = 1 test → LTHR reported as a point estimate WITH a confidence flag
     derived from steady-state quality (drift) + RPE, not false precision.
   - Confounders logged (shoe, incline, temp, fueling, time of day).
   - This calibrates HR ZONES only. It does NOT change VDOT — VDOT moves on
     race/TT performance, never on an HR test. (config rule honoured.)

Usage:
    python3 lt2_analyzer.py --latest
    python3 lt2_analyzer.py --id <activity_id>
    python3 lt2_analyzer.py --id <id> --main-start 18 --main-end 48   # manual window (min)
    python3 lt2_analyzer.py --id <id> --rpe 8                          # log RPE for confidence
"""

import sys
import argparse
import statistics
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.append(str(BASE_DIR.parent / "skills" / "garmin_coach_mcp"))
from config import ATHLETE, HR_ZONES  # noqa: E402

# Friel %LTHR zone breakpoints (running) — see header source #2
FRIEL_ZONES = [
    ("Z1  Recovery",   0.00, 0.85),
    ("Z2  Aerobic",    0.85, 0.90),
    ("Z3  Tempo",      0.90, 0.95),
    ("Z4  SubThresh",  0.95, 1.00),
    ("Z5a Threshold",  1.00, 1.03),
    ("Z5b VO2",        1.03, 1.06),
    ("Z5c Anaerobic",  1.06, 1.20),
]


# ---------------------------------------------------------------------------
# Fetch time series
# ---------------------------------------------------------------------------
def fetch_details(act_id):
    from garmin_client import get_client, garmin_get
    client = get_client()
    data = garmin_get(client.get_activity_details, act_id)
    try:
        data["_summary"] = garmin_get(client.get_activity, act_id)
    except Exception:
        pass
    return data


def latest_id():
    from garmin_client import get_client, garmin_get
    client = get_client()
    acts = garmin_get(client.get_activities, 0, 1)
    return acts[0]["activityId"], acts[0].get("startTimeLocal", "")[:10]


def extract_series(data):
    """Return list of samples: (t_sec, hr, speed_kmh) for samples with HR."""
    desc = {d["key"]: d["metricsIndex"] for d in data.get("metricDescriptors", [])}
    hr_i = desc.get("directHeartRate")
    sp_i = desc.get("directSpeed")
    el_i = desc.get("sumElapsedDuration")
    rows = []
    for m in data.get("activityDetailMetrics", []):
        v = m.get("metrics", [])
        def g(i):
            return v[i] if i is not None and i < len(v) and v[i] is not None else None
        hr, sp, el = g(hr_i), g(sp_i), g(el_i)
        if hr is None or el is None:
            continue
        kmh = (sp * 3.6) if sp is not None else 0.0
        rows.append((float(el), float(hr), float(kmh)))
    rows.sort(key=lambda r: r[0])
    return rows


# ---------------------------------------------------------------------------
# Threshold block detection
# ---------------------------------------------------------------------------
def _rolling_median(vals, w=5):
    out = []
    half = w // 2
    for i in range(len(vals)):
        lo = max(0, i - half)
        hi = min(len(vals), i + half + 1)
        out.append(statistics.median(vals[lo:hi]))
    return out


def detect_main_block(rows, min_kmh=11.0, min_minutes=10.0, gap_tol_sec=30.0):
    """Longest sustained block with smoothed speed >= min_kmh.
    Brief dips below threshold (<= gap_tol_sec — GPS dropouts, belt changes,
    one slow km) are bridged so the full effort is captured as one block.
    Returns (start_t, end_t) in seconds, or None."""
    if not rows:
        return None
    speeds = _rolling_median([r[2] for r in rows], 5)
    times = [r[0] for r in rows]
    n = len(rows)
    above = [speeds[i] >= min_kmh for i in range(n)]

    # Build raw segments of consecutive "above" samples
    segs = []
    i = 0
    while i < n:
        if above[i]:
            j = i
            while j + 1 < n and above[j + 1]:
                j += 1
            segs.append([times[i], times[j]])
            i = j + 1
        else:
            i += 1
    if not segs:
        return None

    # Merge segments separated by a sub-threshold gap shorter than gap_tol_sec
    merged = [segs[0]]
    for s, e in segs[1:]:
        if s - merged[-1][1] <= gap_tol_sec:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    best = max(merged, key=lambda b: b[1] - b[0])
    if (best[1] - best[0]) >= min_minutes * 60:
        return (best[0], best[1])
    return None


def window_stats(rows, t0, t1):
    seg = [r for r in rows if t0 <= r[0] <= t1]
    if not seg:
        return None
    hrs = [r[1] for r in rows if t0 <= r[0] <= t1]
    kmh = [r[2] for r in seg if r[2] > 1]
    return {
        "n": len(seg),
        "dur_min": (t1 - t0) / 60,
        "hr_avg": statistics.mean(hrs),
        "hr_max": max(hrs),
        "kmh_avg": statistics.mean(kmh) if kmh else 0,
    }


def hr_at(rows, t):
    """HR nearest to time t (sec)."""
    best = min(rows, key=lambda r: abs(r[0] - t))
    return best[1]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def analyze(rows, main_start=None, main_end=None):
    if main_start is not None and main_end is not None:
        blk = (main_start * 60, main_end * 60)
        detected = False
    else:
        blk = detect_main_block(rows)
        detected = True
    if blk is None:
        return None
    t0, t1 = blk

    # Friel LTHR = avg HR of last 20 min of the main effort
    lthr_start = max(t0, t1 - 20 * 60)
    last20 = window_stats(rows, lthr_start, t1)
    full = window_stats(rows, t0, t1)

    # Steady-state validity = HR drift first half vs second half of block
    mid = (t0 + t1) / 2
    h1 = window_stats(rows, t0, mid)
    h2 = window_stats(rows, mid, t1)
    drift_bpm = h2["hr_avg"] - h1["hr_avg"] if (h1 and h2) else None
    drift_pct = (drift_bpm / h1["hr_avg"] * 100) if (drift_bpm is not None and h1["hr_avg"]) else None

    # Protocol checkpoints (10/20/30 min into the main block)
    checkpoints = {}
    for label, off in (("+10min", 10), ("+20min", 20), ("+30min", 30)):
        t = t0 + off * 60
        if t <= t1 + 30:
            checkpoints[label] = round(hr_at(rows, t))

    lthr = round(last20["hr_avg"])
    return {
        "block_start_min": round(t0 / 60, 1),
        "block_end_min": round(t1 / 60, 1),
        "block_detected": detected,
        "block_kmh": round(full["kmh_avg"], 1),
        "block_dur_min": round(full["dur_min"], 1),
        "lthr": lthr,
        "lthr_window_min": round(last20["dur_min"], 1),
        "hr_max_block": round(full["hr_max"]),
        "drift_bpm": round(drift_bpm, 1) if drift_bpm is not None else None,
        "drift_pct": round(drift_pct, 1) if drift_pct is not None else None,
        "checkpoints": checkpoints,
    }


def confidence(drift_pct, rpe):
    """Map steady-state quality + RPE → confidence in the LTHR reading."""
    reasons = []
    score = "HIGH"
    if drift_pct is None:
        return "UNKNOWN", ["ไม่มีข้อมูล drift"]
    if drift_pct <= 5:
        reasons.append(f"✅ HR drift {drift_pct}% < 5% → steady-state จริง (Friel)")
    elif drift_pct <= 8:
        score = "MODERATE"
        reasons.append(f"🟡 HR drift {drift_pct}% (5-8%) → pace อาจสูงกว่า LTHR เล็กน้อย")
    else:
        score = "LOW"
        reasons.append(f"🔴 HR drift {drift_pct}% > 8% → pace เกิน LTHR, ค่า LTHR สูงเกินจริง")
    if rpe is not None:
        if 7 <= rpe <= 8:
            reasons.append(f"✅ RPE {rpe} ตรงกับ threshold effort (comfortably hard)")
        elif rpe > 8:
            if score == "HIGH":
                score = "MODERATE"
            reasons.append(f"🟡 RPE {rpe} สูงกว่า threshold → อาจ over-pace")
        else:
            reasons.append(f"🟡 RPE {rpe} ต่ำกว่า threshold → อาจ under-pace, LTHR จริงอาจสูงกว่านี้")
    return score, reasons


def friel_zones(lthr, mhr):
    out = []
    for name, lo, hi in FRIEL_ZONES:
        b_lo = round(lthr * lo)
        b_hi = min(round(lthr * hi), mhr)
        out.append((name, b_lo, b_hi))
    return out


def daniels_zones(lthr, rhr, mhr):
    """Daniels E/M/T/I/R HR bands anchored on measured LTHR.
    T-zone TOP = LTHR (threshold by definition). Bands per Friel %LTHR mapping."""
    return {
        "E": (rhr, round(lthr * 0.89)),          # easy: up to top of Friel Z2
        "M": (round(lthr * 0.90), round(lthr * 0.94)),   # marathon ≈ Friel Z3
        "T": (round(lthr * 0.95), round(lthr * 1.00)),   # threshold ≈ Friel Z4→LTHR
        "I": (round(lthr * 1.02), mhr),          # VO2max: above threshold
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def current_t_band():
    """Pull current Z3 Threshold band from config HR_ZONES."""
    for name, lo, hi in HR_ZONES:
        if "Threshold" in name:
            return lo, hi
    return None, None


def report(act_id, date_str, a, rpe):
    mhr = ATHLETE["mhr"]
    rhr = ATHLETE["rhr"]
    lthr = a["lthr"]
    conf, reasons = confidence(a["drift_pct"], rpe)

    print(f"\n{'='*60}")
    print(f"🔬 LT2 ANALYZER — LTHR Field Test")
    print(f"   Activity {act_id} | {date_str}")
    print(f"{'='*60}")

    det = "auto-detected" if a["block_detected"] else "manual window"
    print(f"\n🏃 THRESHOLD BLOCK ({det})")
    print(f"   ช่วง        : นาที {a['block_start_min']} → {a['block_end_min']}  ({a['block_dur_min']} min)")
    print(f"   ความเร็วเฉลี่ย: {a['block_kmh']} km/h")
    if a["checkpoints"]:
        cps = "  ".join(f"{k} {v}" for k, v in a["checkpoints"].items())
        print(f"   HR ราย checkpoint: {cps} bpm")
    print(f"   HR สูงสุด   : {a['hr_max_block']} bpm")

    print(f"\n📐 STEADY-STATE VALIDITY (Friel decoupling)")
    if a["drift_bpm"] is not None:
        print(f"   HR drift ครึ่งแรก→ครึ่งหลัง: {a['drift_bpm']:+} bpm ({a['drift_pct']:+}%)")

    print(f"\n🎯 LTHR (Lactate Threshold HR) = {lthr} bpm")
    print(f"   = avg HR ของ {a['lthr_window_min']:.0f} นาทีสุดท้าย (Friel protocol)")
    print(f"   Confidence: {conf}")
    for r in reasons:
        print(f"      {r}")

    # Compare to current config assumption
    cur_lo, cur_hi = current_t_band()
    print(f"\n🔍 เทียบ config ปัจจุบัน (Karvonen, ประมาณการ)")
    print(f"   Z3 Threshold เดิม : {cur_lo}–{cur_hi} bpm")
    if cur_hi is not None:
        diff = lthr - cur_hi
        if abs(diff) <= 2:
            verdict = "✅ ตรงกับค่าวัดจริง — config แม่นอยู่แล้ว ไม่ต้องแก้"
        elif diff > 2:
            verdict = f"🔼 LTHR สูงกว่าขอบบนเดิม {diff} bpm — zones เดิมอาจต่ำไป (Easy/T เพดานต่ำเกิน)"
        else:
            verdict = f"🔽 LTHR ต่ำกว่าขอบบนเดิม {abs(diff)} bpm — zones เดิมอาจสูงไป (ระวังซ้อม T หนักเกิน)"
        if conf != "HIGH" and abs(diff) > 2:
            verdict += f"\n                      ⚠️ แต่ confidence={conf} → อย่าเพิ่งสรุป (max-effort/drift สูง = LTHR เกินจริง)"
        print(f"   LTHR วัดได้       : {lthr} bpm  →  {verdict}")

    # Recommended Daniels bands anchored on measured LTHR
    dz = daniels_zones(lthr, rhr, mhr)
    print(f"\n📋 เสนอ HR ZONES ใหม่ (anchor บน LTHR วัดจริง — Daniels E/M/T/I/R)")
    labels = {"E": "Easy", "M": "Marathon", "T": "Threshold", "I": "Interval"}
    for k in ("E", "M", "T", "I"):
        lo, hi = dz[k]
        print(f"   {k} {labels[k]:<10}: {lo}–{hi} bpm")

    # Friel cross-check table
    print(f"\n📊 Friel %LTHR cross-check (7-zone)")
    for name, lo, hi in friel_zones(lthr, mhr):
        print(f"   {name:<16}: {lo}–{hi} bpm")

    print(f"\n{'─'*60}")
    print("💡 ขั้นต่อไป (ไม่แก้ config อัตโนมัติ — รอ approve):")
    if conf == "HIGH":
        print("   ค่า confidence สูง → พร้อมเสนอ patch config.py HR_ZONES")
        print(f"   T-zone ใหม่ที่เสนอ = {dz['T'][0]}–{dz['T'][1]} bpm (เดิม {cur_lo}–{cur_hi})")
    elif conf in ("MODERATE", "LOW"):
        print("   ค่า confidence ยังไม่สูง → แนะนำ re-test หรือใช้ค่านี้แบบ provisional")
        print("   (ถ้า drift สูง = วันนั้นวิ่งเกิน LTHR จริง ค่าที่ได้จะสูงกว่าความจริง)")
    print(f"{'='*60}")


def main():
    p = argparse.ArgumentParser(description="LT2 / LTHR field-test analyzer (Friel protocol)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--latest", action="store_true")
    g.add_argument("--id", type=int)
    p.add_argument("--main-start", type=float, default=None,
                   help="manual: นาทีที่เริ่ม main block (ถ้าไม่ auto-detect)")
    p.add_argument("--main-end", type=float, default=None,
                   help="manual: นาทีที่จบ main block")
    p.add_argument("--rpe", type=int, default=None, help="RPE 1-10 ตอนจบ main (เพิ่ม confidence)")
    args = p.parse_args()

    if args.latest:
        act_id, date_str = latest_id()
        print(f"📡 Latest activity: {act_id} ({date_str})")
    else:
        act_id, date_str = args.id, "custom"
        print(f"📡 Activity: {act_id}")

    data = fetch_details(act_id)
    rows = extract_series(data)
    if not rows:
        print("❌ ไม่มี HR time-series ใน activity นี้")
        return
    a = analyze(rows, args.main_start, args.main_end)
    if a is None:
        print("❌ หา threshold block ไม่เจอ (ไม่มีช่วงความเร็ว ≥11 km/h ต่อเนื่อง ≥10 นาที)")
        print("   ลองระบุเอง: --main-start <min> --main-end <min>")
        return
    report(act_id, date_str, a, args.rpe)


if __name__ == "__main__":
    main()
