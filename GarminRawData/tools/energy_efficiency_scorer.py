#!/usr/bin/env python3
"""
energy_efficiency_scorer.py — Gel/Electrolyte Efficiency Scorer

เปรียบเทียบ Stamina Drain vs Body Battery ข้าม Long Runs
เพื่อประเมินว่า nutrition strategy ไหนทำให้ประสิทธิภาพดีที่สุด

Metrics:
  - Stamina Drain % (จาก post_session_analyzer log)
  - BB drop (BB before - BB after)
  - Efficiency Score = (Stamina Retained) / (BB Consumed)
  - สูง = ร่างกายได้พลังงานดี, ต่ำ = ล้าเร็ว

Usage:
    python3 energy_efficiency_scorer.py
    python3 energy_efficiency_scorer.py --sessions 6
    python3 energy_efficiency_scorer.py --json
"""

from __future__ import annotations

import sys
import json
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR     = Path(__file__).parent.parent
TOOLS_DIR    = Path(__file__).parent
COACH_MCP    = BASE_DIR.parent / "skills" / "garmin_coach_mcp"
QUALITY_LOG  = BASE_DIR / "QualitySessionLog" / "sessions.json"
MASTER_JSON  = BASE_DIR / "QualitySessionLog" / "sessions_master.json"
HEALTH_CACHE = BASE_DIR / "wellness"

LONG_RUN_KM      = 14.0   # minimum km to qualify as Long Run
LONG_RUN_TYPES   = {"Easy Run", "Marathon Pace", "long", "long_run", "easy", "Easy"}
# Exclude quality sessions from Long Run detection (sessions_master uses these types)
QUALITY_TYPES    = {"Threshold (T)", "Interval (I)", "Tempo", "Fast Finish",
                    "threshold", "interval", "tempo"}

ACTIVITIES_JSON  = BASE_DIR / "running_activities_all.json"


def _load_activities_index() -> dict[int, dict]:
    """Load running_activities_all.json indexed by activityId for pace fallback."""
    if not ACTIVITIES_JSON.exists():
        return {}
    try:
        acts = json.loads(ACTIVITIES_JSON.read_text())
        return {int(a["activityId"]): a for a in acts if a.get("activityId")}
    except Exception:
        return {}


def _pace_from_speed(speed_ms: float | None) -> str | None:
    """Convert m/s → 'M:SS/km' string, or None if invalid."""
    if not speed_ms or speed_ms <= 0:
        return None
    secs = 1000 / speed_ms
    return f"{int(secs // 60)}:{int(secs % 60):02d}/km"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(COACH_MCP))


def _load_sessions(max_sessions: int) -> list[dict]:
    """Load Long Run sessions from sessions_master (primary) + sessions.json (fallback).

    sessions_master has ALL sessions (easy + quality) including Long Runs that are
    never logged to sessions.json (which only stores quality sessions via --update-log).
    Deduplicates by activity_id, preferring sessions.json records (richer stats).
    """
    seen_ids: set = set()
    combined: list[dict] = []

    # --- Primary: sessions_master (has all easy/long runs) ---
    if MASTER_JSON.exists():
        try:
            with open(MASTER_JSON, encoding="utf-8") as f:
                master_data = json.load(f)
            for s in master_data.get("sessions", []):
                dist = s.get("distance_km") or s.get("total_km") or 0
                stype = s.get("session_type", "")
                if dist >= LONG_RUN_KM and stype not in QUALITY_TYPES:
                    aid = s.get("activity_id")
                    if aid:
                        seen_ids.add(aid)
                    combined.append(s)
        except Exception:
            pass

    # --- Fallback: sessions.json (richer stats — stamina_drain_pct etc.) ---
    if QUALITY_LOG.exists():
        try:
            with open(QUALITY_LOG, encoding="utf-8") as f:
                _raw = json.load(f)
            for s in (_raw.get("sessions", []) if isinstance(_raw, dict) else _raw):
                dist  = s.get("distance_km", 0)
                stype = s.get("session_type", "")
                aid   = s.get("activity_id")
                if dist >= LONG_RUN_KM and (stype in LONG_RUN_TYPES or dist >= LONG_RUN_KM):
                    if aid and aid in seen_ids:
                        # Replace master record with richer sessions.json record
                        combined = [x for x in combined if x.get("activity_id") != aid]
                    combined.append(s)
                    if aid:
                        seen_ids.add(aid)
        except Exception:
            pass

    long_runs = sorted(combined, key=lambda x: x.get("date", ""))
    return long_runs[-max_sessions:]


def _get_bb_for_date(date_str: str) -> tuple[int | None, int | None]:
    """คืน (bb_before, bb_after) จาก health cache"""
    path = HEALTH_CACHE / f"wellness_{date_str}.json"
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text())
        return data.get("bb_high"), data.get("bb_low")
    except Exception:
        return None, None


def _efficiency_score(stamina_drain_pct: float | None, bb_drop: float | None) -> float | None:
    """
    Efficiency = stamina retained per BB unit consumed
    Higher = better energy utilization
    """
    if stamina_drain_pct is None or bb_drop is None or bb_drop <= 0:
        return None
    stamina_retained = 100 - stamina_drain_pct
    return round(stamina_retained / bb_drop, 2)


def _score_label(score: float | None) -> str:
    if score is None:
        return "N/A"
    if score >= 1.5:
        return "✅ Excellent"
    if score >= 1.0:
        return "🟡 Good"
    if score >= 0.7:
        return "🟠 Moderate"
    return "🔴 Poor"


