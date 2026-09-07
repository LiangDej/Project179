#!/usr/bin/env bash
# get_python.sh — คืน path Python ที่ถูกต้องสำหรับ environment ปัจจุบัน
# Mac   → .venv/bin/python (เสมอ — tools ต้องการ Python 3.10+; NOT system python3,
#          ซึ่งไม่มี Garmin library → PMC/TSB เพี้ยน)
# Linux → .venv-linux/bin/python3 (Cowork sandbox)
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ "$(uname)" == "Linux" ]] && [ -x "$ROOT/.venv-linux/bin/python3" ]; then
    echo "$ROOT/.venv-linux/bin/python3"
elif [ -x "$ROOT/.venv/bin/python" ]; then
    # Accept any venv Python (3.13, 3.12, 3.11, …) instead of hardcoding one
    # exact minor version — a venv built with a different Python still has
    # the Garmin library installed and must be preferred over system python3.
    echo "$ROOT/.venv/bin/python"
elif [ -x "$ROOT/.venv/bin/python3" ]; then
    echo "$ROOT/.venv/bin/python3"
else
    for candidate in "$ROOT"/.venv/bin/python3.*; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            exit 0
        fi
    done
    echo "⚠️  No .venv Python found — falling back to system python3 (missing Garmin library → PMC/TSB will be wrong)" >&2
    echo "python3"
fi
