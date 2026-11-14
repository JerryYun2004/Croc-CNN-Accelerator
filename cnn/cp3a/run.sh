#!/bin/bash
set -euo pipefail
cp3a_here="$(cd "$(dirname "$0")" && pwd)"
cp3a_repo="$(cd "$cp3a_here/../.." && pwd)"
cd "$cp3a_repo"
source native/env.sh
cp3a_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp3a_python" || { echo 'Existing Croc Python environment is missing.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp3a_python" "$cp3a_here/tools/run.py" "$@"