def analyze(max_sessions: int) -> dict:
    sessions = _load_sessions(max_sessions)

    if not sessions:
        return {
            "error": (
                f"ไม่พบ Long Run sessions (≥{LONG_RUN_KM}km) ใน sessions_master.json หรือ sessions.json\n"
                "รัน run_post_long.sh หลัง Long Run เพื่อให้ได้ stamina_drain_pct ด้วย"
            )
        }

    activities_idx = _load_activities_index()

    scored = []
    for s in sessions:
        date_str = s.get("date", "")[:10]
        dist     = s.get("distance_km") or s.get("total_km")
        stamina  = s.get("stamina_drain_pct") or s.get("stamina_drain")

        # BB: prefer direct bb_start/bb_end from sessions_master, fallback to health_cache
        bb_start = s.get("bb_start")
        bb_end   = s.get("bb_end")
        if bb_start is not None and bb_end is not None:
            bb_high, bb_low = bb_start, bb_end
        else:
            bb_high, bb_low = _get_bb_for_date(date_str)
        bb_drop = (bb_high - bb_low) if (bb_high is not None and bb_low is not None) else None

        eff = _efficiency_score(stamina, bb_drop)

        # pace: prefer stored value, fallback to running_activities_all.json via averageSpeed
        avg_pace = s.get("avg_pace")
        if avg_pace is None:
            aid = s.get("activity_id")
            if aid and int(aid) in activities_idx:
                avg_pace = _pace_from_speed(activities_idx[int(aid)].get("averageSpeed"))

        scored.append({
            "date":             date_str,
            "distance_km":      dist,
            "avg_pace":         avg_pace,
            "avg_hr":           s.get("avg_hr"),
            "stamina_drain_pct": stamina,
            "bb_start":         bb_high,
            "bb_end":           bb_low,
            "bb_drop":          bb_drop,
            "efficiency_score": eff,
            "efficiency_label": _score_label(eff),
            "nutrition_note":   s.get("nutrition_note", "ไม่มีบันทึก"),
        })

    scored.sort(key=lambda x: x["date"])

    # Rank by efficiency
    with_score = [s for s in scored if s["efficiency_score"] is not None]
    ranked     = sorted(with_score, key=lambda x: x["efficiency_score"], reverse=True)

    # Trend
    scores = [s["efficiency_score"] for s in scored if s["efficiency_score"]]
    trend_note = None
    if len(scores) >= 3:
        if scores[-1] > scores[0]:
            trend_note = "📈 Energy efficiency กำลังดีขึ้น — nutrition strategy ใช้งานได้"
        elif scores[-1] < scores[0]:
            trend_note = "📉 Energy efficiency ลดลง — ลองเปลี่ยน timing หรือ dose"
        else:
            trend_note = "➡️  Stable — ลองทดลองเปลี่ยน nutrition เพื่อหาค่าที่ดีกว่า"

    return {
        "sessions_analyzed": len(scored),
        "trend":             trend_note,
        "best_session":      ranked[0] if ranked else None,
        "worst_session":     ranked[-1] if ranked else None,
        "sessions":          scored,
    }


def print_report(r: dict):
    if "error" in r:
        print(f"❌ {r['error']}")
        return

    print(f"\n{'='*60}")
    print(f"⚡ ENERGY EFFICIENCY SCORER — Long Run Analysis")
    print(f"   Sessions analyzed: {r['sessions_analyzed']}")
    print(f"{'='*60}")

    if r['trend']:
        print(f"\n📈 Trend: {r['trend']}")

    if r['best_session']:
        b = r['best_session']
        print(f"\n🏆 Best Session:")
        print(f"   {b['date']} | {b['distance_km']}km | Score: {b['efficiency_score']} {b['efficiency_label']}")
        print(f"   Stamina drain: {b['stamina_drain_pct']}% | BB {b['bb_start']}→{b['bb_end']} (drop {b['bb_drop']})")
        print(f"   Nutrition: {b['nutrition_note']}")

    print(f"\n📊 All Sessions:")
    print(f"   {'Date':<12} {'Dist':>6} {'Drain':>7} {'BB start→end':>13} {'Drop':>5} {'Score':>7} {'Label':<12}")
    print(f"   {'-'*72}")
    for s in r['sessions']:
        drain  = f"{s['stamina_drain_pct']:.0f}%" if s['stamina_drain_pct'] else "N/A"
        bb_str = f"{s['bb_start']}→{s['bb_end']}" if s['bb_start'] is not None else "N/A"
        bbdrop = f"{s['bb_drop']}" if s['bb_drop'] is not None else "N/A"
        score  = f"{s['efficiency_score']}" if s['efficiency_score'] else "N/A"
        dist_s = f"{s['distance_km']}km" if s['distance_km'] else "N/A"
        print(f"   {s['date']:<12} {dist_s:>6} {drain:>7} {bb_str:>13} {bbdrop:>5} "
              f"{score:>7} {s['efficiency_label']:<12}")

    print(f"\n💡 เพิ่ม nutrition_note ใน session log เพื่อ track ว่าใช้ gel/palatinose แบบไหน")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Energy Efficiency Scorer")
    parser.add_argument("--sessions", type=int, default=8, help="Max sessions to analyze")
    parser.add_argument("--json",     action="store_true")
    args = parser.parse_args()

    result = analyze(args.sessions)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
