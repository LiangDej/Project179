#!/usr/bin/env python3
"""backfill_session_metrics.py — one-off backfill of run-window Body Battery and
Stamina for OLD records in sessions_master.json.

Since v1.7 session_logger writes both correctly at log time
(session_logger.run_window_bb / stamina_from_details), so this is NOT part of
the post-run pipeline any more. It replaces fix_session_bb.py and
stamina_patcher.py, which used to overwrite every new record after logging.

Only touches records that are missing the value:
  - BB      : bb_source is empty (old records stored the day's peak / sync-time BB)
  - Stamina : stamina_drain_pct is None and not marked stamina_unavailable

Usage (from GarminRawData/tools, venv python):
  ../../.venv/bin/python3.13 ../backfill_session_metrics.py --dry-run
  ../../.venv/bin/python3.13 ../backfill_session_metrics.py
  ../../.venv/bin/python3.13 ../backfill_session_metrics.py --id 123
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, str(HERE.parent / "skills" / "garmin_coach_mcp"))

MASTER = HERE / "QualitySessionLog" / "sessions_master.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, help="only this activity_id (re-computes even if set)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import session_logger as sl
    import post_session_analyzer as psa

    raw = json.loads(MASTER.read_text(encoding="utf-8"))
    sessions = raw.get("sessions", raw) if isinstance(raw, dict) else raw
    client = None
    changed, skipped = 0, []

    for s in sessions:
        aid = s.get("activity_id")
        if not str(aid).isdigit() or (args.id and int(aid) != args.id):
            continue
        need_bb = args.id or not s.get("bb_source")
        need_st = args.id or (s.get("stamina_drain_pct") is None and not s.get("stamina_unavailable"))
        if not (need_bb or need_st):
            continue
        try:
            details = psa.fetch_activity(int(aid))
        except Exception as e:
            skipped.append((s.get("date"), aid, f"fetch failed: {e}"))
            continue
        msg = []
        if need_bb:
            client = client or sl.get_client()
            bb = sl.run_window_bb(details, fallback_start=s.get("bb_start"), client=client)
            if bb["bb_end"] is not None:
                msg.append(f"BB {s.get('bb_start')}->{s.get('bb_end')} => {bb['bb_start']}->{bb['bb_end']} [{bb['bb_source']}]")
                s.update(bb)
            else:
                # v1.7.1: Garmin no longer has the run-window timeline for
                # these. Clear the old day-peak/sync-time numbers (e.g. 69→5)
                # so no tool reads them as a run drain, and mark it so the
                # backfill stops retrying.
                if s.get("bb_source") != "unavailable":
                    msg.append(f"BB {s.get('bb_start')}->{s.get('bb_end')} => unavailable")
                    s.update({"bb_start": None, "bb_end": None, "bb_source": "unavailable"})
                skipped.append((s.get("date"), aid, "no reliable BB for run window → marked unavailable"))
        if need_st:
            st0, st1 = sl.stamina_from_details(details)
            if st0 is not None:
                s.update({"stam_start": st0, "stam_end": st1, "stamina_drain_pct": st0 - st1})
                msg.append(f"stamina {st0}->{st1}")
            elif details.get("metricDescriptors"):
                s["stamina_unavailable"] = True
        if msg:
            changed += 1
            print(f"{s.get('date')} {aid}: " + " | ".join(msg))

    for d, a, why in skipped:
        print(f"skip {d} {a}: {why}")
    if args.dry_run:
        print(f"[dry-run] {changed} session(s) would change")
        return
    if changed:
        MASTER.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ backfill_session_metrics: {changed} session(s) updated")


if __name__ == "__main__":
    main()
