#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-io-fill.sh run COMPLETED_POWER_PAD_RUN' >&2
  exit 1
fi
shift
cp0fill_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0fill_python" || { echo 'Existing KLayout Python environment is missing.' >&2; exit 1; }
cp0fill_here="$(cd "$(dirname "$0")" && pwd)"
exec "$cp0fill_python" "$cp0fill_here/cp0c-io-fill.py" "$@"
