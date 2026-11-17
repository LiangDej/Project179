#!/usr/bin/env python3
"""fix_session_bb.py — set bb_start/bb_end in sessions_master.json to the
Body Battery of the RUN WINDOW only (not the day's peak / the sync-time value).

Why: session_logger.build_session stores bb_start = daily bb_high (morning peak)
and bb_end = current BB at sync time, so an afternoon 25km run showed
"89 -> 17 (drop 72)" when Garmin's own figure for the run is -27.

Single logic for every session (past and new):
  drop     = |summaryDTO.differenceBodyBattery| of the activity (authoritative);
             fallback = 3-min stress-timeline BB at run start minus at run end
  bb_start = 3-min timeline BB at the run's start (fallback: stored value)
  bb_end   = bb_start - drop

Idempotent: sessions already stamped bb_source == "activity_diff" are skipped.

Usage (from GarminRawData/tools, venv python):
  ../../.venv/bin/python3.13 ../fix_session_bb.py            # all unfixed sessions
  ../../.venv/bin/python3.13 ../fix_session_bb.py --id 123   # one activity
  ../../.venv/bin/python3.13 ../fix_session_bb.py --dry-run
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tools"))

MASTER = HERE / "QualitySessionLog" / "sessions_master.json"
BB_MATCH_MAX_GAP_MS = 6 * 60 * 1000  # timeline is every 3 min


def _timeline(client, day, cache):
    if day not in cache:
        try:
            sd = client.get_stress_data(day)
            cache[day] = [(v[0], v[2]) for v in (sd.get("bodyBatteryValuesArray") or [])
                          if len(v) > 2 and v[2] is not None]
        except Exception:
            cache[day] = []
    return cache[day]


def _near(tl, ms):
    best = min(tl, key=lambda p: abs(p[0] - ms), default=None)
    return best[1] if best and abs(best[0] - ms) <= BB_MATCH_MAX_GAP_MS else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, help="only this activity_id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import post_session_analyzer as psa
    client = psa.get_garmin_client()

    raw = json.loads(MASTER.read_text(encoding="utf-8"))
    sessions = raw.get("sessions", raw) if isinstance(raw, dict) else raw
    cache, changed, skipped = {}, 0, []

    for s in sessions:
        aid = s.get("activity_id")
        if not str(aid).isdigit():
            continue
        if args.id and int(aid) != args.id:
            continue
        if s.get("bb_source") == "activity_diff" and not args.id:
            continue
        try:
            summ = client.get_activity(int(aid)).get("summaryDTO", {})
        except Exception as e:
            skipped.append((s.get("date"), aid, f"fetch failed: {e}"))
            continue

        diff = summ.get("differenceBodyBattery")
        start = (summ.get("startTimeLocal") or "")[:19].replace("T", " ")
        tl = _timeline(client, start[:10], cache) if start else []
        t_start = t_end = None
        if start and tl:
            sms = dt.datetime.strptime(start, "%Y-%m-%d %H:%M:%S").timestamp() * 1000
            t_start = _near(tl, sms)
            t_end = _near(tl, sms + (summ.get("duration") or 0) * 1000)

        drop = abs(diff) if diff not in (None, 0) else (
            t_start - t_end if t_start is not None and t_end is not None and t_start != t_end else None)
        bb_start = t_start if t_start is not None else s.get("bb_start")
        if drop is None or bb_start is None:
            skipped.append((s.get("date"), aid, "no reliable BB for run window"))
            continue

        new_end = max(bb_start - drop, 0)
        if (s.get("bb_start"), s.get("bb_end")) != (bb_start, new_end):
            print(f"{s.get('date')} {str(s.get('total_km')).rjust(5)}km  "
                  f"{s.get('bb_start')}->{s.get('bb_end')}  =>  {bb_start}->{new_end} (drop {drop})")
            changed += 1
        s["bb_start"], s["bb_end"] = bb_start, new_end
        s["bb_source"] = "activity_diff" if diff not in (None, 0) else "timeline"

    for d, a, why in skipped:
        print(f"skip {d} {a}: {why}")
    if args.dry_run:
        print(f"[dry-run] {changed} session(s) would change")
        return
    if changed or any(x.get("bb_source") for x in sessions):
        MASTER.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ fix_session_bb: {changed} session(s) updated")


if __name__ == "__main__":
    main()
