#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-pad-model.sh run COMPLETED_GPIO_TIMING_DIRECTORY' >&2
  exit 1
fi
shift
cp0pad_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0pad_python" || { echo 'Existing native Python is missing.' >&2; exit 1; }
cp0pad_here="$(cd "$(dirname "$0")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
exec "$cp0pad_python" "$cp0pad_here/cp0c_pad_model/main.py" "$@"
