#!/usr/bin/env python3
"""
form_tracker.py — Running-Form Trend & Form→HR Correlation

ติดตามพัฒนาการ running dynamics (Cadence, Vertical Ratio, GCT, Vertical
Oscillation, Stride) ข้ามหลายสัปดาห์ แล้ววัดว่า "ฟอร์มดีขึ้น = HR ลดลงจริงไหม"

────────────────────────────────────────────────────────────────────────────
SCIENTIFIC BASIS (verified sources)
────────────────────────────────────────────────────────────────────────────
1. Cadence / step-rate manipulation — Heiderscheit et al. (2011),
   "Effects of Step Rate Manipulation on Joint Mechanics during Running,"
   Medicine & Science in Sports & Exercise 43(2). 45 recreational runners,
   treadmill, constant speed. เพิ่ม step rate +10% เหนือ preferred →
   center-of-mass vertical excursion, braking impulse, peak knee flexion
   และ energy absorption ลดลง = โหลดข้อต่อ/แรงกระแทกน้อยลง.
   → https://pubmed.ncbi.nlm.nih.gov/20581720/
   นั่นแปลว่า cadence ต่ำ + stride ยาว = vertical bounce มาก = เสียพลังงาน
   = HR สูงขึ้นที่ความเร็วเดียวกัน (ตรงกับที่ Nat สังเกตเอง).

2. Garmin Running Dynamics — vertical ratio = vertical oscillation / stride
   length. นักวิ่งที่เก่ง/มีประสบการณ์มี GCT สั้นกว่า, vertical oscillation
   ต่ำกว่า, vertical ratio ต่ำกว่า, cadence สูงกว่า (color gauge อิง percentile).
   → Garmin Forerunner Owner's Manual, "Running Dynamics."

3. Targets จาก config.BASELINES: cadence_min 164 spm | vr_good 8.5% |
   gct_max 275 ms (+20 TM). ใช้ thresholds เดียวกับ post_session_analyzer.

DATA-ANALYTICS DISCIPLINE
   - Trend = least-squares linear slope ต่อ 30 วัน + จำนวน n เสมอ
     (ไม่สรุปจาก 1-2 จุด). Weekly bins ลด noise.
   - Form→HR correlation = Pearson r แต่คุม confounder: ใช้เฉพาะ Easy run
     บนลู่ (treadmill = อุณหภูมิคุมได้) ในกรอบความเร็วแคบ. รายงาน n + ช่วง
     ความเร็ว + เตือนชัดว่า correlation ≠ causation. ถ้า n < 5 = "ข้อมูลไม่พอ".

Usage:
    python3 form_tracker.py                 # 60 วันล่าสุด
    python3 form_tracker.py --days 90
    python3 form_tracker.py --easy-speed 8.4 9.2   # speed band สำหรับ corr
"""

import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.append(str(BASE_DIR.parent / "skills" / "garmin_coach_mcp"))
from config import ATHLETE, BASELINES, ZONE_PCT  # noqa: E402

ACTS_FILE = BASE_DIR / "running_activities_all.json"
RUN_TYPES = {"running", "treadmill_running", "trail_running"}


# ---------------------------------------------------------------------------
# Load activities (live + file, merged & deduped)
# ---------------------------------------------------------------------------
def load_activities(limit=80):
    acts = {}
    # File first (history)
    if ACTS_FILE.exists():
        try:
            for a in json.loads(ACTS_FILE.read_text()):
                acts[a.get("activityId")] = a
        except Exception:
            pass
    # Live overlay (recent runs not yet synced to file)
    try:
        from garmin_client import get_client, garmin_get
        client = get_client()
        for a in garmin_get(client.get_activities, 0, limit):
            acts[a.get("activityId")] = a
    except Exception as e:
        print(f"⚠️  live fetch ไม่ได้ ({e}) — ใช้ไฟล์ history อย่างเดียว")
    return list(acts.values())


