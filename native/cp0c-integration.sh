#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-integration.sh run COMPLETED_FILLER_CORNER_RUN' >&2
  exit 1
fi
shift
cp0integration_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0integration_python" || { echo 'Existing native KLayout Python environment is missing.' >&2; exit 1; }
cp0integration_here="$(cd "$(dirname "$0")" && pwd)"
exec "$cp0integration_python" "$cp0integration_here/cp0c-integration.py" "$@"
