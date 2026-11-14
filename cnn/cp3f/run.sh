#!/bin/bash
set -euo pipefail
cp3f_here="$(cd "$(dirname "$0")" && pwd)"
cp3f_repo="$(cd "$cp3f_here/../.." && pwd)"
cd "$cp3f_repo"
source native/env.sh
cp3f_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp3f_python" || { echo 'Existing Croc Python environment is missing.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp3f_python" "$cp3f_here/tools/run.py" "$@"
