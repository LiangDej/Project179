#!/usr/bin/env python3
"""
race_registry.py — SINGLE SOURCE OF TRUTH loader for race targets.

All race dates / distances / goals / aid-station layouts / course profiles live in
ONE data file: GarminRawData/races.json. Every tool imports from here instead of
hardcoding its own RACE dict — so changing a race (or archiving one like Fuji) is a
one-line edit in races.json and propagates everywhere. This kills the class of bug
where one tool says "Fuji Dec 13" and another says "ATM Nov 29".

Public API
----------
    load_races()                 -> full dict {key: race_dict}
    get_race(key)                -> one race dict (raises KeyError if unknown)
    active_race_key()            -> key of the A-race (races.json "active_race")
    active_race()                -> the A-race dict
    race_date(key)               -> datetime.date
    list_races(active_only=True) -> [(key, race_dict), ...] sorted by date
    race_choices(active_only)    -> [key, ...] for argparse choices

CLI
---
    python3 race_registry.py                 # list active races
    python3 race_registry.py --all           # include archived
    python3 race_registry.py --show atm      # one race detail
    python3 race_registry.py --set-active atm
"""
import json
import argparse
from pathlib import Path
from datetime import date

RACES_FILE = Path(__file__).resolve().parent.parent / "races.json"


def load_races() -> dict:
    """Return {key: race_dict}. Empty dict if file missing/corrupt."""
    if not RACES_FILE.exists():
        return {}
    try:
        data = json.loads(RACES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get("races", {})


def _raw() -> dict:
    try:
        return json.loads(RACES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_race(key: str) -> dict:
    races = load_races()
    if key not in races:
        raise KeyError(f"Unknown race '{key}'. Known: {sorted(races)}")
    return races[key]


def active_race_key() -> str:
    raw = _raw()
    key = raw.get("active_race")
    if key and key in raw.get("races", {}):
        return key
    # Fallback: earliest-dated active race
    actives = list_races(active_only=True)
    return actives[0][0] if actives else "atm"


def active_race() -> dict:
    return get_race(active_race_key())


def race_date(key: str) -> date:
    return date.fromisoformat(get_race(key)["date"])


def list_races(active_only: bool = True) -> list:
    races = load_races()
    items = [(k, r) for k, r in races.items()
             if (r.get("active", True) or not active_only)]
    return sorted(items, key=lambda kr: kr[1].get("date", "9999-12-31"))


def race_choices(active_only: bool = True) -> list:
    return [k for k, _ in list_races(active_only=active_only)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_race(key: str, r: dict):
    star = " ⭐ ACTIVE" if key == active_race_key() else ""
    arc  = "" if r.get("active", True) else "  📦 archived"
    print(f"  {key:<8} {r.get('short',''):<6} {r.get('date',''):<12} "
          f"{r.get('dist_km',0):>6}km  tier:{r.get('tier','?'):<8} "
          f"{r.get('goal_label',''):<10}{star}{arc}")


def main():
    ap = argparse.ArgumentParser(description="Race registry — single source of truth")
    ap.add_argument("--all", action="store_true", help="include archived races")
    ap.add_argument("--show", metavar="KEY", help="show one race in full")
    ap.add_argument("--set-active", metavar="KEY", help="set the A-race in races.json")
    args = ap.parse_args()

    if args.set_active:
        raw = _raw()
        if args.set_active not in raw.get("races", {}):
            print(f"❌ Unknown race '{args.set_active}'. Known: {sorted(raw.get('races', {}))}")
            return
        raw["active_race"] = args.set_active
        RACES_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ active_race = {args.set_active}")
        return

    if args.show:
        try:
            r = get_race(args.show)
        except KeyError as e:
            print(f"❌ {e}")
            return
        print(json.dumps({args.show: r}, ensure_ascii=False, indent=2))
        return

    print("=" * 70)
    print(f"🏁 RACE REGISTRY  (active = {active_race_key()})")
    print("=" * 70)
    for key, r in list_races(active_only=not args.all):
        _print_race(key, r)
    print("=" * 70)


if __name__ == "__main__":
    main()
