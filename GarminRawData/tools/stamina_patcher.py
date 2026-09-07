#!/usr/bin/env python3
"""
stamina_patcher.py — Auto-patch stamina_drain_pct into sessions_master.json

อ่าน directAvailableStamina จาก SessionCache (downloaded แล้วโดย post_session_analyzer)
→ ไม่ยิง API เพิ่มเลย

เรียกอัตโนมัติจาก run_post_easy.sh และ run_post_quality.sh หลังทุก session

Usage:
    python3 stamina_patcher.py                    # patch latest session
    python3 stamina_patcher.py --id 22906125783   # patch specific activity
    python3 stamina_patcher.py --backfill         # patch all sessions ที่ยังไม่มี stamina
    python3 stamina_patcher.py --status           # show backfill progress (no writes)
"""

from __future__ import annotations
import sys
import os
import json
import argparse
from pathlib import Path

TOOLS_DIR     = Path(__file__).parent
BASE_DIR      = TOOLS_DIR.parent
MASTER_JSON   = BASE_DIR / "QualitySessionLog" / "sessions_master.json"
SESSION_CACHE = BASE_DIR / "SessionCache"


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _parse_stamina_from_cache(activity_id: int) -> tuple[int | None, int | None]:
    """
    Extract (stam_start, stam_end) from SessionCache.
    SessionCache ถูก populate โดย post_session_analyzer → ไม่ต้องยิง API ใหม่

    Returns (None, None) ถ้า:
      - ยังไม่มี cache (post_session_analyzer ยังไม่รัน)
      - activity นั้นไม่มี directAvailableStamina metric (indoor GPS-only บางรุ่น)
    """
    cache_file = SESSION_CACHE / f"session_{activity_id}.json"
    if not cache_file.exists():
        return None, None

    try:
        data      = json.loads(cache_file.read_text(encoding="utf-8"))
        desc_list = data.get("metricDescriptors", [])
        metrics   = data.get("activityDetailMetrics", [])

        # Build key → metricsIndex map
        desc_map = {m.get("key"): m.get("metricsIndex") for m in desc_list}
        stam_idx = desc_map.get("directAvailableStamina")
        if stam_idx is None:
            return None, None

        stams = [
            p["metrics"][stam_idx]
            for p in metrics
            if p["metrics"][stam_idx] is not None
        ]
        if not stams:
            return None, None

        return int(stams[0]), int(stams[-1])

    except Exception as e:
        print(f"  ⚠️  stamina parse error: {e}")
        return None, None


