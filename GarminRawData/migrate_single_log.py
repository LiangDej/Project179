#!/usr/bin/env python3
"""
migrate_single_log.py — ONE-OFF (v1.7.1): fold QualitySessionLog/sessions.json
into sessions_master.json so there is a single session log.

Each sessions.json row becomes the `analysis` block of the matching master
record (by activity_id); rows with no master record become `_stub` records.
Afterwards tools read the analysed sessions via session_logger.load_quality_log()
and tm_patch.py is no longer needed.

Safe to re-run: records that already have an `analysis` block are skipped.

Usage (from GarminRawData/):
    ../.venv/bin/python3.13 migrate_single_log.py --dry-run
    ../.venv/bin/python3.13 migrate_single_log.py
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE.parent / "skills" / "garmin_coach_mcp"))

import session_logger as sl  # noqa: E402

LEGACY = BASE / "QualitySessionLog" / "sessions.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not LEGACY.exists():
        print("sessions.json not found — nothing to migrate")
        return
    rows = json.loads(LEGACY.read_text()).get("sessions", [])
    master = sl._load_master()
    idx = {r.get("activity_id"): r for r in master.get("sessions", [])}

    merged = stubbed = skipped = 0
    for row in rows:
        aid = row.get("activity_id")
        rec = idx.get(aid)
        analysis = {k: row[k] for k in sl.ANALYSIS_KEYS if k in row}
        if rec is not None:
            if rec.get("analysis"):
                skipped += 1
                continue
            rec["analysis"] = analysis
            if row.get("treadmill_phases") and not rec.get("treadmill_phases"):
                rec["treadmill_phases"] = row["treadmill_phases"]
            merged += 1
        else:
            stub = {
                "date": row.get("date"), "activity_id": aid,
                "session_type": sl.MASTER_TYPE.get(row.get("session_type"), "easy"),
                "is_treadmill": row.get("is_treadmill", False),
                "avg_hr": row.get("avg_hr"), "max_hr": row.get("max_hr"),
                "laps": None, "treadmill_phases": row.get("treadmill_phases"),
                "bb_start": row.get("bb_start"), "bb_end": row.get("bb_end"),
                "stam_start": row.get("stam_start"), "stam_end": row.get("stam_end"),
                "_stub": True, "analysis": analysis,
            }
            master.setdefault("sessions", []).append(stub)
            idx[aid] = stub
            stubbed += 1
            print(f"  + stub {row.get('date')} [{aid}] {row.get('session_type')}")

    master["sessions"].sort(key=lambda x: x.get("activity_id", 0), reverse=True)
    print(f"merged {merged} | stub {stubbed} | already done {skipped} | of {len(rows)} rows")
    if args.dry_run:
        print("(dry-run — nothing written)")
        return
    shutil.copy2(LEGACY, LEGACY.with_suffix(".pre-v1.7.1.bak"))
    shutil.copy2(sl.MASTER_PATH, sl.MASTER_PATH.with_suffix(".pre-v1.7.1.bak"))
    sl._save_master(master)
    LEGACY.rename(LEGACY.with_suffix(".json.migrated"))
    print("✅ written; sessions.json → sessions.json.migrated (backups *.pre-v1.7.1.bak)")


if __name__ == "__main__":
    main()
