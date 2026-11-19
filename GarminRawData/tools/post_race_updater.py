#!/usr/bin/env python3
"""
post_race_updater.py — Race Result → VDOT → New Training Paces

⚠️  MODEL-AGNOSTIC GUARD — Tool บังคับ protocol นี้เสมอ ไม่ว่าจะใช้ LLM ใด (Claude / Gemini / GPT)
    Logic อยู่ใน code ไม่ใช่ใน LLM context → ผลลัพธ์เหมือนกันทุก model

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VDOT Update Decision Tree (Jack Daniels Principle)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CASE 1: Race temp > 20°C (Bangkok / hot conditions)
  → ต้องใส่ --temp → Ely heat correction อัตโนมัติ
  → แสดง raw VDOT + heat-adj VDOT
  → ห้าม --apply (race HR confounded by heat+adrenaline+drift)
  → ให้ทำ 30-min TT บน TM แล้วใช้ --tt-confirmed แทน

CASE 2: Race temp ≤ 20°C (cool / controlled)
  → apply ได้เลย — conditions แม่น

CASE 3: TM Time Trial (30-min TT protocol)
  → ใช้ --tt-confirmed → apply ได้เลย
  → นี่คือ gold standard สำหรับ LT2 + T-pace calibration

CASE 4: Training session estimate
  → ห้าม apply เด็ดขาด → ใช้ race_predictor.py เพื่อดู trend เท่านั้น

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Usage:
    # Race ร้อน (> 20°C) — แสดง heat-adj เท่านั้น ไม่ apply
    python3 post_race_updater.py hm 1:57:34 --temp 27 --humidity 82

    # Race เย็น (≤ 20°C) — apply ได้
    python3 post_race_updater.py hm 1:52:30 --temp 18
    python3 post_race_updater.py hm 1:52:30 --temp 18 --apply

    # TM Time Trial — gold standard, apply ได้เลย
    python3 post_race_updater.py hm 1:47:00 --tt-confirmed
    python3 post_race_updater.py hm 1:47:00 --tt-confirmed --apply

    # ไม่ใส่ --temp → ERROR (ต้องระบุเสมอ เพื่อให้ระบบ guard ได้)
"""

from __future__ import annotations

import sys
import json
import argparse
from datetime import date
from pathlib import Path

TOOLS_DIR    = Path(__file__).parent
BASE_DIR     = TOOLS_DIR.parent
ATHLETE_JSON = BASE_DIR / "athlete.json"

sys.path.insert(0, str(TOOLS_DIR))
from vdot_math import compute_vdot as _vdot_math_compute, jd_paces  # noqa: E402
from bangkok_climate import ely_penalty_fraction  # noqa: E402

# ---------------------------------------------------------------------------
# Race distances (metres)
# ---------------------------------------------------------------------------
DISTANCES = {
    "hm":  21097.5,
    "m":   42195.0,
    "5k":   5000.0,
    "10k": 10000.0,
}

# Temperature threshold — above this, heat correction required & apply blocked
HOT_THRESHOLD_C = 20.0

# ---------------------------------------------------------------------------
# Heat correction — the shared Ely model (bangkok_climate), not a copy
# ---------------------------------------------------------------------------
def ely_heat_correction(temp_c: float) -> float:
    """Pace penalty fraction (e.g. 0.068 = 6.8 % slower) — bangkok_climate."""
    return ely_penalty_fraction(temp_c)


def heat_adjusted_duration(duration_s: float, temp_c: float) -> float:
    """Return cool-equivalent duration (faster = higher VDOT)."""
    return duration_s / (1 + ely_heat_correction(temp_c))


