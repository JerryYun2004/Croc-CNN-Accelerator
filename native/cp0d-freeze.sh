#!/bin/bash
set -euo pipefail
cp0d_native="$(cd "$(dirname "$0")" && pwd)"
cp0d_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0d_python" || { echo 'Existing Croc Python environment is missing.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp0d_python" "$cp0d_native/cp0d_freeze/freeze.py" --repo "$(dirname "$cp0d_native")" "$@"
