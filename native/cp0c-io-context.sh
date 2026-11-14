#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-io-context.sh run COMPLETED_SRAM_CONTEXT_RUN' >&2
  exit 1
fi
shift
cp0io_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0io_python" || { echo 'Existing KLayout Python environment is missing.' >&2; exit 1; }
cp0io_here="$(cd "$(dirname "$0")" && pwd)"
exec "$cp0io_python" "$cp0io_here/cp0c-io-context.py" "$@"
