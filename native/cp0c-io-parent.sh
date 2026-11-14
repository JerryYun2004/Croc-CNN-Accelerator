#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo 'Usage: bash native/cp0c-io-parent.sh {run COMPLETED_IO_CONTEXT_RUN | replay COMPLETED_IO_PARENT_RUN}' >&2
  exit 1
fi
case "$1" in
  run) cp0p_script=cp0c-io-parent.py ;;
  replay) cp0p_script=cp0c-io-parent-replay.py ;;
  *) echo 'Expected run or replay.' >&2; exit 1 ;;
esac
shift
cp0p_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0p_python" || { echo 'Existing KLayout Python environment is missing.' >&2; exit 1; }
cp0p_here="$(cd "$(dirname "$0")" && pwd)"
exec "$cp0p_python" "$cp0p_here/$cp0p_script" "$@"
