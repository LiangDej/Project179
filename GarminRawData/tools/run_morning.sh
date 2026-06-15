#!/usr/bin/env bash
# run_morning.sh — เรียกก่อนวิ่งทุกเช้า
# ดู BB / HRV / TSB / Injury Risk / แผนวันนี้
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TOOLS_DIR"

"$(bash "$TOOLS_DIR/get_python.sh")" daily_brief.py
