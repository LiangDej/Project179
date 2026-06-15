#!/usr/bin/env bash
# setup_cowork_venv.sh — รันครั้งเดียวใน Cowork เพื่อสร้าง Linux-compatible venv
# หลังจากนี้ run_*.sh ทุกตัวจะใช้ .venv-linux อัตโนมัติ
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "🔧 Creating .venv-linux for Cowork (Linux) environment..."
python3 -m venv "$ROOT/.venv-linux"

echo "📦 Installing packages from requirements.txt..."
"$ROOT/.venv-linux/bin/pip" install --upgrade pip -q
"$ROOT/.venv-linux/bin/pip" install -r "$ROOT/requirements.txt" -q

echo ""
echo "✅ .venv-linux ready!"
echo "   run_*.sh ทุกตัวจะใช้ .venv-linux โดยอัตโนมัติตั้งแต่นี้"
echo "   ทดสอบ: cd GarminRawData/tools && bash run_morning.sh"
