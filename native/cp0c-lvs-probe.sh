#!/bin/bash
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo "Usage: bash native/cp0c-lvs-probe.sh EXISTING_LVS_RUN_DIRECTORY" >&2
  exit 1
fi
cp0p_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0p_python" || { echo "Existing CP0C KLayout Python environment is missing." >&2; exit 1; }
cp0p_here="$(cd "$(dirname "$0")" && pwd)"
exec "$cp0p_python" "$cp0p_here/cp0c-lvs-probe.py" "$@"
