"""
sleep_correlator.py — Correlate sleep score → next-day HR drift + performance
Reads wellness/ + running_activities_all.json

Usage:
    python3 sleep_correlator.py
    python3 sleep_correlator.py --days 60
    python3 sleep_correlator.py --insight
    python3 sleep_correlator.py --backfill          # fetch historical health data from Garmin
    python3 sleep_correlator.py --backfill --days 90
"""
import os, sys, json, argparse, math
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
CACHE_DIR = Path(__file__).resolve().parent.parent / "wellness"
ACTS_FILE = Path(__file__).parent.parent / "running_activities_all.json"


# ---------------------------------------------------------------------------
# Backfill historical health cache from Garmin API
# ---------------------------------------------------------------------------
def backfill_health_cache(days: int = 90):
    """Fetch past health data (sleep/BB/HRV) for all run dates and save to cache.

    Only fetches dates where cache file is missing — never overwrites existing data.
    """
    try:
        from garmin_client import get_client, get_health_cached
    except ImportError:
        print("❌ garmin_client ไม่พบ — รันจาก GarminRawData/tools/ ครับ")
        return

    acts = load_activities()
    if not acts:
        print("❌ ไม่พบ running_activities_all.json")
        return

    cutoff = date.today() - timedelta(days=days)
    run_dates: set[str] = set()
    for a in acts:
        d_str = a.get("startTimeLocal", "")[:10]
        if not d_str:
            continue
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        if d >= cutoff and a.get("distance", 0) > 3000:
            run_dates.add(d_str)

    # Need health data for the night BEFORE each run
    dates_to_fetch = sorted(
        str(date.fromisoformat(d) - timedelta(days=1))
        for d in run_dates
        if date.fromisoformat(d) - timedelta(days=1) >= cutoff
    )

    print(f"🔄 Backfill health cache — {len(dates_to_fetch)} dates to check ({days}d window)")
    client = get_client()
    fetched = skipped = failed = 0

    for d_str in dates_to_fetch:
        cache_path = CACHE_DIR / f"wellness_{d_str}.json"
        if cache_path.exists():
            print(f"  ⏭  {d_str} — already cached")
            skipped += 1
            continue

        h = get_health_cached(client, d_str, force_refresh=False)
        src = h.get("_source", "?")
        if src == "unavailable":
            print(f"  ❌ {d_str} — unavailable")
            failed += 1
        else:
            print(f"  ✅ {d_str} — {src}")
            fetched += 1

    print(f"\n✅ Done — fetched: {fetched} | skipped: {skipped} | failed: {failed}")
    if fetched > 0:
        print("   รัน sleep_correlator.py --insight อีกครั้งเพื่อดูผล")


# ---------------------------------------------------------------------------
# Load health cache
# ---------------------------------------------------------------------------
def load_health(days: int = 90) -> dict:
    today = date.today()
    cutoff = today - timedelta(days=days)
    data = {}
    for f in CACHE_DIR.glob("wellness_*.json"):
        try:
            d = date.fromisoformat(f.stem.replace("wellness_", ""))
            if d < cutoff:
                continue
            with open(f) as fh:
                data[str(d)] = json.load(fh)
        except Exception:
            pass
    return data


# ---------------------------------------------------------------------------
# Load activities
# ---------------------------------------------------------------------------
def load_activities() -> list:
    if not ACTS_FILE.exists():
        return []
    with open(ACTS_FILE) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Main correlation logic
