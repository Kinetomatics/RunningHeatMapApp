#!/bin/bash
set -e

cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/"
  read -r -p "Press Enter to close..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  python3 -m venv .venv
fi

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" -m streamlit run app.py
