#!/usr/bin/env python3
"""
injury_risk_detector.py — Early Injury Risk Warning System

วิเคราะห์ trend ของ biomechanical + training load signals ย้อนหลัง 14 วัน
เพื่อตรวจจับความเสี่ยง ACL / Hamstring / Popliteus ล่วงหน้า

Risk Signals:
  1. GCT (Ground Contact Time) เพิ่มขึ้น >5% trend → ขาล้าสะสม, form เสีย
  2. Cadence ลดลง >3% trend → ก้าวยาวขึ้น = impact สูงขึ้น
  3. ATL spike: Training Load เพิ่ม >20% ใน 7 วัน → overtraining risk
  4. Run streak >4 วันไม่มี rest day
  5. Decoupling สูง >10% ต่อเนื่องใน 2 sessions

Usage:
    python3 injury_risk_detector.py              # วิเคราะห์ 14 วันล่าสุด
    python3 injury_risk_detector.py --days 21    # ขยายเป็น 21 วัน
    python3 injury_risk_detector.py --json
"""

from __future__ import annotations

import sys
import json
import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR  = Path(__file__).parent.parent
TOOLS_DIR = Path(__file__).parent
COACH_MCP = BASE_DIR.parent / "skills" / "garmin_coach_mcp"
DATA_FILE = BASE_DIR / "running_activities_all.json"
QUALITY_LOG = BASE_DIR / "QualitySessionLog" / "sessions.json"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))

from config import BASELINES  # noqa: E402

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Risk thresholds
GCT_TREND_WARN    = 0.05   # GCT เพิ่ม >5% จาก baseline
CADENCE_TREND_WARN = 0.03  # Cadence ลด >3% จาก baseline
ACWR_CAUTION      = 1.3    # Acute:Chronic Workload Ratio — บนสุดของ sweet spot (Gabbett 2016)
ACWR_DANGER       = 1.5    # ACWR > 1.5 = injury "danger zone" (Gabbett 2016, BJSM)
DECOUPLE_HIGH     = 10.0   # Decoupling > 10% = signal
MAX_RUN_STREAK    = 4      # วิ่งติดกัน > 4 วัน ไม่มี rest

RISK_LEVELS = {0: "✅ LOW", 1: "🟡 MODERATE", 2: "🔴 HIGH", 3: "🚨 CRITICAL"}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def _load_activities(lookback_days: int) -> list[dict]:
    if not DATA_FILE.exists():
        return []
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            all_acts = json.load(f)
    except Exception:
        return []
    cutoff = date.today() - timedelta(days=lookback_days)
    result = []
    for a in all_acts:
        dt_str = a.get("startTimeLocal", "")[:10]
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dt >= cutoff:
            result.append({**a, "_date": dt})
    return sorted(result, key=lambda x: x["_date"])


def _load_quality_log(lookback_days: int) -> list[dict]:
    """Load session log สำหรับ GCT + Cadence + Decoupling data"""
    if not QUALITY_LOG.exists():
        return []
    try:
        with open(QUALITY_LOG, encoding="utf-8") as f:
            _raw = json.load(f)
            sessions = _raw.get("sessions", []) if isinstance(_raw, dict) else _raw
    except Exception:
        return []
    cutoff = date.today() - timedelta(days=lookback_days)
    result = []
    for s in sessions:
        dt_str = s.get("date", "")[:10]
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dt >= cutoff:
            result.append({**s, "_date": dt})
    return sorted(result, key=lambda x: x["_date"])


