#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"
if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
  APP_PYTHON="$APP_DIR/.venv/bin/python"
else
  APP_PYTHON=python3
fi
if ! command -v "$APP_PYTHON" >/dev/null; then
  echo "Python 3 est introuvable. Consultez README.md." >&2
  exit 1
fi
if ! "$APP_PYTHON" -c 'import tkinter' 2>/dev/null; then
  echo "Tkinter manque. Sous Linux Mint : sudo apt install python3-tk" >&2
  exit 1
fi
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
exec "$APP_PYTHON" "$APP_DIR/app.py" "$@"
