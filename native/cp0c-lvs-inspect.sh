#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo "Usage: bash native/cp0c-lvs-inspect.sh COMPLETED_LVS_DIRECTORY" >&2
  exit 2
fi
cp0i_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0i_python" || { echo "Existing CP0C KLayout Python environment is missing." >&2; exit 1; }
exec "$cp0i_python" "$(dirname "$0")/cp0c-lvs-inspect.py" "$1"
