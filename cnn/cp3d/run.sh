#!/bin/bash
set -euo pipefail
cp3d_here="$(cd "$(dirname "$0")" && pwd)"
cp3d_repo="$(cd "$cp3d_here/../.." && pwd)"
cd "$cp3d_repo"
source native/env.sh
cp3d_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp3d_python" || { echo 'Existing Croc Python environment is missing.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp3d_python" "$cp3d_here/tools/run.py" "$@"
