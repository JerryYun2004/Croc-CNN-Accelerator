#!/bin/bash
# Native CP0C GDS/DRC entry point. No changes to Croc's RTL or constraints.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
toolbase="$HOME/tools/croc-klayout-0.30.9"
case "${1:-}" in
  install|recover) exec /bin/bash "$here/cp0c-klayout-install.sh" "$1" ;;
  run)
    shift
    if [[ ! -x "$toolbase/venv/bin/python3" || ! -f "$toolbase/READY" ]]; then
      echo 'Run: bash native/cp0c-gds.sh install' >&2; exit 1
    fi
    exec "$toolbase/venv/bin/python3" "$here/cp0c-gds-drc.py" "$@" ;;
  *) echo 'Usage: bash native/cp0c-gds.sh install | recover | run INTERNAL_CLOSE_DIRECTORY' >&2; exit 2 ;;
esac
