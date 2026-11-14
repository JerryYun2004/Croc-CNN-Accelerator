#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo 'Usage: bash native/cp0c-io-signal.sh run INPUT_PAD_RUN | replay COMPLETED_SIGNAL_RUN' >&2
  exit 1
fi
cp0s_mode="$1"
shift
case "$cp0s_mode" in
  run) cp0s_script=cp0c-io-signal.py ;;
  replay) cp0s_script=cp0c-io-signal-replay.py ;;
  *) echo 'Mode must be run or replay.' >&2; exit 1 ;;
esac
cp0s_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0s_python" || { echo 'Existing KLayout Python environment is missing.' >&2; exit 1; }
cp0s_here="$(cd "$(dirname "$0")" && pwd)"
exec "$cp0s_python" "$cp0s_here/$cp0s_script" "$@"