# ---------------------------------------------------------------------------
# Core VDOT calculations
# ---------------------------------------------------------------------------
def parse_time(t: str) -> float:
    parts = [int(p) for p in t.strip().split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    raise ValueError(f"Cannot parse time: {t}")


def compute_vdot(dist_m: float, duration_s: float) -> float:
    result = _vdot_math_compute(dist_m, duration_s / 60, min_dist_m=0)
    return round(result, 1) if result else 0.0


def fmt_pace(sec: int) -> str:
    m, s_ = divmod(int(sec), 60)
    return f"{m}:{s_:02d}/km"


# ---------------------------------------------------------------------------
# athlete.json read / write — the single source of truth (config.py only
# ever *derives* from this file on import; never hand-edit config.py here).
# ---------------------------------------------------------------------------
def _load_athlete() -> dict:
    return json.loads(ATHLETE_JSON.read_text(encoding="utf-8"))


def _save_athlete(data: dict) -> None:
    ATHLETE_JSON.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def read_current_vdot() -> int:
    return _load_athlete().get("vdot")


def apply_to_athlete_json(new_vdot: int, calibration_source: str,
                          heat_adj_estimate: float | None) -> bool:
    """Update vdot + calibration metadata in athlete.json. Paces are NOT
    stored (v1.8: config derives them from vdot via vdot_math.jd_paces);
    a stale vdot_paces_sec key is removed if present."""
    data = _load_athlete()
    data["vdot"]                  = new_vdot
    data["vdot_source"]           = calibration_source
    data["vdot_calibration_date"] = date.today().isoformat()
    data["vdot_calibration"]      = "confirmed"
    data["vdot_heat_adj_estimate"] = round(heat_adj_estimate, 1) if heat_adj_estimate else None
    data.pop("vdot_paces_sec", None)
    _save_athlete(data)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Post-race VDOT updater — model-agnostic heat guard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("race", choices=list(DISTANCES.keys()),
                        help="Race type: hm / m / 5k / 10k")
    parser.add_argument("time", help="Finish time HH:MM:SS or MM:SS")
    parser.add_argument("--temp", type=float, default=None,
                        help="Race temperature °C (REQUIRED for races — omit only with --tt-confirmed)")
    parser.add_argument("--humidity", type=float, default=60.0,
                        help="Humidity %% (default 60, for display only)")
    parser.add_argument("--tt-confirmed", action="store_true",
                        help="TM Time Trial result — bypasses heat guard, apply allowed")
    parser.add_argument("--apply", action="store_true",
                        help="Apply changes to athlete.json (blocked for hot races)")
    args = parser.parse_args()

    # ── Guard: require --temp unless TT ────────────────────────────────────
    if not args.tt_confirmed and args.temp is None:
        print("\n❌ ERROR: --temp ต้องระบุเสมอ (Jack Daniels protocol)")
        print("   เหตุผล: อุณหภูมิกำหนดว่า VDOT จาก race นี้ apply ได้หรือไม่")
        print()
        print("   Race:         python3 post_race_updater.py hm 1:57:34 --temp 27")
        print("   TM Time Trial: python3 post_race_updater.py hm 1:47:00 --tt-confirmed")
        sys.exit(1)

    dist_m     = DISTANCES[args.race]
    duration_s = parse_time(args.time)
    race_label = {"hm": "Half Marathon", "m": "Marathon",
                  "5k": "5K", "10k": "10K"}[args.race]
    dur_hms    = f"{int(duration_s//3600)}:{int((duration_s%3600)//60):02d}:{int(duration_s%60):02d}"
    current_vdot = read_current_vdot()

    # ── Compute raw VDOT ────────────────────────────────────────────────────
    raw_vdot = compute_vdot(dist_m, duration_s)

    # ── Heat correction ─────────────────────────────────────────────────────
    is_hot      = (not args.tt_confirmed) and args.temp is not None and args.temp > HOT_THRESHOLD_C
    heat_adj_s  = None
    heat_vdot   = None
    ely_penalty = 0.0

    if not args.tt_confirmed and args.temp is not None:
        ely_penalty  = ely_heat_correction(args.temp)
        heat_adj_s   = heat_adjusted_duration(duration_s, args.temp)
        heat_vdot    = compute_vdot(dist_m, heat_adj_s)

    # ── Determine apply VDOT ────────────────────────────────────────────────
    apply_vdot = raw_vdot  # default: use raw (cool race or TT)

    # ── Print report ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"🏁 POST-RACE VDOT UPDATER")
    print(f"{'='*60}")
    print(f"   Race    : {race_label}")
    print(f"   Result  : {dur_hms}")
    if args.tt_confirmed:
        print(f"   Mode    : ✅ TM Time Trial (30-min TT) — gold standard")
    else:
        print(f"   Temp    : {args.temp}°C | Humidity: {args.humidity:.0f}%")

    print(f"\n📊 VDOT Analysis:")
    print(f"   Current VDOT   : {current_vdot}")
    print(f"   Raw VDOT       : {raw_vdot}  (จากเวลาจริง {dur_hms})")

    if heat_vdot is not None:
        print(f"   Ely Penalty    : +{ely_penalty*100:.1f}% (Ely 2007 — {args.temp}°C)")
        adj_hms = f"{int(heat_adj_s//3600)}:{int((heat_adj_s%3600)//60):02d}:{int(heat_adj_s%60):02d}"
        print(f"   Cool-equiv time: {adj_hms}")
        print(f"   Heat-adj VDOT  : {heat_vdot}  ← fitness จริง (เทียบเท่า cool conditions)")

    # ── Heat guard ──────────────────────────────────────────────────────────
    if is_hot:
        print(f"\n🌡️  HOT RACE GUARD — {args.temp}°C > {HOT_THRESHOLD_C}°C")
        print(f"   ❌ ห้าม apply raw VDOT {raw_vdot} เข้า athlete.json โดยตรง")
        print(f"      เหตุผล: race HR confounded by heat + adrenaline + cardiovascular drift")
        print(f"              raw VDOT underestimates fitness จริง {heat_vdot - raw_vdot:.1f} จุด")
        print(f"\n   ✅ สิ่งที่ต้องทำ:")
        print(f"      1. บันทึก heat-adj estimate = {heat_vdot} ลง athlete.json (vdot_heat_adj_estimate) — ต้องใส่ --apply")
        print(f"      2. ทำ 30-min TT บน TM ใน 20°C controlled conditions")
        print(f"      3. รัน: python3 post_race_updater.py {args.race} [TT_time] --tt-confirmed --apply")
        # Only heat_adj_estimate (never vdot) — and only with --apply
        # (v1.8.1: a dry-run used to write athlete.json silently).
        if args.apply:
            data = _load_athlete()
            data["vdot_heat_adj_estimate"] = heat_vdot
            _save_athlete(data)
            print(f"   💾 athlete.json → vdot_heat_adj_estimate = {heat_vdot} (updated, zones unchanged)")
        else:
            print(f"   💡 Dry-run — เพิ่ม --apply เพื่อบันทึก heat-adj estimate (zones ไม่เปลี่ยน)")
        print(f"\n{'='*60}\n")
        return

    # ── Cool race or TT → compute paces ─────────────────────────────────────
    new_ranges = jd_paces(apply_vdot)   # same derivation config.py uses

    print(f"\n🏃 New Training Paces (VDOT {apply_vdot}):")
    zone_labels = {"E": "Easy (E)", "M": "Marathon (M)", "T": "Threshold (T)",
                   "I": "Interval (I)", "R": "Repetition (R)"}
    for zone in ("E", "M", "T", "I", "R"):
        lo, hi = new_ranges[zone]
        print(f"   {zone_labels[zone]:<16}: {fmt_pace(lo)} – {fmt_pace(hi)}")

    delta = apply_vdot - current_vdot
    arrow = "⬆" if delta > 0 else "⬇" if delta < 0 else "="
    print(f"\n   VDOT: {current_vdot} → {apply_vdot}  {arrow} ({'+' if delta >= 0 else ''}{delta:.1f})")

    calibration_source = "tt_tm_30min" if args.tt_confirmed else f"race_{args.race}_cool"

    if args.apply:
        new_vdot_int = round(apply_vdot)
        apply_to_athlete_json(new_vdot_int, calibration_source, heat_vdot)
        print(f"\n✅ athlete.json updated (single source — config.py re-derives on next import):")
        print(f"   vdot                  = {new_vdot_int}")
        print(f"   vdot_source           = {calibration_source}")
        print(f"   vdot_calibration_date = {date.today().isoformat()}")
        print(f"   vdot_calibration      = confirmed")
        print(f"   paces                 = derived from vdot by config (not stored)")
    else:
        print(f"\n💡 Dry-run — เพิ่ม --apply เพื่ออัปเดต athlete.json จริง")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
