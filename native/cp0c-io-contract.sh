#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != audit ]; then
  echo 'Usage: bash native/cp0c-io-contract.sh audit COMPLETED_PAD_MODEL_DIRECTORY' >&2
  exit 1
fi
shift
cp0ioc_here="$(cd "$(dirname "$0")" && pwd)"
cp0ioc_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0ioc_python" || { echo 'Existing Python environment is missing.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp0ioc_python" "$cp0ioc_here/cp0c_io_contract/audit.py" "$1" --results "$cp0ioc_here/results"
