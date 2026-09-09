#!/usr/bin/env bash
# check_before_push.sh — run before every `git push` on this repo.
#
# Verifies:
#   1. Test suite passes (see GarminRawData/tests/test_suite.py for current check count)
#   2. No sensitive file is staged/tracked that should be gitignored
#      (personal athlete data, credentials, VME lab secrets, wellness data)
#
# Exit 0 = safe to push. Exit 1 = fix something first.
set -uo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$TOOLS_DIR/../.." && pwd)"
cd "$ROOT_DIR"

FAIL=0

echo ""
echo "════════════════════════════════════════"
echo "🔒 PRE-PUSH CHECK — $(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════"

# ── 1. Test suite ───────────────────────────────────────────────────────────
echo ""
echo "▶ Step 1 — Test suite"
if "$(bash "$TOOLS_DIR/get_python.sh")" GarminRawData/tests/test_suite.py; then
  echo "  ✅ Test suite passed"
else
  echo "  ❌ Test suite FAILED — fix before pushing"
  FAIL=1
fi

# ── 2. Sensitive files must never be tracked ────────────────────────────────
# Add new personal/secret paths here as the project grows — this list is the
# single source for "what must stay out of git history."
SENSITIVE_PATHS=(
  "GarminRawData/athlete.json"
  "GarminRawData/races.json"
  "GarminRawData/wellness"
  "GarminRawData/lab_envs.py"
  "GarminRawData/deploy_vm.py"
  "GarminRawData/VME-Training-Lab-Runbook.md"
  "GarminRawData/MASTER_PLAN_2026.md"
  "GarminRawData/QualitySessionLog"
  "GarminRawData/SessionCache"
  ".env"
  "PalatinoseLab"
  "skills/investment-coach.md"
)

echo ""
echo "▶ Step 2 — Sensitive file check"
FOUND_TRACKED=0
for p in "${SENSITIVE_PATHS[@]}"; do
  if git ls-files --error-unmatch "$p" >/dev/null 2>&1; then
    echo "  ❌ TRACKED (should be gitignored): $p"
    FOUND_TRACKED=1
  fi
done
# Also catch anything currently staged (added/modified — NOT deleted; a
# staged deletion is how we fix a past leak, not a new one) that matches
# known secret patterns, even if not yet committed.
STAGED_HITS="$(git diff --cached --name-status | grep -v '^D' | cut -f2- | grep -E 'lab_envs\.py|deploy_vm\.py|athlete\.json|races\.json|\.env$|wellness/|VME-Training-Lab-Runbook\.md|PalatinoseLab/|investment-coach\.md' || true)"
if [ -n "$STAGED_HITS" ]; then
  echo "  ❌ STAGED (about to be committed) — sensitive files:"
  echo "$STAGED_HITS" | sed 's/^/       /'
  FOUND_TRACKED=1
fi

if [ "$FOUND_TRACKED" -eq 0 ]; then
  echo "  ✅ No sensitive files tracked or staged"
else
  echo "  → run: git rm --cached <file>   (keeps it locally, removes from git)"
  FAIL=1
fi

# ── 3. Verify ha_failover_report_*.md glob pattern too (dated filenames) ────
DATED_REPORTS="$(git ls-files 'GarminRawData/ha_failover_report_*.md' 2>/dev/null || true)"
if [ -n "$DATED_REPORTS" ]; then
  echo "  ❌ TRACKED (should be gitignored): $DATED_REPORTS"
  FAIL=1
fi

echo ""
echo "════════════════════════════════════════"
if [ "$FAIL" -eq 0 ]; then
  echo "✅ Safe to push"
else
  echo "❌ NOT safe to push — fix the items above first"
fi
echo "════════════════════════════════════════"

exit "$FAIL"
