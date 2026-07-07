#!/usr/bin/env bash
# run_post_quality.sh — เรียกหลังวิ่ง Quality (T / I / Tempo / Fast Finish)
# วิเคราะห์ + log sessions.json + sessions_master.json + VDOT + skill sync
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TOOLS_DIR"

echo ""
echo "════════════════════════════════════════"
echo "🔥 POST QUALITY RUN — $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════"

# ── Step 0: Sync latest activities → running_activities_all.json ───────────
echo ""
echo "▶ Step 0/3 — Sync Activities + Wellness"
"$(bash "$TOOLS_DIR/get_python.sh")" "$TOOLS_DIR/../fetch_incremental.py" --lookback-days 3
"$(bash "$TOOLS_DIR/get_python.sh")" "$TOOLS_DIR/../fetch_wellness.py"

# ── Step 1: Post-session analysis + log sessions.json ──────────────────────
echo ""
echo "▶ Step 1/3 — Post Session Analyzer"
echo "────────────────────────────────────"

if [ -n "${1:-}" ]; then
  echo "📌 Activity ID: $1"
  "$(bash "$TOOLS_DIR/get_python.sh")" post_session_analyzer.py --id "${1:-}" --update-log
else
  echo "📌 Latest activity"
  "$(bash "$TOOLS_DIR/get_python.sh")" post_session_analyzer.py --latest --update-log
fi

# ── Step 1.5: Patch TM pace in sessions.json ───────────────────────────────
echo ""
echo "▶ Step 1.5/3 — TM Pace Correction"
echo "────────────────────────────────────"
"$(bash "$TOOLS_DIR/get_python.sh")" tm_patch.py

# ── Step 2: VDOT trend ──────────────────────────────────────────────────────
echo ""
echo "▶ Step 2/3 — VDOT Estimator"
echo "────────────────────────────────────"
"$(bash "$TOOLS_DIR/get_python.sh")" vdot_estimator.py

# ── Step 3: Stamina patch ───────────────────────────────────────────────────
echo ""
echo "▶ Step 3/4 — Stamina Patch"
echo "────────────────────────────────────"
if [ -n "${1:-}" ]; then
  "$(bash "$TOOLS_DIR/get_python.sh")" stamina_patcher.py --id "${1:-}"
else
  "$(bash "$TOOLS_DIR/get_python.sh")" stamina_patcher.py
fi

# ── Step 4: Sync skill file ─────────────────────────────────────────────────
echo ""
echo "▶ Step 4/4 — Skill Sync"
echo "────────────────────────────────────"
"$(bash "$TOOLS_DIR/get_python.sh")" skill_sync.py

echo ""
echo "════════════════════════════════════════"
echo "✅ Done — sessions.json + sessions_master.json + stamina + skill file updated"
echo "════════════════════════════════════════"
