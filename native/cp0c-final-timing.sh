#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-final-timing.sh run SUCCESSFUL_INTEGRATION_REPLAY_DIRECTORY' >&2
  exit 1
fi
shift
cp0final_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0final_python" || { echo 'Existing native Python is missing.' >&2; exit 1; }
cp0final_here="$(cd "$(dirname "$0")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
exec "$cp0final_python" "$cp0final_here/cp0c_final_timing/main.py" "$@"
