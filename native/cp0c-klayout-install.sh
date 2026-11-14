#!/bin/bash
# Build the upstream Qt-free strmrun executable with Ruby DRC support.
set -euo pipefail
mode="${1:-install}"
[[ "$mode" == install || "$mode" == recover ]] || {
  echo 'Usage: cp0c-klayout-install.sh [install|recover]' >&2; exit 2;
}
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  echo 'Native Apple Silicon macOS required.' >&2; exit 1;
}
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(git rev-parse --show-toplevel)"
toolbase="$HOME/tools/croc-klayout-0.30.9"
pin=6270877110ef808dd442fd2244164cec06a7b10e
mkdir -p "$root/native/results" "$toolbase"
run="$(mktemp -d "$root/native/results/cp0c-klayout-install-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
mkdir "$toolbase/install.lock" || { echo 'An installation may already be running.' >&2; exit 1; }
finish() {
  rc=$?
  trap - EXIT
  echo "$rc" > "$run/exit-status.txt"
  rmdir "$toolbase/install.lock"
  tar -czf "$run.tar.gz" -C "$run" .
  echo "Installer evidence: $run.tar.gz"
  exit "$rc"
}
trap finish EXIT
exec > >(tee "$run/console.log") 2>&1
echo "Evidence: $run"
echo 'KLayout 0.30.9: native command-line build with Ruby/Python support, without Qt.'
echo "Mode: $mode; package 1.3-sram-placeholders"
mkdir "$run/helpers"
cp "$here/cp0c-klayout-install.sh" "$here/cp0c-gds-smoke.py" "$here/cp0c_gds_runtime.py" "$run/helpers/"

verify_install() {
  [[ -x "$toolbase/install/strmrun" && -x "$toolbase/venv/bin/python3" ]] || {
    echo 'Completed build and Python environment are required; recovery will not rebuild.'; return 1;
  }
  [[ "$(git -C "$toolbase/src" rev-parse HEAD)" == "$pin" ]] || { echo 'KLayout source pin differs.'; return 1; }
  git -C "$toolbase/src" diff --exit-code
  git -C "$toolbase/src" diff --cached --exit-code
  echo "$pin" > "$run/source-revision.txt"
  file "$toolbase/install/strmrun" | tee "$run/architecture.txt"
  lipo -archs "$toolbase/install/strmrun" | grep -qw arm64
  otool -L "$toolbase/install/strmrun" > "$run/linked-libraries.txt"
  "$toolbase/venv/bin/python3" -m pip freeze > "$run/python-packages.txt"
}

finalize_install() {
  verify_install
  if [[ -f "$toolbase/runtime.sha256" ]]; then
    (cd "$toolbase" && shasum -a 256 -c runtime.sha256) > "$run/prior-hash-check.txt"
  fi
  "$toolbase/venv/bin/python3" - "$toolbase" "$run/runtime-before.sha256" <<'PY'
from pathlib import Path
import hashlib,sys
p=Path(sys.argv[1])
files=sorted(x for x in (p/'install').rglob('*') if x.is_file())
if not files: raise SystemExit('Empty KLayout installation.')
with Path(sys.argv[2]).open('w') as f:
    for x in files:
        with x.open('rb') as h: digest=hashlib.file_digest(h,'sha256').hexdigest()
        f.write(f'{digest}  {x.relative_to(p)}\n')
PY
  "$toolbase/venv/bin/python3" "$here/cp0c-gds-smoke.py" "$toolbase/install/strmrun" "$run/smoke"
  (cd "$toolbase" && shasum -a 256 -c "$run/runtime-before.sha256") > "$run/preservation.txt"
  cp "$run/runtime-before.sha256" "$toolbase/runtime.sha256"
  printf 'KLayout 0.30.9\nSource %s\nRuby DRC smoke passed\nLauncher 1.3-sram-placeholders\n' "$pin" > "$toolbase/READY"
  cp "$toolbase/READY" "$run/READY"
  echo 'INSTALL COMPLETE. Next: bash native/cp0c-gds.sh run INTERNAL_CLOSE_DIRECTORY'
}

if [[ -f "$toolbase/READY" ]]; then
  verify_install
  (cd "$toolbase" && shasum -a 256 -c runtime.sha256)
  "$toolbase/venv/bin/python3" "$here/cp0c-gds-smoke.py" "$toolbase/install/strmrun" "$run/smoke"
  echo 'Existing installation verified.'
  exit 0
fi
if [[ "$mode" == recover ]]; then
  echo 'Reusing the completed native build. No Homebrew install or compilation will run.'
  finalize_install
  exit 0
fi
[[ -x /opt/homebrew/bin/brew ]] || { echo 'ARM Homebrew missing at /opt/homebrew/bin/brew.'; exit 1; }
eval "$(/opt/homebrew/bin/brew shellenv)"
xcode-select -p
python3 - "$toolbase" <<'PY'
import shutil,sys
if shutil.disk_usage(sys.argv[1]).free < 12*1024**3:
    raise SystemExit('At least 12 GiB free space required for the first build.')
PY
brew install qtbase ruby@3.4 python@3.13
qmake="$(brew --prefix qtbase)/bin/qmake"
ruby="$(brew --prefix ruby@3.4)/bin/ruby"
py="$(brew --prefix python@3.13)/bin/python3.13"
pyinc="$("$py" -c 'import sysconfig; print(sysconfig.get_path("include"))')"
pylib="$("$py" - <<'PY'
from pathlib import Path
import sys,sysconfig
candidates=[Path(sysconfig.get_config_var('LIBDIR'))/sysconfig.get_config_var('LDLIBRARY'),
            Path(sys.base_prefix)/'Python']
for p in candidates:
    if p.is_file():
        print(p.resolve());break
else:raise SystemExit('Cannot find the Homebrew Python shared library.')
PY
)"
"$py" -m venv "$toolbase/venv"
"$toolbase/venv/bin/python3" -m pip install --only-binary=:all: 'klayout==0.30.9'
"$toolbase/venv/bin/python3" -m pip freeze > "$run/python-packages.txt"
if [[ ! -d "$toolbase/src/.git" ]]; then
  [[ ! -e "$toolbase/src" ]] || { echo 'Unexpected existing source directory.'; exit 1; }
  git init "$toolbase/src"
  git -C "$toolbase/src" remote add origin https://github.com/KLayout/klayout.git
  git -C "$toolbase/src" fetch --depth 1 origin "$pin"
  git -C "$toolbase/src" checkout --detach "$pin"
fi
[[ "$(git -C "$toolbase/src" rev-parse HEAD)" == "$pin" ]] || { echo 'KLayout source pin differs.'; exit 1; }
git -C "$toolbase/src" diff --exit-code
git -C "$toolbase/src" diff --cached --exit-code
mkdir -p "$toolbase/build" "$toolbase/install"
brew list --versions qtbase ruby@3.4 python@3.13 > "$run/homebrew-versions.txt"
"$qmake" -v
"$ruby" -v
(
  cd "$toolbase/src"
  # qmake is needed to build; the resulting executable has no Qt dependency.
  # Python is also needed by upstream's Qt-free resource compiler (pyqrc).
  bash build.sh -release -without-qt -python "$py" -pyinc "$pyinc" -pylib "$pylib" -ruby "$ruby" \
    -qmake "$qmake" -libexpat -nolibgit2 -nolstream \
    -build "$toolbase/build" -bin "$toolbase/install" \
    -rpath "$toolbase/install" -j2
)
finalize_install
