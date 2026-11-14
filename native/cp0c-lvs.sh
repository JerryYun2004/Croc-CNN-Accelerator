#!/bin/bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
if [[ ! -x "$py" ]]; then
  echo 'The completed CP0C KLayout environment is required.' >&2
  exit 1
fi
case "${1:-}" in
  run) shift; exec "$py" "$here/cp0c-lvs.py" "$@" ;;
  *) echo 'Usage: bash native/cp0c-lvs.sh run GDS_DRC_DIRECTORY' >&2; exit 2 ;;
esac
