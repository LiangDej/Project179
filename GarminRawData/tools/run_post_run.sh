#!/usr/bin/env bash
# run_post_run.sh — Universal post-run script (ใช้อันเดียวหลังวิ่งทุกครั้ง)
#
# ตรวจจับ session type อัตโนมัติ แล้ว route ไปยัง pipeline ที่ถูกต้อง:
#   quality  → run_post_quality.sh  (T/I/R session)
#   long     → run_post_long.sh     (Easy/M ≥14km)
#   easy     → run_post_easy.sh     (Easy <14km)
#
# Usage:
#   bash run_post_run.sh              # auto-detect latest activity
#   bash run_post_run.sh [activityId] # ระบุ activity ID เฉพาะ
#
# v1.8.1: bash 3.2 compatible (macOS /bin/bash — no bash-4 case modifiers) and ONE
# detect_session call (was 4 → 4 live Garmin API calls).
set -uo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TOOLS_DIR"

ACT_ID="${1:-}"
PY="$(bash "$TOOLS_DIR/get_python.sh")"

echo ""
echo "════════════════════════════════════════"
echo "🔍 AUTO-DETECT — $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════"

# ── Detect session type: stdout = type, stderr = verbose reasoning ─────────
ERR_FILE="$(mktemp -t detect_session.XXXXXX)"
SESSION_TYPE="$("$PY" detect_session.py ${ACT_ID:+"$ACT_ID"} --verbose 2>"$ERR_FILE" || true)"
cat "$ERR_FILE" >&2
rm -f "$ERR_FILE"

echo ""
echo "📌 Detected: $(printf '%s' "$SESSION_TYPE" | tr '[:lower:]' '[:upper:]')"
echo ""

# ── Route to correct pipeline ──────────────────────────────────────────────
case "$SESSION_TYPE" in
  quality)
    echo "🔥 → Quality Pipeline"
    echo "────────────────────────────────────"
    bash run_post_quality.sh ${ACT_ID:+"$ACT_ID"}
    ;;
  long)
    echo "🟡 → Long Run Pipeline"
    echo "────────────────────────────────────"
    bash run_post_long.sh ${ACT_ID:+"$ACT_ID"}
    ;;
  easy|*)
    echo "🟢 → Easy Run Pipeline"
    echo "────────────────────────────────────"
    bash run_post_easy.sh ${ACT_ID:+"$ACT_ID"}
    ;;
esac