def _patch_one(s: dict) -> bool:
    """
    Patch stam_start / stam_end / stamina_drain_pct into session dict in-place.
    Returns True if patched (changes made), False if skipped.
    """
    date_str = s.get("date", "?")[:10]

    # Already patched → skip
    if s.get("stamina_drain_pct") is not None:
        print(f"  ⏭  {date_str} — already patched ({s['stamina_drain_pct']}% drain)")
        return False

    act_id = s.get("activity_id")
    if not act_id:
        print(f"  ⚠️  {date_str} — no activity_id, skip")
        return False

    stam_start, stam_end = _parse_stamina_from_cache(act_id)

    if stam_start is None:
        cache_file = SESSION_CACHE / f"session_{act_id}.json"
        if not cache_file.exists():
            print(f"  ⏭  {date_str} — no SessionCache yet (post_session_analyzer runs first)")
        else:
            # Mark permanently so --backfill doesn't retry this activity forever
            s.setdefault("stamina_unavailable", True)
            print(f"  ⚠️  {date_str} — no stamina metric in activity (GPS-only watch mode?) [marked stamina_unavailable]")
        return False

    drain = stam_start - stam_end
    s["stam_start"]        = stam_start
    s["stam_end"]          = stam_end
    s["stamina_drain_pct"] = drain

    label = "🟢" if drain <= 30 else "🟡" if drain <= 50 else "🔴"
    print(f"  ✅ {date_str} — {stam_start}% → {stam_end}% | drain {drain}% {label}")
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def patch(activity_id: int | None = None, backfill: bool = False) -> int:
    """
    Patch stamina into sessions_master.json.
    Returns number of sessions updated.
    """
    if not MASTER_JSON.exists():
        print("❌ sessions_master.json not found")
        return 0

    with open(MASTER_JSON, encoding="utf-8") as f:
        master = json.load(f)

    sessions = master.get("sessions", [])
    if not sessions:
        print("⚠️  sessions_master.json is empty")
        return 0

    changed = 0

    if backfill:
        # Skip sessions already marked as having no stamina metric (GPS-only watch)
        missing = [s for s in sessions
                   if s.get("stamina_drain_pct") is None and not s.get("stamina_unavailable")]
        print(f"🔄 Backfill stamina — {len(missing)} sessions missing (of {len(sessions)} total)")
        for s in missing:
            if _patch_one(s):
                changed += 1

    elif activity_id:
        target = next((s for s in sessions if s.get("activity_id") == activity_id), None)
        if not target:
            print(f"❌ Activity {activity_id} not found in sessions_master.json")
            return 0
        if _patch_one(target):
            changed += 1

    else:
        # Default: patch latest session — pick by activity_id (monotonic with
        # time) rather than sessions[0]/date string, since a non-ISO "date"
        # value (e.g. "custom") sorts lexicographically above any real date
        # and would otherwise pin a stale session at position 0 forever.
        with_id = [s for s in sessions if s.get("activity_id")]
        if not with_id:
            print("⚠️  no session with activity_id found")
            return 0
        latest = max(with_id, key=lambda s: s["activity_id"])
        if _patch_one(latest):
            changed += 1

    if changed > 0:
        tmp = MASTER_JSON.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(master, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MASTER_JSON)  # atomic — safe against crash mid-write
        print(f"💾 sessions_master.json saved — {changed} session(s) updated")
    else:
        print("  — No changes needed")

    return changed


# ---------------------------------------------------------------------------
# Status reporter (read-only)
# ---------------------------------------------------------------------------

def status_report() -> None:
    """Print backfill progress without modifying anything."""
    if not MASTER_JSON.exists():
        print("❌ sessions_master.json not found")
        return

    with open(MASTER_JSON, encoding="utf-8") as f:
        master = json.load(f)

    sessions = master.get("sessions", [])
    total    = len(sessions)
    patched  = [s for s in sessions if s.get("stamina_drain_pct") is not None]
    unavail  = [s for s in sessions if s.get("stamina_unavailable")]
    pending  = [s for s in sessions
                if s.get("stamina_drain_pct") is None and not s.get("stamina_unavailable")]

    print("=" * 60)
    print("📊 STAMINA BACKFILL STATUS")
    print("=" * 60)
    print(f"  Total sessions       : {total}")
    print(f"  ✅ Patched           : {len(patched)}")
    print(f"  ⚠️  Unavailable       : {len(unavail)} (GPS-only watch, marked)")
    print(f"  ⏳ Pending backfill  : {len(pending)}")
    print("-" * 60)

    if pending:
        print("Pending sessions (need post_session_analyzer to cache first):")
        for s in pending[:15]:
            date_str = s.get("date", "?")[:10]
            act_id   = s.get("activity_id", "?")
            cache    = SESSION_CACHE / f"session_{act_id}.json"
            tag      = "📥 cache ready" if cache.exists() else "❌ no cache"
            print(f"   • {date_str} | id={act_id} | {tag}")
        if len(pending) > 15:
            print(f"   … and {len(pending) - 15} more")
        print()
        print("💡 Run `python3 stamina_patcher.py --backfill` to process cached ones")
    else:
        print("✨ All sessions accounted for — nothing pending")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Patch stamina_drain_pct into sessions_master.json from SessionCache"
    )
    parser.add_argument("--id",       type=int, default=None,
                        help="Activity ID to patch (default: latest session)")
    parser.add_argument("--backfill", action="store_true",
                        help="Patch all sessions missing stamina_drain_pct")
    parser.add_argument("--status",   action="store_true",
                        help="Show backfill progress (read-only, no writes)")
    args = parser.parse_args()

    if args.status:
        status_report()
        return

    patch(args.id, args.backfill)


if __name__ == "__main__":
    main()
