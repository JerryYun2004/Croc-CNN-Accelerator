#!/bin/bash
# Open the saved Croc candidate in OpenROAD's local browser viewer.
# Usage: bash native/cp0c-view.sh [layout|timing] [postcheck-directory]
set -euo pipefail
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
mode=${1:-layout}
[[ $mode == layout || $mode == timing ]] || die 'Use layout or timing.'
[[ $# -le 2 ]] || die 'Too many arguments.'
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || die 'Run on native Apple Silicon macOS.'
root=$(git rev-parse --show-toplevel)
cd "$root"
source_run=${2:-native/results/cp0c-postcheck-20261018-205032-jo26bwbu}
source_run=$(cd "$source_run" && pwd -P)
[[ -f "$source_run/exit-status.txt" && $(cat "$source_run/exit-status.txt") == 0 ]] || die 'The source postcheck did not complete.'
pin=c463e998615210dc322c999fc9a9f56885dbd664
base="$HOME/tools/croc-openroad-$pin"
tool="$base/install/bin/openroad"
[[ -x $tool ]] || die "Missing OpenROAD: $tool"
[[ -f native/env.sh && -f env.sh ]] || die 'Run inside croc-cnn-native.'
mkdir -p native/results
run=$(mktemp -d "$root/native/results/cp0c-view-$mode-$(date +%Y%m%d-%H%M%S)-XXXXXX")
finish() {
  status=$?
  trap - EXIT
  printf '%s\n' "$status" > "$run/exit-status.txt"
  if [[ -f "$run/input.sha256" ]]; then
    if ! shasum -a 256 -c "$run/input.sha256" > "$run/input-check-after.txt" 2>&1; then
      printf 'Viewer input file changed; inspect %s/input-check-after.txt\n' "$run" >&2
      status=1
      printf '1\n' > "$run/exit-status.txt"
    fi
  fi
  # The database and SPEFs stay on the Mac; collect only small diagnostics.
  local archive="$run.tar.gz"
  COPYFILE_DISABLE=1 tar --exclude='./input' --exclude='./out' --exclude='./save' \
    -czf "$archive" -C "$run" . || true
  printf '\nViewer evidence: %s\nUpload if needed: %s\n' "$run" "$archive"
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
printf 'Viewer evidence: %s\nMode: %s\n' "$run" "$mode"
{
  shasum -a 256 -c "$base/tool.sha256"
  shasum -a 256 -c "$source_run/input.sha256"
  shasum -a 256 -c "$source_run/dynamic_15pf/output.sha256"
} > "$run/source-check.txt"
"$tool" -help > "$run/openroad-help.txt"
grep -q -- '-web' "$run/openroad-help.txt" || die 'This binary does not advertise the web viewer; upload this evidence.'
"$tool" -version > "$run/openroad-version.txt"
mkdir -p "$run/input" "$run/reports"
cp "$source_run/dynamic_15pf/out/pg_fixed.odb" "$run/input/pg_fixed.odb"
if [[ $mode == timing ]]; then
  cp -R "$source_run/dynamic_15pf/scripts" "$source_run/dynamic_15pf/src" "$run/"
  for name in 15pf.sdc estimated_tt estimated_ff; do
    cp "$source_run/input/$name" "$run/input/$name"
  done
fi
shasum -a 256 "$run"/input/* > "$run/input.sha256"
printf 'Source: %s\nMode: %s\nOpenROAD pin: %s\n' "$source_run" "$mode" "$pin" > "$run/provenance.txt"
export CP0V_MODE="$mode"
cat > "$run/view.tcl" <<'TCL'
if {[llength [info commands web_server]] != 1} {
    error "This OpenROAD binary has no web_server command"
}
set_thread_count 2
if {$::env(CP0V_MODE) eq "timing"} {
    source scripts/startup.tcl
}
read_db input/pg_fixed.odb
if {$::env(CP0V_MODE) eq "timing"} {
    read_sdc input/15pf.sdc
    set_propagated_clock [all_clocks]
    foreach corner {tt ff} {read_spef -corner $corner "input/estimated_${corner}"}
    # Public timing query initializes the graph before any endpoint queries.
    report_checks -path_delay min_max -group_path_count 5 \
        -format full_clock_expanded -fields {slew capacitance fanout} \
        -digits 6 > reports/timing.rpt
    report_check_types -max_slew -max_capacitance -max_fanout \
        -violators -digits 6 > reports/erc.rpt
    report_parasitic_annotation -report_unannotated > reports/parasitic-annotation.rpt
    check_setup -verbose > reports/check-setup.rpt
    tee -file reports/units.rpt {report_units}
    create_toolbar_button -text "ERC report" -script {
        report_check_types -max_slew -max_capacitance -max_fanout -violators -digits 6
    }
    puts "TIMING LOADED: saved 15 pF constraints, dynamic GPIO enables, estimated RC."
} else {
    puts "LAYOUT ONLY: libraries, timing constraints and parasitics are not loaded."
    puts "Use the timing mode in a separate session for timing analysis."
}
puts "VIEWER READY: repaired candidate before detailed routing."
puts "Find instance pad_gpio0_io or instances matching *cp0c_eco*."
puts "To close the server, enter: web_server -stop"
# No placement, routing, repair or write_db command is run by this launcher.
web_server -bind 127.0.0.1 -port 0
TCL
if [[ $mode == timing ]]; then
  source native/env.sh
  export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
  unset CROC_SKIP_TECH_SETUP
  source env.sh
fi
cd "$run"
printf 'A browser window should open. If needed, open the local URL printed by OpenROAD.\n'
"$tool" -no_init -exit -log "$run/openroad.log" "$run/view.tcl" 2>&1 | tee "$run/console.log"
