#!/bin/bash
set -euo pipefail
if [ "$#" -ne 2 ] || [ "$1" != run ]; then
  echo 'Usage: bash native/cp0c-gpio-timing.sh run COMPLETED_FINAL_TIMING_DIRECTORY' >&2
  exit 1
fi
shift
cp0gpio_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp0gpio_python" || { echo 'Existing native Python is missing.' >&2; exit 1; }
cp0gpio_here="$(cd "$(dirname "$0")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
exec "$cp0gpio_python" "$cp0gpio_here/cp0c_gpio_timing/main.py" "$@"
