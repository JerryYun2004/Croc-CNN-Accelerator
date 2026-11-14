#!/bin/bash
set -euo pipefail
cp1_root="$(cd "$(dirname "$0")" && pwd)"
cp1_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp1_python" || { echo 'Existing Croc Python environment not found.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp1_python" "$cp1_root/tools/run.py" "$@"
