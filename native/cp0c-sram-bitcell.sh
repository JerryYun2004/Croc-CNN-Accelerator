#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-sram-bitcell.sh run REPAIRED_SRAM_POWER_RUN' >&2
  exit 1
fi
shift
cp0b_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0b_python" || { echo 'Existing CP0C KLayout Python environment is missing.' >&2; exit 1; }
cp0b_here="$(cd "$(dirname "$0")" && pwd)"
exec "$cp0b_python" "$cp0b_here/cp0c-sram-bitcell.py" "$@"
