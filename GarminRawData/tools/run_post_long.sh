#!/usr/bin/env bash
# run_post_long.sh — เรียกหลังวิ่ง Long Run (≥14km)
# ต่างจาก run_post_easy.sh ตรงที่ใช้ --update-log เพื่อบันทึกผลวิเคราะห์ (grade) ลง sessions_master.json
# ทำให้ energy_efficiency_scorer.py เห็น stamina_drain_pct ของ Long Run

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TOOLS_DIR"

echo ""
echo "════════════════════════════════════════"
echo "🟡 POST LONG RUN — $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════"

# ── Step 1: Analyze + log to sessions_master.json ──────────────────────────
echo ""
echo "▶ Step 1/3 — Post Session Analyzer (--update-log)"
echo "────────────────────────────────────"
if [ -n "${1:-}" ]; then
  echo "📌 Activity ID: $1"
  "$(bash "$TOOLS_DIR/get_python.sh")" post_session_analyzer.py --id "${1:-}" --update-log
else
  echo "📌 Latest activity"
  "$(bash "$TOOLS_DIR/get_python.sh")" post_session_analyzer.py --latest --update-log
fi

# (v1.7.1) tm_patch removed — treadmill pace corrected on read
# (session_logger.load_quality_log).

# ── Step 2: Energy Efficiency Score ───────────────────────────────────────
echo ""
echo "▶ Step 2/3 — Energy Efficiency Scorer"
echo "────────────────────────────────────"
"$(bash "$TOOLS_DIR/get_python.sh")" energy_efficiency_scorer.py

# ── Step 3: Sync skill file ────────────────────────────────────────────────
echo ""
echo "▶ Step 3/3 — Skill Sync"
echo "────────────────────────────────────"
"$(bash "$TOOLS_DIR/get_python.sh")" skill_sync.py

echo ""
echo "════════════════════════════════════════"
echo "✅ Done — sessions_master.json + skill file updated"
echo "════════════════════════════════════════"
