#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
pip install 'pyinstaller>=6.0'

rm -rf build/QR\ AirDrop build/QRAirDrop-mac dist/QR\ AirDrop.app dist/QR\ AirDrop
pyinstaller --noconfirm QRAirDrop-mac.spec

echo
echo "Built: $(pwd)/dist/QR AirDrop.app"
echo "Open with: open \"dist/QR AirDrop.app\""
echo "Optional: drag it into /Applications"
