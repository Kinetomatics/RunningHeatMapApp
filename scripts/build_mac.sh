#!/bin/bash
set -e

cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  python3 -m venv .venv
fi

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" scripts/generate_icons.py
export PYINSTALLER_CONFIG_DIR="${PWD}/build/pyinstaller-cache"
"$PYTHON" -m PyInstaller --clean --noconfirm RunningHeatmap.spec
ditto -c -k --sequesterRsrc --keepParent dist/RunningHeatmap.app dist/RunningHeatmap-mac.zip
"$PYTHON" scripts/check_release.py dist/RunningHeatmap-mac.zip

echo
echo "Build complete:"
echo "  dist/RunningHeatmap.app"
echo "  dist/RunningHeatmap-mac.zip"
