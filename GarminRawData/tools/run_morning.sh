#!/usr/bin/env bash
# run_morning.sh — เรียกก่อนวิ่งทุกเช้า
# ดู BB / HRV / TSB / Injury Risk / แผนวันนี้
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TOOLS_DIR"

PY="$(bash "$TOOLS_DIR/get_python.sh")"

# Fetches are best-effort — a Garmin rate-limit or network hiccup shouldn't
# stop daily_brief.py from running: it has its own offline/cache fallback.
"$PY" "$TOOLS_DIR/../fetch_incremental.py" --lookback-days 3 || echo "⚠️  fetch_incremental failed — daily_brief will use cached data"
"$PY" "$TOOLS_DIR/../fetch_wellness.py" || echo "⚠️  fetch_wellness failed — daily_brief will use cached data"
"$PY" daily_brief.py