def _linear_trend(values: list[float]) -> float:
    """Simple linear regression slope (% change per step) — returns slope/mean"""
    n = len(values)
    if n < 2:
        return 0.0
    mean_v = sum(values) / n
    if mean_v == 0:
        return 0.0
    xs = list(range(n))
    mean_x = (n - 1) / 2
    num = sum((xs[i] - mean_x) * (values[i] - mean_v) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    slope = num / den if den != 0 else 0.0
    return slope / mean_v  # normalized slope (fraction per step)


# ---------------------------------------------------------------------------
# Risk checks
# ---------------------------------------------------------------------------
def _check_gct_trend(quality_sessions: list[dict]) -> dict | None:
    """GCT เพิ่มขึ้น trend → ขาล้า / form เสีย"""
    gct_vals = [s.get("avg_gct") or s.get("gct_avg")
                for s in quality_sessions
                if (s.get("avg_gct") or s.get("gct_avg"))]
    if len(gct_vals) < 3:
        return None

    trend = _linear_trend(gct_vals)
    if trend > GCT_TREND_WARN:
        severity = 2 if trend > GCT_TREND_WARN * 2 else 1
        return {
            "signal":   "GCT Rising Trend",
            "severity": severity,
            "detail":   f"GCT เพิ่มขึ้น {trend*100:.1f}% ต่อ session "
                        f"(last: {gct_vals[-1]:.0f}ms, baseline_max: {BASELINES['gct_max']}ms)",
            "action":   "ลด intensity ลง 1 session, เช็ก form — เน้น Cadence 170+ spm",
        }
    return None


def _check_cadence_trend(quality_sessions: list[dict]) -> dict | None:
    """Cadence ลดลง trend → ก้าวยาวขึ้น = impact เพิ่ม"""
    cad_vals = [s.get("avg_cadence") or s.get("cadence_avg")
                for s in quality_sessions
                if (s.get("avg_cadence") or s.get("cadence_avg"))]
    if len(cad_vals) < 3:
        return None

    trend = _linear_trend(cad_vals)
    if trend < -CADENCE_TREND_WARN:
        severity = 2 if trend < -CADENCE_TREND_WARN * 2 else 1
        return {
            "signal":   "Cadence Dropping Trend",
            "severity": severity,
            "detail":   f"Cadence ลดลง {abs(trend)*100:.1f}% ต่อ session "
                        f"(last: {cad_vals[-1]:.0f} spm, min: {BASELINES['cadence_min']} spm)",
            "action":   "เพิ่ม Strides ใน Easy Run — metronome app ช่วยได้",
        }
    return None


def _check_atl_spike(activities: list[dict]) -> dict | None:
    """Acute:Chronic Workload Ratio (ACWR) — Gabbett 2016 (Br J Sports Med 50:273).

    ACWR = acute (7-day load) / chronic (28-day AVERAGE weekly load).
      • 0.8–1.3 = "sweet spot" (lowest injury risk)
      • 1.3–1.5 = caution
      • > 1.5   = "danger zone" (sharp rise in injury risk)

    Why this replaces the old raw week-over-week %: the chronic 28-day window
    smooths out deload weeks, so a normal week after a deload no longer reads as
    a false "spike + CRITICAL". Load is hrTSS (intensity-aware) when HR exists —
    same engine as training_load.py — else falls back to distance (km).
    """
    # ACWR via EWMA = ATL/CTL. Williams et al. 2017 (BJSM 51:209) showed the
    # rolling-average ACWR is mathematically biased; the EWMA form is preferred.
    # We reuse training_load.compute_current_pmc() (called with NO args so it
    # self-loads the full 200-day warmup history) — same engine as daily_brief /
    # season_summary, so the ratio is consistent and not distorted by data gaps
    # in a fixed 28-day window or by activities missing HR.
    #   ATL (τ=7)  = acute load,  CTL (τ=42) = chronic load
    try:
        from training_load import compute_current_pmc, pmc_confidence
        pmc = compute_current_pmc()
        if not pmc:
            return None
        _, _, ctl, atl, _ = pmc[-1]
    except Exception:
        return None

    # A fresh clone / new athlete with <14 days of history has an
    # artificially low CTL (42-day EWMA hasn't converged yet), so ATL/CTL
    # blows up to an absurd ratio (e.g. 22.9/4.7 = 4.9) that reads as a
    # false CRITICAL — same cold-start problem pmc_confidence() already
    # guards against for the daily_brief PMC section. Reuse it here instead
    # of a second, looser "ctl < 1" heuristic that doesn't actually catch it.
    try:
        _, confidence, _ = pmc_confidence(activities)
        if confidence in ("NONE", "LOW"):
            return None
    except Exception:
        pass

    if ctl < 1:   # ไม่มี baseline พอ (belt-and-suspenders for the confidence check above)
        return None

    acwr = atl / ctl
    if acwr <= ACWR_CAUTION:
        return None

    severity = 3 if acwr > ACWR_DANGER else 2
    return {
        "signal":   "Acute:Chronic Workload Ratio สูง",
        "severity": severity,
        "detail":   f"ACWR {acwr:.2f} (ATL {atl:.0f} / CTL {ctl:.0f}, EWMA) — sweet spot 0.8–1.3",
        "action":   "ACWR > 1.5 = danger zone (Gabbett 2016) — ลด load สัปดาห์หน้าให้ ACWR กลับ < 1.3",
    }


def _check_run_streak(activities: list[dict]) -> dict | None:
    """วิ่งติดกันเกิน MAX_RUN_STREAK วัน ไม่มี rest"""
    run_dates = sorted({a["_date"] for a in activities})
    if len(run_dates) < MAX_RUN_STREAK:
        return None

    max_streak = 1
    cur_streak = 1
    for i in range(1, len(run_dates)):
        if (run_dates[i] - run_dates[i-1]).days == 1:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 1

    if max_streak > MAX_RUN_STREAK:
        severity = 2 if max_streak > MAX_RUN_STREAK + 2 else 1
        return {
            "signal":   f"Run Streak {max_streak} Days",
            "severity": severity,
            "detail":   f"วิ่งติดกัน {max_streak} วันไม่มี rest day (max: {MAX_RUN_STREAK})",
            "action":   "พักอย่างน้อย 1 วันก่อนวิ่ง quality session ถัดไป",
        }
    return None


def _check_decoupling_trend(quality_sessions: list[dict]) -> dict | None:
    """Decoupling สูง >10% ต่อเนื่อง 2 sessions → aerobic stress accumulating"""
    dec_vals = [s.get("decoupling") for s in quality_sessions if s.get("decoupling")]
    if len(dec_vals) < 2:
        return None

    high_count = sum(1 for d in dec_vals[-3:] if d > DECOUPLE_HIGH)
    if high_count >= 2:
        severity = 2 if dec_vals[-1] > 15 else 1
        return {
            "signal":   "High Decoupling Trend",
            "severity": severity,
            "detail":   f"Decoupling >10% ใน {high_count}/3 sessions ล่าสุด "
                        f"(last: {dec_vals[-1]:.1f}%)",
            "action":   "ลด quality intensity — เน้น easy volume ก่อนสัปดาห์หน้า",
        }
    return None


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------
def analyze(lookback_days: int) -> dict:
    activities      = _load_activities(lookback_days)
    quality_sessions = _load_quality_log(lookback_days)

    risks: list[dict] = []

    checks = [
        _check_gct_trend(quality_sessions),
        _check_cadence_trend(quality_sessions),
        _check_atl_spike(activities),
        _check_run_streak(activities),
        _check_decoupling_trend(quality_sessions),
    ]
    risks = [c for c in checks if c is not None]

    if not risks:
        overall_severity = 0
    else:
        overall_severity = min(3, max(r["severity"] for r in risks))

    # Specific injury site mapping
    injury_sites = []
    for r in risks:
        sig = r["signal"]
        if "GCT" in sig or "Cadence" in sig:
            injury_sites.append("⚠️  Popliteus / Hamstring (form-related)")
        if "ATL" in sig or "Streak" in sig:
            injury_sites.append("⚠️  Shin / IT Band (overuse)")
        if "Decoupling" in sig:
            injury_sites.append("⚠️  General fatigue — ACL risk ถ้า form เสีย")

    return {
        "analysis_date":    date.today().isoformat(),
        "lookback_days":    lookback_days,
        "activities_found": len(activities),
        "quality_sessions_found": len(quality_sessions),
        "overall_risk":     RISK_LEVELS[overall_severity],
        "risk_score":       overall_severity,
        "injury_sites":     list(set(injury_sites)),
        "risks":            risks,
        "data_quality":     "limited — install Stryd for GCT/Cadence" if not quality_sessions else "ok",
    }


def print_report(r: dict):
    print(f"\n{'='*57}")
    print(f"🩺 INJURY RISK DETECTOR — {r['analysis_date']}")
    print(f"   Lookback: {r['lookback_days']} วัน | {r['activities_found']} runs analyzed")
    print(f"{'='*57}")
    print(f"\n🎯 Overall Risk: {r['overall_risk']}")

    if r['injury_sites']:
        print(f"\n🦵 Risk Sites:")
        for site in r['injury_sites']:
            print(f"   {site}")

    if not r['risks']:
        print("\n✅ ไม่พบ risk signals — training load ดูสมเหตุสมผล")
    else:
        print(f"\n⚠️  Risk Signals ({len(r['risks'])} found):")
        for i, risk in enumerate(r['risks'], 1):
            sev_icon = "🔴" if risk['severity'] >= 2 else "🟡"
            print(f"\n   {i}. {sev_icon} {risk['signal']}")
            print(f"      Detail: {risk['detail']}")
            print(f"      Action: {risk['action']}")

    if r['data_quality'] != "ok":
        print(f"\n💡 Note: {r['data_quality']}")

    print(f"\n{'='*57}\n")


def main():
    parser = argparse.ArgumentParser(description="Injury Risk Detector")
    parser.add_argument("--days", type=int, default=14,
                        help="Lookback window in days (default: 14)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = analyze(args.days)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
