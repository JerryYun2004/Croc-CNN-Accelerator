#!/bin/bash
set -euo pipefail
cp2b_here="$(cd "$(dirname "$0")" && pwd)"
cp2b_repo="$(cd "$cp2b_here/../.." && pwd)"
cd "$cp2b_repo"
test -f native/env.sh || { echo 'Run from the existing native Croc repository.' >&2; exit 1; }
source native/env.sh
cp2b_python="$HOME/tools/croc-klayout-0.30.9/venv/bin/python3"
test -x "$cp2b_python" || { echo 'Existing Croc Python environment is missing.' >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
exec "$cp2b_python" "$cp2b_here/tools/run.py" "$@"
