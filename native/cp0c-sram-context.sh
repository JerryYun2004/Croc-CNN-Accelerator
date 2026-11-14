#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-sram-context.sh run COMPLETED_BITCELL_RUN' >&2
  exit 1
fi
shift
cp0a_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0a_python" || { echo 'Existing KLayout Python environment is missing.' >&2; exit 1; }
cp0a_here="$(cd "$(dirname "$0")" && pwd)"
exec "$cp0a_python" "$cp0a_here/cp0c-sram-context.py" "$@"
