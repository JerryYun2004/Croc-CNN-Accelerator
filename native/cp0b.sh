#!/bin/bash
# Native Croc checkpoint 0B: install -> synth -> sim.
# Run from the existing croc-cnn-native repository after CP0A passes.
# No RTL edits, dependency updates, Homebrew upgrades, or git commits.
# Uses pinned native OSS CAD Suite Yosys/Slang; existing Homebrew Verilator.
# Synthesis reports and functional netlist simulation do not establish
# routed die area, timing closure, SDF timing correctness, or equivalence.
set -euo pipefail

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
stage="${1:-help}"
case "$stage" in
    install|synth|sim) ;;
    help|--help|-h)
        printf '%s\n' 'From croc-cnn-native:' \
          '  bash native/cp0b.sh install' \
          '  bash native/cp0b.sh synth' \
          '  bash native/cp0b.sh sim' \
          'Stop at a failed stage and retain the printed evidence directory.'
        exit 0 ;;
    *) die "Unknown stage: $stage" ;;
esac

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] ||
    die 'This script targets native Apple Silicon macOS (arm64).'
root="$(git rev-parse --show-toplevel)"
cd "$root"
case "$root" in *[[:space:]]*) die 'Croc upstream scripts require a path without spaces.';; esac
[[ -f native/env.sh ]] || die 'Missing native/env.sh from checkpoint 0A.'
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
pin=29b2e748e72c592a5a895c2b11fa85bc9080f67a
release=2025-12-30
archive=oss-cad-suite-darwin-arm64-20251230.tgz
sha=a04443d93a0a4f80fcd2896ecc32d502cc041c340aff1103ea82c4e8d2fc4c10
tool_parent="$HOME/tools/croc-oss-cad-suite-$release"
suite="$tool_parent/oss-cad-suite"
export CROC_CP0B_YOSYS="$suite/bin/yosys"

mkdir -p native/results
run="$(mktemp -d "$root/native/results/cp0b-$stage-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
exec > >(tee "$run/driver.log") 2>&1
trap 'rc=$?; if [[ "$rc" != 0 ]]; then printf "FAILED (exit %s). Evidence: %s\n" "$rc" "$run" >&2; fi' EXIT
echo "Evidence: $run"

check_sources() {
    git merge-base --is-ancestor "$pin" HEAD || die 'Expected pinned Croc revision is not an ancestor of HEAD.'
    git diff --exit-code "$pin" -- \
      rtl sw Bender.yml Bender.lock env.sh scripts ihp13 \
      yosys/scripts yosys/src/abc.constr yosys/src/lazy_man_synth_library.aig \
      yosys/run_synthesis.sh verilator/run_verilator.sh \
      verilator/technology_netlist_yosys.f > "$run/source-diff.txt" ||
        die 'Baseline source or PDK changed; inspect source-diff.txt.'
    # CP0B uses the two PDK trees. artistic/artistic belongs to a separate
    # flow and was intentionally not initialized during native checkpoint 0A.
    git submodule status --recursive -- ihp13/sg13cmos5l ihp13/sg13g2 > "$run/submodules.txt"
    if grep -Eq '^[+U-]' "$run/submodules.txt"; then
        cat "$run/submodules.txt"
        die 'A required PDK submodule differs from its pinned revision or is uninitialized.'
    fi
}

record_tools() {
    {
        date -u
        sw_vers
        uname -m
        printf 'Croc baseline: %s\nWorking HEAD: ' "$pin"
        git rev-parse HEAD
        printf 'PDK: %s\nOSS CAD Suite: %s\nArchive SHA256: %s\n' "$CROC_PDK" "$release" "$sha"
        printf 'Yosys executable: %s\n' "$CROC_CP0B_YOSYS"
        "$CROC_CP0B_YOSYS" -V
        command -v verilator bender make sed gawk timeout
        verilator --version
        bender --version
        make --version
        riscv64-unknown-elf-gcc --version
        printf 'USER_CPPFLAGS=%s\nUSER_LDFLAGS=%s\n' "${USER_CPPFLAGS:-}" "${USER_LDFLAGS:-}"
        shasum -a 256 native/env.sh Bender.lock
    } > "$run/tools.txt"
    cat "$run/tools.txt"
}

