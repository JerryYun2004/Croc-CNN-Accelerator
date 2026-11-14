#!/bin/bash
set -euo pipefail
cp3c_here="$(cd "$(dirname "$0")" && pwd)"
cp3c_repo="$(cd "$cp3c_here/../.." && pwd)"
cd "$cp3c_repo"
source native/env.sh
cp3c_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp3c_python" || { echo 'Existing Croc Python environment is missing.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp3c_python" "$cp3c_here/tools/run.py" "$@"