def parse_runs(acts, days):
    cutoff = datetime.now() - timedelta(days=days)
    runs = []
    for a in acts:
        tk = (a.get("activityType") or {}).get("typeKey", "")
        if tk not in RUN_TYPES:
            continue
        if a.get("avgVerticalRatio") is None:
            continue
        ts = a.get("startTimeLocal", "")
        try:
            dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if dt < cutoff:
            continue
        cad = a.get("averageRunningCadenceInStepsPerMinute")
        spd = a.get("averageSpeed") or 0
        runs.append({
            "dt": dt,
            "date": ts[:10],
            "tk": tk,
            "is_tm": tk == "treadmill_running",
            "km": round((a.get("distance") or 0) / 1000, 2),
            "cadence": round(cad, 1) if cad else None,
            "vr": a.get("avgVerticalRatio"),
            "vo": a.get("avgVerticalOscillation"),
            "gct": a.get("avgGroundContactTime"),
            "stride": a.get("avgStrideLength"),
            "hr": a.get("averageHR"),
            "kmh": round(spd * 3.6, 2) if spd else None,
        })
    runs.sort(key=lambda r: r["dt"])
    return runs


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------
def _linreg(xs, ys):
    """Least-squares slope + intercept. Returns (slope, intercept) or None."""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return slope, my - slope * mx


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / (sx * sy)


def _r_strength(r):
    a = abs(r)
    if a >= 0.7:
        s = "แรง"
    elif a >= 0.4:
        s = "ปานกลาง"
    elif a >= 0.2:
        s = "อ่อน"
    else:
        s = "แทบไม่มี"
    return s


