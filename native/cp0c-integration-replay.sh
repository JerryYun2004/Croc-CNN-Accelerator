#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-integration-replay.sh run INTEGRATION_RUN' >&2
  exit 1
fi
shift
cp0replay_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0replay_python" || { echo 'Existing native KLayout Python is missing.' >&2; exit 1; }
cp0replay_here="$(cd "$(dirname "$0")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
exec "$cp0replay_python" "$cp0replay_here/cp0c_integration_replay/main.py" "$@"
