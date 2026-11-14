#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-io-power.sh run COMPLETED_SIGNAL_REPLAY_RUN' >&2
  exit 1
fi
shift
cp0power_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0power_python" || { echo 'Existing KLayout Python environment is missing.' >&2; exit 1; }
cp0power_here="$(cd "$(dirname "$0")" && pwd)"
exec "$cp0power_python" "$cp0power_here/cp0c-io-power.py" "$@"
