#!/bin/bash
set -euo pipefail
cp2c_here="$(cd "$(dirname "$0")" && pwd)"
cp2c_repo="$(cd "$cp2c_here/../.." && pwd)"
cd "$cp2c_repo"
source native/env.sh
cp2c_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp2c_python" || { echo 'Existing Croc Python environment is missing.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp2c_python" "$cp2c_here/tools/run.py" "$@"
