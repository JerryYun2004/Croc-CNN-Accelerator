#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-sram-power.sh run ORIGINAL_LVS_DIRECTORY' >&2
  exit 2
fi
cp0p_here="$(cd "$(dirname "$0")" && pwd)"
cp0p_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
if [ ! -x "$cp0p_python" ]; then
  echo "Missing completed CP0C Python environment: $cp0p_python" >&2
  exit 1
fi
exec "$cp0p_python" -u "$cp0p_here/cp0c-sram-power.py" "$2"
