#!/usr/bin/env bash
# get_python.sh — คืน path Python ที่ถูกต้องสำหรับ environment ปัจจุบัน
# Mac   → .venv/bin/python3.13  (เสมอ — tools ต้องการ Python 3.10+)
# Linux → .venv-linux/bin/python3 (Cowork sandbox)
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ "$(uname)" == "Linux" ]] && [ -f "$ROOT/.venv-linux/bin/python3" ]; then
    echo "$ROOT/.venv-linux/bin/python3"
elif [ -f "$ROOT/.venv/bin/python3.13" ]; then
    echo "$ROOT/.venv/bin/python3.13"
else
    echo "python3"
fi
