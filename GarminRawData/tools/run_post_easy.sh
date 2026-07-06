#!/usr/bin/env bash
# run_post_easy.sh — เรียกหลังวิ่ง Easy ทุกครั้ง
# วิเคราะห์ session + auto-log ลง sessions_master.json
# หมายเหตุ: skill_sync ไม่รันที่นี่ — easy runs ไม่เข้า sessions.json
#           skill_sync รันหลัง Quality/Long Run เท่านั้น (มีข้อมูลใหม่จริง)
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TOOLS_DIR"

echo ""
echo "════════════════════════════════════════"
echo "🏃 POST EASY RUN — $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════"

# ── Step 0: Sync latest activities → running_activities_all.json ───────────
echo ""
echo "▶ Step 0 — Sync Activities + Wellness"
"$(bash "$TOOLS_DIR/get_python.sh")" "$TOOLS_DIR/../fetch_incremental.py" --lookback-days 3
"$(bash "$TOOLS_DIR/get_python.sh")" "$TOOLS_DIR/../fetch_wellness.py"

# ── Post-session analysis → sessions_master.json ───────────────────────────
echo ""
if [ -n "${1:-}" ]; then
  echo "📌 Activity ID: $1"
  "$(bash "$TOOLS_DIR/get_python.sh")" post_session_analyzer.py --id "${1:-}"
else
  echo "📌 Latest activity"
  "$(bash "$TOOLS_DIR/get_python.sh")" post_session_analyzer.py --latest
fi

# ── Auto-patch stamina_drain_pct → sessions_master.json ────────────────────
echo ""
echo "▶ Stamina patch"
if [ -n "${1:-}" ]; then
  "$(bash "$TOOLS_DIR/get_python.sh")" stamina_patcher.py --id "${1:-}"
else
  "$(bash "$TOOLS_DIR/get_python.sh")" stamina_patcher.py
fi

# ── Recalculate Energy Efficiency Score ────────────────────────────────────
echo ""
echo "▶ Energy Efficiency Score"
"$(bash "$TOOLS_DIR/get_python.sh")" energy_efficiency_scorer.py

echo ""
echo "════════════════════════════════════════"
echo "✅ Done — sessions_master.json updated"
echo "════════════════════════════════════════"
