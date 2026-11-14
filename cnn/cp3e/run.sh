#!/bin/bash
set -euo pipefail
cp3e_here="$(cd "$(dirname "$0")" && pwd)"
cp3e_repo="$(cd "$cp3e_here/../.." && pwd)"
cd "$cp3e_repo"
source native/env.sh
cp3e_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp3e_python" || { echo 'Existing Croc Python environment is missing.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp3e_python" "$cp3e_here/tools/run.py" "$@"