check_slang() {
    [[ -x "$CROC_CP0B_YOSYS" ]] || die 'Run the install stage first.'
    cat > "$run/smoke.sv" <<'SV'
package smoke_pkg;
  typedef struct packed {logic [7:0] payload;} word_t;
endpackage
module smoke(input logic clk, input smoke_pkg::word_t a,
             output smoke_pkg::word_t q);
  always_ff @(posedge clk) q <= a;
endmodule
SV
    (
        cd "$run"
        "$CROC_CP0B_YOSYS" -Q -T -p 'plugin -i slang.so; read_slang --top smoke --cmd-rename +define,+define --compat-mode --keep-hierarchy --allow-use-before-declare --ignore-unknown-modules smoke.sv; hierarchy -check -top smoke; proc; check -assert; stat'
    ) 2>&1 | tee "$run/slang-smoke.log"
}

check_sources
if [[ "$stage" == install ]]; then
    mkdir -p "$tool_parent"
    if [[ ! -x "$CROC_CP0B_YOSYS" ]]; then
        [[ ! -e "$suite" ]] || die "Incomplete installation exists at $suite; retain it and move it aside before retrying."
        curl --fail --location --retry 3 \
          "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/$release/$archive" \
          -o "$tool_parent/$archive.part"
        printf '%s  %s\n' "$sha" "$tool_parent/$archive.part" | shasum -a 256 -c -
        mv "$tool_parent/$archive.part" "$tool_parent/$archive"
        # The publisher documents removing quarantine from its archive before
        # extraction. Scope this to this checksum-verified download only.
        if xattr -p com.apple.quarantine "$tool_parent/$archive" >/dev/null 2>&1; then
            xattr -d com.apple.quarantine "$tool_parent/$archive"
        fi
        tar -xzf "$tool_parent/$archive" -C "$tool_parent"
    fi
    check_slang
    record_tools
    echo 'PASS: 0B.1 native Yosys + Slang frontend'
    exit 0
fi

check_slang
record_tools
make --version | grep -q 'GNU Make' || die 'GNU Make must be first on PATH.'
sed --version | grep -q 'GNU sed' || die 'GNU sed must be first on PATH.'
command -v gawk timeout >/dev/null

if [[ "$stage" == synth ]]; then
    # A Yosys-only wrapper avoids replacing the already-tested Verilator.
    mkdir "$run/bin"
    cat > "$run/bin/yosys" <<'WRAPPER'
#!/bin/bash
exec "$CROC_CP0B_YOSYS" "$@"
WRAPPER
    chmod +x "$run/bin/yosys"
    # Preserve earlier generated results; require fresh synthesis outputs.
    for item in out reports tmp; do
        if [[ -e "yosys/$item" ]]; then mv "yosys/$item" "$run/previous-$item"; fi
    done
    (
        export PATH="$run/bin:$PATH"
        cd yosys
        bash run_synthesis.sh --synth
    ) 2>&1 | tee "$run/synthesis-console.log"
    cp yosys/croc.log "$run/synthesis.log"
    for item in croc_yosys.v netlist_debug.v; do
        [[ -s "yosys/out/$item" ]] || die "Missing synthesized $item"
    done
    cp -R yosys/reports "$run/reports"
    cp yosys/src/croc.flist "$run/croc.flist"
    for item in croc_synth.rpt croc_area.rpt croc_area_logic.rpt; do
        [[ -s "$run/reports/$item" ]] || die "Missing report $item"
    done
    # This dedicated report contains one CHECK result. Accept Yosys's
    # "Found and reported N problems." wording as well as the shorter form,
    # but require exactly one summary and a zero problem count.
    if ! gawk '
        /^[[:space:]]*Found( and reported)? [0-9]+ problems\.[[:space:]]*$/ {
            summaries++
            if ($(NF - 1) != 0) bad = 1
        }
        END { exit !(summaries == 1 && !bad) }
    ' "$run/reports/croc_synth.rpt"; then
        cat "$run/reports/croc_synth.rpt"
        die 'Yosys CHECK report does not contain exactly one zero-problem result; see the report above.'
    fi
    shasum -a 256 yosys/out/croc_yosys.v yosys/out/netlist_debug.v > "$run/netlists.sha256"
    cat "$run/reports/croc_area.rpt" "$run/reports/croc_area_logic.rpt"
    check_sources
    printf '%s\n' "$run" > native/results/cp0b-last-synth.txt
    echo 'PASS: 0B.2 synthesis, structural check, and cell-area reports'
    exit 0
