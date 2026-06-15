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

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TOOLS_DIR"

ACT_ID="${1:-}"

echo ""
echo "════════════════════════════════════════"
echo "🔍 AUTO-DETECT — $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════"

# ── Detect session type ────────────────────────────────────────────────────
if [ -n "$ACT_ID" ]; then
  SESSION_TYPE=$("$(bash "$TOOLS_DIR/get_python.sh")" detect_session.py "$ACT_ID" --verbose 2>&1 >/dev/null; "$(bash "$TOOLS_DIR/get_python.sh")" detect_session.py "$ACT_ID")
else
  SESSION_TYPE=$("$(bash "$TOOLS_DIR/get_python.sh")" detect_session.py --verbose 2>&1 >/dev/null; "$(bash "$TOOLS_DIR/get_python.sh")" detect_session.py)
fi

# แสดง verbose output แยก (stderr → terminal, stdout → SESSION_TYPE)
if [ -n "$ACT_ID" ]; then
  "$(bash "$TOOLS_DIR/get_python.sh")" detect_session.py "$ACT_ID" --verbose 2>&1 1>/dev/null || true
else
  "$(bash "$TOOLS_DIR/get_python.sh")" detect_session.py --verbose 2>&1 1>/dev/null || true
fi

SESSION_TYPE=$("$(bash "$TOOLS_DIR/get_python.sh")" detect_session.py ${ACT_ID:+"$ACT_ID"} 2>/dev/null)

echo ""
echo "📌 Detected: ${SESSION_TYPE^^}"
echo ""

# ── Route to correct pipeline ──────────────────────────────────────────────
case "$SESSION_TYPE" in
  quality)
    echo "🔥 → Quality Pipeline"
    echo "────────────────────────────────────"
    if [ -n "$ACT_ID" ]; then
      bash run_post_quality.sh "$ACT_ID"
    else
      bash run_post_quality.sh
    fi
    ;;
  long)
    echo "🟡 → Long Run Pipeline"
    echo "────────────────────────────────────"
    if [ -n "$ACT_ID" ]; then
      bash run_post_long.sh "$ACT_ID"
    else
      bash run_post_long.sh
    fi
    ;;
  easy|*)
    echo "🟢 → Easy Run Pipeline"
    echo "────────────────────────────────────"
    if [ -n "$ACT_ID" ]; then
      bash run_post_easy.sh "$ACT_ID"
    else
      bash run_post_easy.sh
    fi
    ;;
esac