def trend(runs, key, lower_is_better, unit, target=None):
    pts = [(r, r[key]) for r in runs if r.get(key) is not None]
    if len(pts) < 3:
        return f"   {key:<9}: ข้อมูลไม่พอ (n={len(pts)})"
    t0 = pts[0][0]["dt"]
    xs = [(r["dt"] - t0).total_seconds() / 86400 for r, _ in pts]  # days
    ys = [v for _, v in pts]
    reg = _linreg(xs, ys)
    n = len(pts)
    first = sum(ys[:max(1, n // 3)]) / max(1, n // 3)
    last = sum(ys[-max(1, n // 3):]) / max(1, n // 3)
    if reg is None:
        return f"   {key:<9}: n={n}  avg {sum(ys)/n:.1f}{unit}"
    slope30 = reg[0] * 30  # change per 30 days
    improving = (slope30 < 0) == lower_is_better
    arrow = "↘" if slope30 < 0 else ("↗" if slope30 > 0 else "→")
    verdict = "✅ ดีขึ้น" if improving and abs(slope30) > 0.05 else \
              ("🔴 แย่ลง" if (not improving) and abs(slope30) > 0.05 else "→ คงที่")
    tgt = ""
    if target is not None:
        hit = (last <= target) if lower_is_better else (last >= target)
        tgt = f" | เป้า {target}{unit} {'✅' if hit else '❌'}"
    return (f"   {key:<9}: n={n}  {first:.1f}{arrow}{last:.1f}{unit}  "
            f"({slope30:+.2f}{unit}/30วัน)  {verdict}{tgt}")


# ---------------------------------------------------------------------------
# Form → HR correlation (confounder-controlled)
# ---------------------------------------------------------------------------
def form_hr_corr(runs, speed_lo, speed_hi):
    # Easy HR ceiling (top of Z2 in %HRR terms) to keep only easy runs
    rhr, mhr = ATHLETE["rhr"], ATHLETE["mhr"]
    hrr = mhr - rhr
    easy_ceiling = rhr + hrr * (ZONE_PCT["M"] / 100)  # below threshold

    subset = [r for r in runs
              if r["is_tm"] and r.get("hr") and r.get("kmh")
              and speed_lo <= r["kmh"] <= speed_hi
              and r["hr"] <= easy_ceiling
              and r.get("cadence") and r.get("vr")]
    print(f"\n{'─'*60}")
    print(f"🔗 FORM → HR CORRELATION (confounder-controlled)")
    print(f"   subset: Easy treadmill runs, {speed_lo}–{speed_hi} km/h, "
          f"HR ≤ {easy_ceiling:.0f} bpm")
    print(f"   n = {len(subset)}")
    if len(subset) < 5:
        print("   ⚠️  n < 5 → ข้อมูลไม่พอจะสรุป correlation (avoid overfitting)")
        print("   → เก็บ Easy TM runs ความเร็วใกล้กันเพิ่มอีกสองสามครั้งก่อน")
        return

    cad = [r["cadence"] for r in subset]
    vr = [r["vr"] for r in subset]
    hr = [r["hr"] for r in subset]
    speeds = [r["kmh"] for r in subset]
    sp_range = max(speeds) - min(speeds)

    r_cad = _pearson(cad, hr)
    r_vr = _pearson(vr, hr)
    if r_cad is not None:
        exp = "cadence สูง → HR ต่ำ (ตามทฤษฎี)" if r_cad < 0 else "cadence สูง → HR สูง"
        print(f"   Cadence ↔ HR : r = {r_cad:+.2f} ({_r_strength(r_cad)})  [{exp}]")
    if r_vr is not None:
        exp = "VR สูง → HR สูง (เด้งแนวตั้ง = เปลืองพลังงาน)" if r_vr > 0 else "VR สูง → HR ต่ำ"
        print(f"   VR ↔ HR      : r = {r_vr:+.2f} ({_r_strength(r_vr)})  [{exp}]")
    print(f"\n   📐 caveats (data-analytics honesty):")
    print(f"      • correlation ≠ causation; n={len(subset)} ยังเล็ก")
    print(f"      • speed ในกรอบนี้ยังกระจาย {sp_range:.1f} km/h — เป็น confounder ที่เหลือ")
    print(f"      • heat/sleep/fatigue คุมไม่ได้ครบ → ใช้เป็น signal ไม่ใช่ข้อสรุป")


# ---------------------------------------------------------------------------
# Weekly table
# ---------------------------------------------------------------------------
def weekly_table(runs):
    from collections import defaultdict
    wk = defaultdict(list)
    for r in runs:
        y, w, _ = r["dt"].isocalendar()
        wk[(y, w)].append(r)
    print(f"\n{'─'*60}")
    print(f"📅 WEEKLY FORM (avg)")
    print(f"   {'week':<12}{'runs':<6}{'cad':<8}{'VR':<8}{'GCT':<8}{'VO':<7}")
    for key in sorted(wk.keys()):
        rs = wk[key]
        def avg(k):
            vals = [x[k] for x in rs if x.get(k) is not None]
            return sum(vals) / len(vals) if vals else None
        c, v, g, o = avg("cadence"), avg("vr"), avg("gct"), avg("vo")
        wklabel = f"{key[0]}-W{key[1]:02d}"
        print(f"   {wklabel:<12}{len(rs):<6}"
              f"{(f'{c:.0f}' if c else '-'):<8}"
              f"{(f'{v:.1f}' if v else '-'):<8}"
              f"{(f'{g:.0f}' if g else '-'):<8}"
              f"{(f'{o:.1f}' if o else '-'):<7}")


def main():
    p = argparse.ArgumentParser(description="Running-form trend + form→HR correlation")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--easy-speed", type=float, nargs=2, default=[8.2, 9.4],
                   metavar=("LO", "HI"), help="speed band (km/h) สำหรับ correlation")
    args = p.parse_args()

    acts = load_activities()
    runs = parse_runs(acts, args.days)

    print(f"\n{'='*60}")
    print(f"🏃 FORM TRACKER — {args.days} วันล่าสุด")
    print(f"   วิ่ง {len(runs)} ครั้ง ({runs[0]['date'] if runs else '-'} → "
          f"{runs[-1]['date'] if runs else '-'})")
    print(f"{'='*60}")
    if len(runs) < 3:
        print("❌ ข้อมูลไม่พอ (ต้องมีอย่างน้อย 3 runs ที่มี running dynamics)")
        return

    print(f"\n📈 TRENDS (least-squares, ต่อ 30 วัน)")
    print(trend(runs, "cadence", lower_is_better=False, unit="", target=BASELINES["cadence_min"]))
    print(trend(runs, "vr", lower_is_better=True, unit="%", target=BASELINES["vr_good"]))
    print(trend(runs, "gct", lower_is_better=True, unit="ms", target=BASELINES["gct_max"]))
    print(trend(runs, "vo", lower_is_better=True, unit="mm"))
    print(trend(runs, "stride", lower_is_better=False, unit="cm"))

    weekly_table(runs)
    form_hr_corr(runs, args.easy_speed[0], args.easy_speed[1])
    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