fi

[[ -f native/results/cp0b-last-synth.txt ]] || die 'Run the synth stage successfully first.'
synth_run="$(cat native/results/cp0b-last-synth.txt)"
[[ -f "$synth_run/netlists.sha256" ]] || die 'Missing successful synthesis evidence.'
shasum -a 256 -c "$synth_run/netlists.sha256"
verilator --version | grep -q 'Verilator 5.052 ' ||
    die 'Expected CP0A Verilator 5.052. Restore its PATH or revalidate CP0A with the new version.'
for program in helloworld test/print_config test/test_compute; do
    [[ -s "sw/bin/$program.hex" ]] || die "Missing CP0A firmware: $program.hex"
done
shasum -a 256 sw/bin/helloworld.hex sw/bin/test/print_config.hex \
    sw/bin/test/test_compute.hex > "$run/firmware.sha256"
if [[ -e verilator/obj_dir_netlist_yosys ]]; then
    mv verilator/obj_dir_netlist_yosys "$run/previous-netlist-build"
fi
(
    cd verilator
    bash run_verilator.sh --build-netlist
) 2>&1 | tee "$run/netlist-build.log"
[[ -x verilator/obj_dir_netlist_yosys/Vtb_croc_soc ]] || die 'Missing netlist simulator executable.'
file verilator/obj_dir_netlist_yosys/Vtb_croc_soc | tee "$run/executable-type.txt"
cp verilator/croc_netlist_yosys.f "$run/croc_netlist_yosys.f"

for program in helloworld test/print_config test/test_compute; do
    name="${program##*/}"
    log="$run/$name.log"
    (
        cd verilator
        timeout --kill-after=30s 1800 ./obj_dir_netlist_yosys/Vtb_croc_soc \
            "+binary=../sw/bin/$program.hex"
    ) 2>&1 | tee "$log"
    grep -Fq '[JTAG] Simulation finished: SUCCESS' "$log" || die "$program did not report success."
    if grep -Eq 'Simulation finished: FAIL|%Error|Assertion failed' "$log"; then
        die "$program reported an error."
    fi
    if [[ "$name" == print_config ]]; then
        bash .github/scripts/check_sim.sh "$log"
    fi
    echo "PASS: post-synthesis $program"
done
check_sources
shasum -a 256 -c "$synth_run/netlists.sha256"
shasum -a 256 -c "$run/firmware.sha256"
{
    printf 'CP0B PASS: synthesis and functional debug-netlist simulation\n'
    printf 'Baseline: %s\nPDK: %s\nOSS CAD Suite: %s\n' "$pin" "$CROC_PDK" "$release"
    printf 'Synthesis evidence: %s\nSimulation evidence: %s\n' "$synth_run" "$run"
    printf 'Programs: helloworld, test/print_config, test/test_compute\n'
    printf 'This is not an SDF timing test or formal equivalence proof.\n'
    printf 'Cell area is not routed core/die area; no measured Fmax yet.\n'
    cat "$synth_run/netlists.sha256" "$run/firmware.sha256"
} > native/cp0b-summary.txt

mkdir -p "$run/evidence/synthesis" "$run/evidence/simulation"
cp "$synth_run/tools.txt" "$synth_run/synthesis.log" "$synth_run/netlists.sha256" \
    "$synth_run/submodules.txt" "$synth_run/croc.flist" "$run/evidence/synthesis/"
cp -R "$synth_run/reports" "$run/evidence/synthesis/"
cp "$run/tools.txt" "$run/firmware.sha256" "$run/netlist-build.log" \
    "$run/helloworld.log" "$run/print_config.log" "$run/test_compute.log" \
    "$run/executable-type.txt" "$run/croc_netlist_yosys.f" "$run/evidence/simulation/"
cp native/cp0b-summary.txt "$run/evidence/"
tar -czf "$root/native/results/cp0b-evidence-$(basename "$run").tar.gz" -C "$run/evidence" .
cat native/cp0b-summary.txt
echo 'PASS: 0B.3 all three post-synthesis firmware tests'
echo "Upload: $root/native/results/cp0b-evidence-$(basename "$run").tar.gz"