# ---------------------------------------------------------------------------
def correlate(days: int = 90, show_insight: bool = False):
    health = load_health(days)
    acts = load_activities()

    # Build activity lookup by date
    act_by_date: dict[str, list] = defaultdict(list)
    for a in acts:
        d = a.get("startTimeLocal", "")[:10]
        if d:
            act_by_date[d].append(a)

    rows = []
    for d_str, h in sorted(health.items()):
        sleep = h.get("sleep_score")
        bb_high = h.get("bb_high")
        hrv = h.get("hrv_status", "")
        rhr = h.get("resting_hr")
        if sleep is None:
            continue

        # Next-day runs
        next_d = str(date.fromisoformat(d_str) + timedelta(days=1))
        next_acts = [a for a in act_by_date.get(next_d, [])
                     if a.get("averageHR", 0) > 100 and a.get("distance", 0) > 3000]
        if not next_acts:
            continue

        # Guard against None / missing fields
        hr_vals   = [a["averageHR"] for a in next_acts if a.get("averageHR")]
        dist_vals = [a["distance"]  for a in next_acts if a.get("distance")]
        pace_vals = [
            a["duration"] / 60 / (a["distance"] / 1000)
            for a in next_acts
            if a.get("duration") and a.get("distance")
        ]
        if not hr_vals or not dist_vals:
            continue

        avg_hr   = sum(hr_vals)   / len(hr_vals)
        avg_dist = sum(dist_vals) / len(dist_vals) / 1000
        avg_pace = sum(pace_vals) / len(pace_vals) if pace_vals else None

        rows.append({
            "sleep_date":  d_str,
            "sleep_score": sleep,
            "bb_high":     bb_high,
            "hrv":         hrv,
            "rhr":         rhr,
            "run_date":    next_d,
            "run_hr":      round(avg_hr, 1),
            "run_dist":    round(avg_dist, 1),
            "run_pace":    round(avg_pace, 2) if avg_pace else None,
        })

    if not rows:
        print("ไม่มีข้อมูลเพียงพอ — ต้องมี wellness/ + activities ที่ match กัน")
        return

    # Bucket by sleep quality
    poor   = [r for r in rows if r["sleep_score"] < 60]
    ok     = [r for r in rows if 60 <= r["sleep_score"] < 80]
    good   = [r for r in rows if r["sleep_score"] >= 80]

    def avg(lst, key):
        vals = [r[key] for r in lst if r[key] is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    print("=" * 60)
    print(f"💤 SLEEP → NEXT-DAY PERFORMANCE CORRELATOR")
    print(f"   {len(rows)} data points | {days} day window")
    print("=" * 60)
    print()
    print(f"{'Sleep Quality':<18} {'n':>4} {'Avg Sleep':>10} {'Next-day HR':>12} {'Avg Pace':>10}")
    print("-" * 58)
    for label, bucket in [("🔴 Poor (<60)", poor), ("🟡 OK (60–79)", ok), ("🟢 Good (≥80)", good)]:
        if not bucket:
            continue
        n = len(bucket)
        s = avg(bucket, "sleep_score")
        hr = avg(bucket, "run_hr")
        pace = avg(bucket, "run_pace")
        pm, ps = divmod(int(pace * 60), 60) if pace else (0, 0)
        pace_str = f"{int(pace)}:{int((pace - int(pace))*60):02d}/km" if pace else "N/A"
        print(f"{label:<18} {n:>4} {str(s):>10} {str(hr):>12} {pace_str:>10}")

    print()

    # Correlation coefficient (sleep_score vs run_hr)
    if len(rows) >= 5:
        xs = [r["sleep_score"] for r in rows]
        ys = [r["run_hr"] for r in rows]
        xm, ym = sum(xs)/len(xs), sum(ys)/len(ys)
        num = sum((x-xm)*(y-ym) for x,y in zip(xs,ys))
        den = math.sqrt(sum((x-xm)**2 for x in xs) * sum((y-ym)**2 for y in ys))
        r = round(num/den, 3) if den > 0 else 0
        direction = "↓ HR ต่ำลงเมื่อนอนดีขึ้น ✅" if r < -0.2 else \
                    "↑ ความสัมพันธ์อ่อน" if abs(r) < 0.2 else "↑ HR สูงขึ้นเมื่อนอนดีขึ้น (?)"
        print(f"📊 Correlation (sleep ↔ next-day HR): r = {r}  {direction}")
        print()

    # HRV analysis
    balanced_hr = avg([r for r in rows if r["hrv"] == "BALANCED"], "run_hr")
    unbalanced_hr = avg([r for r in rows if r["hrv"] in ("LOW", "POOR", "UNBALANCED")], "run_hr")
    if balanced_hr and unbalanced_hr:
        diff = round(unbalanced_hr - balanced_hr, 1)
        print(f"💓 HRV BALANCED next-day HR  : {balanced_hr} bpm")
        print(f"   HRV UNBALANCED next-day HR: {unbalanced_hr} bpm  (diff: +{diff} bpm)")
        print()

    if show_insight:
        print("💡 Insight:")
        if poor and good:
            hr_diff = round(avg(good, "run_hr") - avg(poor, "run_hr") if avg(good, "run_hr") and avg(poor, "run_hr") else 0, 1)
            if hr_diff < 0:
                print(f"   นอนดี (≥80) → HR ถัดไปต่ำกว่านอนแย่ {abs(hr_diff)} bpm")
                print(f"   = ออกแรงน้อยกว่าที่ pace เดิม หรือวิ่งได้เร็วกว่าที่ HR เดิม")
            else:
                print(f"   ข้อมูลยังน้อยเกินไปสรุปได้ชัดเจน — เก็บข้อมูลต่อไป")

    print()
    print("📋 Recent Data Points:")
    print(f"   {'Sleep Date':<12} {'Sleep':>6} {'BB':>5} {'HRV':<12} {'Run HR':>7} {'Pace':>10}")
    print("   " + "-" * 56)
    for r in sorted(rows, key=lambda x: x["sleep_date"], reverse=True)[:15]:
        pace_str = "N/A"
        if r["run_pace"] is not None:
            pm = int(r["run_pace"]); ps = int((r["run_pace"] - pm) * 60)
            pace_str = f"{pm}:{ps:02d}/km"
        hrv_short = r["hrv"][:8] if r["hrv"] else "?"
        print(f"   {r['sleep_date']:<12} {r['sleep_score']:>6} {str(r['bb_high'] or '?'):>5} "
              f"{hrv_short:<12} {r['run_hr']:>7} {pace_str}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",     type=int, default=90)
    parser.add_argument("--insight",  action="store_true")
    parser.add_argument("--backfill", action="store_true",
                        help="Fetch historical health data from Garmin API into local cache")
    args = parser.parse_args()

    if args.backfill:
        backfill_health_cache(args.days)
    else:
        correlate(args.days, args.insight)


if __name__ == "__main__":
    main()
