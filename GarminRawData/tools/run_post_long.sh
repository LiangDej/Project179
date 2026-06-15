#!/usr/bin/env bash
# run_post_long.sh — เรียกหลังวิ่ง Long Run (≥14km)
# ต่างจาก run_post_easy.sh ตรงที่ใช้ --update-log เพื่อ log ลง sessions.json
# ทำให้ energy_efficiency_scorer.py เห็น stamina_drain_pct ของ Long Run

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TOOLS_DIR"

echo ""
echo "════════════════════════════════════════"
echo "🟡 POST LONG RUN — $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════"

# ── Step 1: Analyze + log to sessions.json + sessions_master.json ──────────
echo ""
echo "▶ Step 1/4 — Post Session Analyzer (--update-log)"
echo "────────────────────────────────────"
if [ -n "${1:-}" ]; then
  echo "📌 Activity ID: $1"
  "$(bash "$TOOLS_DIR/get_python.sh")" post_session_analyzer.py --id "${1:-}" --update-log
else
  echo "📌 Latest activity"
  "$(bash "$TOOLS_DIR/get_python.sh")" post_session_analyzer.py --latest --update-log
fi

# ── Step 1.5: TM Pace Correction (Long Runs on treadmill need this too) ────
echo ""
echo "▶ Step 1.5/4 — TM Pace Correction"
echo "────────────────────────────────────"
"$(bash "$TOOLS_DIR/get_python.sh")" tm_patch.py

# ── Step 2: Energy Efficiency Score ───────────────────────────────────────
echo ""
echo "▶ Step 2/4 — Energy Efficiency Scorer"
echo "────────────────────────────────────"
"$(bash "$TOOLS_DIR/get_python.sh")" energy_efficiency_scorer.py

# ── Step 3: Sync skill file ────────────────────────────────────────────────
echo ""
echo "▶ Step 3/4 — Skill Sync"
echo "────────────────────────────────────"
"$(bash "$TOOLS_DIR/get_python.sh")" skill_sync.py

echo ""
echo "════════════════════════════════════════"
echo "✅ Done — sessions.json + sessions_master.json + skill file updated"
echo "════════════════════════════════════════"
