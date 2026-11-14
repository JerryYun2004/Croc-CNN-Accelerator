#!/bin/bash
set -euo pipefail
cp3b_here="$(cd "$(dirname "$0")" && pwd)"
cp3b_repo="$(cd "$cp3b_here/../.." && pwd)"
cd "$cp3b_repo"
source native/env.sh
cp3b_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp3b_python" || { echo 'Existing Croc Python environment is missing.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp3b_python" "$cp3b_here/tools/run.py" "$@"
