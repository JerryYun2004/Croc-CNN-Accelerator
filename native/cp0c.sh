#!/bin/bash
# Native Apple Silicon Croc implementation baseline. No RTL or SDC edits.
# Requires the successful CP0B project; invoke from its repository root.
set -euo pipefail
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
stage=${1:-help}
case "$stage" in
  install|preflight|floorplan|place|cts|route|finish|report|bundle) ;;
  help|-h|--help)
    printf '%s\n' 'Usage: bash native/cp0c.sh STAGE' \
      'Stages: install, preflight, floorplan, place, cts, route, finish, report, bundle' \
      'Run in that order. Stop on failure; bundle collects the current evidence.' \
      'CP0C_JOBS=2 limits build jobs. CP0C_THREADS=2 sets implementation threads.'
    exit 0 ;;
  *) die "Unknown stage: $stage" ;;
esac
[[ $# == 1 ]] || die 'Supply exactly one stage.'
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || die 'Requires native Apple Silicon macOS.'
root=$(git rev-parse --show-toplevel)
cd "$root"
case "$root" in *[[:space:]]*) die 'Use a project path without whitespace.';; esac
[[ -f native/env.sh && -f native/cp0c/driver.py ]] || die 'Missing native/env.sh or the CP0C helper folder.'
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
pin=29b2e748e72c592a5a895c2b11fa85bc9080f67a
or_pin=c463e998615210dc322c999fc9a9f56885dbd664
netlist_sha=477d7fd6205aa4824ffcc53aadc24e7d11de5ac6511c7ff51b02e639e4d93149
base="$HOME/tools/croc-openroad-$or_pin"
src="$base/src"
install_dir="$base/install"
openroad="$install_dir/bin/openroad"
jobs=${CP0C_JOBS:-2}
threads=${CP0C_THREADS:-2}
[[ $jobs =~ ^[1-9][0-9]*$ && $threads =~ ^[1-9][0-9]*$ ]] || die 'Jobs and threads must be positive integers.'
mkdir -p native/results
lock="$root/native/results/cp0c.lock"
mkdir "$lock" 2>/dev/null || die "Another CP0C command may be running. Inspect $lock/owner.txt; remove the lock only after that process has ended."
printf 'PID=%s\nstage=%s\n' "$$" "$stage" > "$lock/owner.txt"
run=$(mktemp -d "$root/native/results/cp0c-$stage-$(date +%Y%m%d-%H%M%S)-XXXXXX")
finish_driver() {
  rc=$?
  printf '%s\n' "$rc" > "$run/exit-status.txt"
  rm -f "$lock/owner.txt"
  rmdir "$lock"
  if [[ $rc != 0 ]]; then printf 'STOPPED (exit %s). Evidence: %s\n' "$rc" "$run" >&2; fi
}
trap finish_driver EXIT
exec > >(tee "$run/driver.log") 2>&1
printf '%s\n' "$run" > native/results/cp0c-last-command.txt
echo "Evidence: $run"
export CP0C_STAGE="$stage" CP0C_THREADS="$threads"

check_sources() {
  git merge-base --is-ancestor "$pin" HEAD || die 'The expected Croc baseline is not an ancestor of HEAD.'
  git diff --exit-code "$pin" -- rtl sw Bender.yml Bender.lock env.sh scripts ihp13 \
    yosys/scripts yosys/src/abc.constr yosys/src/lazy_man_synth_library.aig \
    yosys/run_synthesis.sh openroad/scripts openroad/src openroad/run_backend.sh \
    > "$run/source-diff.txt" || die 'Baseline source changed; inspect source-diff.txt.'
  git submodule status --recursive -- ihp13/sg13cmos5l ihp13/sg13g2 > "$run/submodules.txt"
  if grep -Eq '^[+U-]' "$run/submodules.txt"; then
    cat "$run/submodules.txt"
    die 'A required PDK submodule is uninitialized or differs from its pin.'
  fi
  # Check tracked edits inside initialized PDK submodules too. Downloaded,
  # untracked GDS files do not count as source edits.
  for pdk in ihp13/sg13cmos5l ihp13/sg13g2; do
    git -C "$pdk" diff --quiet HEAD -- || die "Tracked edits exist in $pdk."
    git -C "$pdk" submodule foreach --quiet --recursive \
      'git diff --quiet HEAD -- || exit 1' >> "$run/submodule-worktrees.txt" || die 'Tracked edits exist inside a PDK submodule.'
  done
  [[ -f native/cp0b-summary.txt ]] || die 'Missing successful CP0B summary.'
  grep -q '^CP0B PASS:' native/cp0b-summary.txt || die 'CP0B has not passed.'
  printf '%s  %s\n' "$netlist_sha" yosys/out/croc_yosys.v | shasum -a 256 -c -
}

record_host() {
  {
    date -u
    sw_vers
    uname -m
    sysctl -n hw.memsize
    df -h "$root"
    xcode-select -p
    clang --version
    printf 'Croc baseline: %s\nOpenROAD source pin: %s\nWorking HEAD: ' "$pin" "$or_pin"
    git rev-parse HEAD
    printf 'PDK=%s\nBuild jobs=%s\nImplementation threads=%s\n' "$CROC_PDK" "$jobs" "$threads"
    python3 --version
  } > "$run/host.txt"
  cat "$run/host.txt"
}

check_tool() {
  [[ -x "$openroad" && -f "$base/tool.sha256" ]] || die 'Run the install stage first.'
  shasum -a 256 -c "$base/tool.sha256"
  file -L "$openroad" | tee "$run/openroad-file.txt"
  grep -q 'Mach-O.*arm64' "$run/openroad-file.txt" || die 'OpenROAD is not a native ARM64 Mach-O executable.'
  "$openroad" -version | tee "$run/openroad-version.txt"
}

bundle() {
  # Snapshot logs only after commands have completed. The current driver.log
  # may still be flushing, so it is not a required evidence item.
  local dest="$run/evidence"
  mkdir -p "$dest/commands" "$dest/helpers"
  cp native/cp0c.sh "$dest/helpers/"
  cp -R native/cp0c "$dest/helpers/"
  cp native/cp0b-summary.txt "$dest/" 2>/dev/null || true
  if [[ -f native/results/cp0c-last-install.txt ]]; then
    local install_run
    install_run=$(cat native/results/cp0c-last-install.txt)
    if [[ $install_run == "$root/native/results/cp0c-install-"* && -d $install_run ]]; then
      mkdir -p "$dest/install"
      for f in host.txt openroad-version.txt openroad-file.txt openroad-submodules.txt \
          tool.sha256 .bazelversion MODULE.bazel MODULE.bazel.lock; do
        [[ ! -f "$install_run/$f" ]] || cp "$install_run/$f" "$dest/install/"
      done
    fi
  fi
  if [[ -f native/results/cp0c-current.txt ]]; then
    local flow_path
    flow_path=$(cat native/results/cp0c-current.txt)
    [[ $flow_path == "$root/native/results/cp0c-flow-"* && -d $flow_path ]] || die 'Invalid flow evidence path.'
    cp -R "$flow_path/openroad/reports" "$flow_path/openroad/logs" "$flow_path/stamps" "$dest/"
    cp "$flow_path/inputs.sha256" "$flow_path/tool.sha256" "$flow_path/threads.txt" "$flow_path/technology.txt" "$dest/"
    [[ ! -f "$flow_path/summary.txt" ]] || cp "$flow_path/summary.txt" "$dest/"
    # DEF/ODB checkpoints can be large. Hashes are sufficient for initial review;
    # all actual layouts remain in the local flow directory printed in summary.
    for f in "$flow_path"/stamps/*.command; do
      [[ -f $f ]] || continue
      local command_path
      command_path=$(cat "$f")
      [[ $command_path == "$root/native/results/cp0c-"* && -d $command_path ]] || continue
      cp -R "$command_path" "$dest/commands/"
    done
  fi
  # Include unsuccessful runs too, including a failed install or preflight.
  for command_path in "$root"/native/results/cp0c-*; do
    [[ -d $command_path && $command_path != "$run" && -f "$command_path/exit-status.txt" ]] || continue
    if [[ $(cat "$command_path/exit-status.txt") != 0 ]]; then
      mkdir -p "$dest/commands/$(basename "$command_path")"
      for f in "$command_path"/*; do
        [[ -f $f ]] && cp "$f" "$dest/commands/$(basename "$command_path")/"
      done
    fi
  done
  local archive="$root/native/results/cp0c-evidence-$(date +%Y%m%d-%H%M%S).tar.gz"
  COPYFILE_DISABLE=1 tar -czf "$archive" -C "$dest" .
  echo "Upload: $archive"
}

if [[ $stage == bundle ]]; then bundle; exit 0; fi
check_sources
record_host
command -v python3 zip unzip >/dev/null

if [[ $stage == install ]]; then
  # Bootstrap only; Bazel itself and source dependencies are pinned by the
  # checked-out .bazelversion / MODULE.bazel.lock files.
  HOMEBREW_NO_AUTO_UPDATE=1 brew install bazelisk
  mkdir -p "$base"
  if [[ ! -d "$src/.git" ]]; then
    [[ ! -e "$src" ]] || die "An incomplete source directory exists: $src"
    git clone --filter=blob:none --no-checkout https://github.com/The-OpenROAD-Project/OpenROAD.git "$src"
    git -C "$src" checkout --detach "$or_pin"
  fi
  [[ $(git -C "$src" rev-parse HEAD) == "$or_pin" ]] || die 'Existing OpenROAD checkout has a different revision.'
  git -C "$src" diff --quiet HEAD -- || die 'Existing OpenROAD sources contain tracked edits.'
  git -C "$src" submodule update --init --recursive
  git -C "$src" submodule status --recursive > "$run/openroad-submodules.txt"
  if grep -Eq '^[+U-]' "$run/openroad-submodules.txt"; then die 'OpenROAD submodule pin mismatch.'; fi
  (
    cd "$src"
    bazelisk version
    # Default optimized CLI build. No release ThinLTO, reducing build memory.
    # --stamp embeds the source version without enabling ThinLTO.
    bazelisk --output_user_root="$base/bazel-cache" run \
      --jobs="$jobs" --stamp --//:platform=cli //:install -- "$install_dir"
  ) 2>&1 | tee "$run/build.log"
  [[ -x "$openroad" ]] || die 'The install did not produce bin/openroad.'
  shasum -a 256 "$openroad" > "$base/tool.sha256"
  cp "$base/tool.sha256" "$run/tool.sha256"
  cp "$src/.bazelversion" "$src/MODULE.bazel" "$src/MODULE.bazel.lock" "$run/"
  check_tool
  printf '%s\n' "$run" > native/results/cp0c-last-install.txt
  echo 'PASS: 0C.1 native OpenROAD installation. Next: preflight.'
  exit 0
fi

check_tool
source "$root/env.sh"
if [[ $stage == preflight ]]; then
  flow=$(mktemp -d "$root/native/results/cp0c-flow-$(date +%Y%m%d-%H%M%S)-XXXXXX")
  python3 native/cp0c/driver.py prepare "$root" "$flow" "$pin" "$threads"
  cp "$base/tool.sha256" "$flow/tool.sha256"
  printf '%s\n' "$flow" > native/results/cp0c-current.txt
else
  [[ -f native/results/cp0c-current.txt ]] || die 'Run preflight first.'
  flow=$(cat native/results/cp0c-current.txt)
fi
[[ $flow == "$root/native/results/cp0c-flow-"* && -d $flow ]] || die 'Invalid current flow path.'
export CP0C_FLOW="$flow" CP0C_HELPERS="$flow/helpers"
# Thread count belongs to the flow; changing the shell variable later does not
# silently alter a partially completed implementation.
export CP0C_THREADS=$(cat "$flow/threads.txt")
shasum -a 256 -c "$flow/inputs.sha256" > "$run/input-check.txt"
shasum -a 256 -c "$flow/tool.sha256"
echo "Flow: $flow"

if [[ $stage == report ]]; then
  [[ -f "$flow/stamps/finish.pass" ]] || die 'Run finish successfully before report.'
  for completed in preflight floorplan place cts route finish; do
    shasum -a 256 -c "$flow/stamps/$completed.sha256" >> "$run/artifact-check.txt"
  done
  set +e
  python3 "$flow/helpers/driver.py" report "$flow" | tee "$run/summary.txt"
  status=${PIPESTATUS[0]}
  set -e
  bundle
  exit "$status"
fi

case "$stage" in
  preflight) predecessor= ;;
  floorplan) predecessor=preflight ;;
  place) predecessor=floorplan ;;
  cts) predecessor=place ;;
  route) predecessor=cts ;;
  finish) predecessor=route ;;
esac
if [[ -n $predecessor ]]; then
  [[ -f "$flow/stamps/$predecessor.pass" ]] || die "Run $predecessor successfully first."
  shasum -a 256 -c "$flow/stamps/$predecessor.sha256"
fi
python3 "$flow/helpers/driver.py" stage "$flow" "$stage" "$run"
export CP0C_STAGE_FILE="$run/stage.tcl"
(
  cd "$flow/openroad"
  "$openroad" -no_init -exit "$flow/helpers/entry.tcl" -log "$run/openroad.log"
) 2>&1 | tee "$run/openroad-console.log"
grep -qx "CP0C_STAGE_COMPLETE $stage" "$run/openroad-console.log" || die 'OpenROAD did not emit the stage completion marker.'
cp "$run/openroad-console.log" "$flow/openroad/logs/$stage.log"
python3 "$flow/helpers/driver.py" stamp "$flow" "$stage"
printf '%s\n' "$run" > "$flow/stamps/$stage.command"
check_sources
printf 'PASS: %s execution and required artifacts. Timing/area acceptance is evaluated by report.\n' "$stage"
