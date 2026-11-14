#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-io-pad.sh run COMPLETED_PARENT_REPLAY_RUN' >&2
  exit 1
fi
shift
cp0d_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0d_python" || { echo 'Existing KLayout Python environment is missing.' >&2; exit 1; }
cp0d_here="$(cd "$(dirname "$0")" && pwd)"
exec "$cp0d_python" "$cp0d_here/cp0c-io-pad.py" "$@"
