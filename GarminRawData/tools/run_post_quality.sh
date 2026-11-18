#!/usr/bin/env bash
# run_post_quality.sh — เรียกหลังวิ่ง Quality (T / I / Tempo / Fast Finish)
# วิเคราะห์ + log sessions_master.json (single log, v1.7.1) + VDOT + skill sync
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

# ── Step 1: Post-session analysis + log sessions_master.json ───────────────
echo ""
echo "▶ Step 1/3 — Post Session Analyzer"
echo "────────────────────────────────────"

# post_session_analyzer.py prompts interactively to confirm sessions_master
# logging (Y/n), session-type override, and per-lap role annotation. Feed "Y"
# once + enough blank lines (Enter=keep default) so it never hangs waiting
# for input when this script runs unattended.
AUTO_ANSWERS="Y$(printf '\n%.0s' {1..50})"
if [ -n "${1:-}" ]; then
  echo "📌 Activity ID: $1"
  printf '%s' "$AUTO_ANSWERS" | "$(bash "$TOOLS_DIR/get_python.sh")" post_session_analyzer.py --id "${1:-}" --update-log
else
  echo "📌 Latest activity"
  printf '%s' "$AUTO_ANSWERS" | "$(bash "$TOOLS_DIR/get_python.sh")" post_session_analyzer.py --latest --update-log
fi

# (v1.7.1) tm_patch removed — single log; treadmill pace (belt > HR-inferred)
# is applied on read by session_logger.load_quality_log().

# ── Step 2: VDOT trend ──────────────────────────────────────────────────────
echo ""
echo "▶ Step 2/3 — Training estimate + breakthrough (race_predictor)"
echo "────────────────────────────────────"
"$(bash "$TOOLS_DIR/get_python.sh")" race_predictor.py

# (v1.7) Stamina + run-window Body Battery are now written by session_logger
# at log time — the stamina_patcher / fix_session_bb steps are gone.

# ── Step 4: Sync skill file ─────────────────────────────────────────────────
echo ""
echo "▶ Step 3/3 — Skill Sync"
echo "────────────────────────────────────"
"$(bash "$TOOLS_DIR/get_python.sh")" skill_sync.py

echo ""
echo "════════════════════════════════════════"
echo "✅ Done — sessions_master.json + skill file updated"
echo "════════════════════════════════════════"
