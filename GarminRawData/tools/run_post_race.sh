#!/usr/bin/env bash
# run_post_race.sh — Post-Race VDOT Update + Sync
set -eo pipefail
#
# Usage:
#   bash run_post_race.sh hm 1:57:34 --temp 27              # hot race dry-run (heat-adj estimate only)
#   bash run_post_race.sh hm 1:57:34 --temp 27 --apply      # hot race apply  (saves vdot_heat_adj_estimate, blocks VDOT update)
#   bash run_post_race.sh hm 1:47:00 --tt-confirmed          # TT dry-run      (full VDOT calculation)
#   bash run_post_race.sh hm 1:47:00 --tt-confirmed --apply  # TT apply        → update config.py + sync
#
# --temp       REQUIRED for races (°C) — triggers Ely heat correction if > 20°C
# --humidity   optional (%, default 60) — for informational output
# --tt-confirmed  TT result on TM ≤20°C — bypasses heat guard, allows full apply
# --apply      write changes to config.py (dry-run by default)

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TOOLS_DIR"

RACE_TYPE="${1:-}"
RACE_TIME="${2:-}"

if [ -z "$RACE_TYPE" ] || [ -z "$RACE_TIME" ]; then
  echo ""
  echo "Usage: bash run_post_race.sh <race_type> <time> [options]"
  echo "  race_type : hm | m | 5k | 10k"
  echo "  time      : HH:MM:SS or MM:SS"
  echo ""
  echo "Options:"
  echo "  --temp N         Race temperature in °C (REQUIRED for races)"
  echo "  --humidity N     Humidity % (default 60)"
  echo "  --tt-confirmed   Mark as TT result (bypasses heat guard)"
  echo "  --apply          Apply changes to config.py (default: dry-run)"
  echo ""
  echo "Examples:"
  echo "  bash run_post_race.sh hm 1:57:34 --temp 27             # hot race dry-run"
  echo "  bash run_post_race.sh hm 1:57:34 --temp 27 --apply     # hot race apply"
  echo "  bash run_post_race.sh hm 1:47:00 --tt-confirmed --apply # TT apply"
  exit 1
fi

# Parse remaining named args
shift 2
APPLY=false
TEMP=""
HUMIDITY="60"
TT_CONFIRMED=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --temp)         TEMP="$2";     shift 2 ;;
    --humidity)     HUMIDITY="$2"; shift 2 ;;
    --tt-confirmed) TT_CONFIRMED=true; shift ;;
    --apply)        APPLY=true;    shift ;;
    apply)          APPLY=true;    shift ;;   # backward-compat positional
    *) echo "⚠️  Unknown argument: $1"; shift ;;
  esac
done

VENV_PYTHON="$(bash "$TOOLS_DIR/get_python.sh")"

echo ""
echo "════════════════════════════════════════"
echo "🏁 POST-RACE UPDATE — $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════"

# ── Step 1: Compute VDOT + new paces ────────────────────────────────────────
echo ""
echo "▶ Step 1/4 — VDOT Computation"
echo "────────────────────────────────────"

# Build python args
PYTHON_ARGS=("$RACE_TYPE" "$RACE_TIME")
[ -n "$TEMP" ]         && PYTHON_ARGS+=("--temp" "$TEMP")
PYTHON_ARGS+=("--humidity" "$HUMIDITY")
$TT_CONFIRMED          && PYTHON_ARGS+=("--tt-confirmed")
$APPLY                 && PYTHON_ARGS+=("--apply")

"$VENV_PYTHON" post_race_updater.py "${PYTHON_ARGS[@]}"
UPDATER_EXIT=$?

if [ $UPDATER_EXIT -ne 0 ]; then
  echo ""
  echo "❌ post_race_updater.py exited with error $UPDATER_EXIT — aborting"
  exit $UPDATER_EXIT
fi

if ! $APPLY; then
  echo ""
  echo "💡 Dry-run mode — config.py ไม่ถูกแก้ไข"
  echo "   เพิ่ม --apply ถ้าต้องการอัปเดตจริง"
  echo ""
  echo "════════════════════════════════════════"
  echo "✅ Dry-run เสร็จ — config.py ยังไม่ถูกแก้ไข"
  echo "════════════════════════════════════════"
  exit 0
fi

# ── Step 2: Log race result as a session ────────────────────────────────────
echo ""
echo "▶ Step 2/4 — Race Session Analysis"
echo "────────────────────────────────────"
echo "📌 Fetching latest activity (race session)..."
"$VENV_PYTHON" post_session_analyzer.py --latest --update-log

# ── Step 3: VDOT trend (re-run with updated baseline) ───────────────────────
echo ""
echo "▶ Step 3/4 — VDOT Trend (updated baseline)"
echo "────────────────────────────────────"
"$VENV_PYTHON" vdot_estimator.py

# ── Step 4: Sync skill file ─────────────────────────────────────────────────
echo ""
echo "▶ Step 4/4 — Skill Sync"
echo "────────────────────────────────────"
"$VENV_PYTHON" skill_sync.py

echo ""
echo "════════════════════════════════════════"
echo "✅ Post-Race Update Complete"
echo "   config.py → updated"
echo "   sessions.json → race logged"
echo "   skill file → synced"
echo "════════════════════════════════════════"
echo ""
echo "📌 Next steps:"
echo "   1. Restart Claude Desktop เพื่อ reload config"
echo "   2. วิ่ง daily_brief เช้าวันถัดไป → จะใช้ VDOT ใหม่"
echo "   3. ดู training plan สัปดาห์ถัดไปด้วย VDOT ใหม่"
