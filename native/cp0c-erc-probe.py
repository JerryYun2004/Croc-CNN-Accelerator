#!/usr/bin/env python3
"""CP0C electrical diagnostic, v1 (2026-10-18). Python standard library only.

Run from the native Croc repository:
    python3 native/cp0c-erc-probe.py

Loads a private copy of 04_croc.routed.odb and its saved SDC. Reconstructs
estimated global-route RC from the guides in that ODB; does NOT run routing.
Records the original 15 pF scenario, then a hypothetical 3 pF scenario.
The trial load is NOT a board specification and is NOT adopted by CP0C.
Records SRAM/clock-net connections for targeted repair planning.
Writes only a new native/results/cp0c-erc-probe-* directory and its archive,
plus a temporary shared CP0C lock. Never changes CP0C PASS files or pointers.

The default run needs the user's installed native OpenROAD. This script has
been checked using the supplied evidence and Tcl stubs, not run on that Mac.
"""

import argparse
import collections
import datetime
import hashlib
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile


CROC_PIN = '29b2e748e72c592a5a895c2b11fa85bc9080f67a'
OPENROAD_PIN = 'c463e998615210dc322c999fc9a9f56885dbd664'
NETLIST_SHA = '477d7fd6205aa4824ffcc53aadc24e7d11de5ac6511c7ff51b02e639e4d93149'
STAGES = ('preflight', 'floorplan', 'place', 'cts', 'route', 'finish')

TCL = r'''
proc probe_snapshot {label} {
    set path "reports/${label}"
    set f [open "${path}.tsv" w]
    foreach {key command} {
        setup_violations {sta::endpoint_violation_count max}
        hold_violations {sta::endpoint_violation_count min}
        max_slew_violations {sta::max_slew_violation_count}
        max_cap_violations {sta::max_capacitance_violation_count}
        max_fanout_violations {sta::max_fanout_violation_count}
    } {
        puts $f "$key\t[uplevel #0 $command]"
    }
    foreach corner {tt ff} {
        puts $f "setup_${corner}\t[sta::worst_slack -corner $corner -max]"
        puts $f "hold_${corner}\t[sta::worst_slack -corner $corner -min]"
    }
    close $f
    report_check_types -max_slew -max_capacitance -max_fanout -violators \
        -digits 6 > "${path}-erc.rpt"
    report_checks -path_delay min_max -group_path_count 3 \
        -format full_clock_expanded -fields {slew capacitance fanout} \
        -digits 6 > "${path}-timing.rpt"
}

proc probe_net {f category focus iterm} {
    set net [$iterm getNet]
    if {$net eq "NULL" || $net eq ""} {error "Unconnected target: $focus"}
    set netname [$net getName]
    foreach pin [$net getITerms] {
        set inst [$pin getInst]
        set master [$inst getMaster]
        set name "[$inst getName]/[[$pin getMTerm] getName]"
        # Directions describe cell pins. A top-level INPUT drives the design.
        puts $f [join [list $category $focus $netname [$net isDoNotTouch] \
            ITerm $name [$pin getIoType] [$master getName] \
            [$inst isDoNotTouch] [$inst getPlacementStatus] \
            [join [$inst getLocation] ,]] "\t"]
    }
    foreach pin [$net getBTerms] {
        puts $f [join [list $category $focus $netname [$net isDoNotTouch] \
            BTerm [$pin getName] [$pin getIoType] TOP NA NA NA] "\t"]
    }
}

proc probe_connections {} {
    set block [ord::get_db_block]
    set f [open reports/pin-connections.tsv w]
    puts $f "category\tfocus_pin\tnet\tnet_dont_touch\tkind\tconnected_pin\tdirection\tmaster\tinstance_dont_touch\tplacement\tlocation_dbu"
    set macros 0
    set outputs 0
    set dly 0
    set branches 0
    foreach inst [$block getInsts] {
        set master [$inst getMaster]
        set name [$inst getName]
        if {[$master isBlock]} {
            if {[$master getName] ne "RM_IHPSG13_1P_512x32_c2_bm_bist"} {
                error "Unexpected macro: $name / [$master getName]"
            }
            incr macros
            foreach pin [$inst getITerms] {
                set port [[$pin getMTerm] getName]
                set focus "$name/$port"
                if {[regexp {^A_DOUT\[[0-9]+\]$} $port]} {
                    incr outputs
                    probe_net $f SRAM_output $focus $pin
                } elseif {$port eq "A_DLY"} {
                    incr dly
                    probe_net $f SRAM_delay_control $focus $pin
                } elseif {$port in {A_MEN {A_DIN[20]} {A_DIN[22]}}} {
                    probe_net $f SRAM_input $focus $pin
                }
            }
        } elseif {[regexp {^clkbuf_2_[0-3]_0_soc_clk_i_regs$} $name]} {
            incr branches
            set pin [$inst findITerm X]
            if {$pin eq "NULL" || $pin eq ""} {error "Missing clock output: $name/X"}
            probe_net $f Clock_branch "$name/X" $pin
        }
    }
    close $f
    if {$macros != 2 || $outputs != 64 || $dly != 2 || $branches != 4} {
        error "Unexpected baseline targets: macros=$macros DOUT=$outputs DLY=$dly clock_branches=$branches"
    }
    puts "CP0C_ERC_TARGETS macros=$macros DOUT=$outputs DLY=$dly clock_branches=$branches"
}

if {[catch {
    source scripts/startup.tcl
    set_thread_count $::env(CP0C_ERC_THREADS)
    read_db $::env(CP0C_ERC_ODB)
    read_sdc $::env(CP0C_ERC_SDC)
    setDefaultParasitics
    set_propagated_clock [all_clocks]
    set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
    # The pinned GlobalRouter::haveRoutes loads guides from the saved ODB.
    # These reconstructed estimates can differ from the original live process.
    # No global_route, detailed_route, placement, or repair command is invoked.
    estimate_parasitics -global_routing
    tee -file reports/units.rpt {report_units}
    check_setup -verbose > reports/check-setup.rpt
    probe_snapshot baseline_reload
    probe_connections

    # Diagnostic only: changing this in-memory constraint never writes the
    # original SDC, database, reports, checkpoints, or CP0C acceptance stamps.
    set_load $::env(CP0C_ERC_TRIAL_LOAD_PF) [all_outputs]
    probe_snapshot trial_load
} message options]} {
    puts stderr "CP0C_ERC_PROBE_FAILED: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
puts "CP0C_ERC_PROBE_COMPLETE (diagnostic only; CP0C status unchanged)"
exit 0
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def verify_manifest(path):
    entries = []
    for line in Path(path).read_text().splitlines():
        digest, filename = line.split('  ', 1)
        require(re.fullmatch(r'[0-9a-f]{64}', digest), f'Invalid manifest: {path}')
        require(sha256(filename) == digest, f'Checksum mismatch: {filename}')
        entries.append((digest, filename))
    require(entries, f'Empty manifest: {path}')
    return entries


def parse_metrics(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        key, value = line.split('\t')
        require(key not in result, f'Duplicate metric: {key}')
        result[key] = float(value)
        require(math.isfinite(result[key]), f'Nonfinite metric: {key}')
    return result


def classify_report(path):
    rows = []
    kind = None
    for line in Path(path).read_text().splitlines():
        if line.strip() in ('max slew', 'max fanout', 'max capacitance'):
            kind = line.strip()
        if '(VIOLATED)' not in line or kind is None:
            continue
        parts = line.split()
        require(len(parts) == 5, f'Unrecognized ERC report row: {line}')
        pin, limit, actual, slack, _ = parts
        if pin.startswith(('gpio', 'pad_')):
            category = 'I/O'
        elif 'gen_sram_bank' in pin and '/A_DOUT[' in pin:
            category = 'SRAM output'
        elif 'gen_sram_bank' in pin:
            category = 'SRAM input'
        elif pin.startswith('clkbuf'):
            category = 'Clock tree'
        else:
            category = 'Other'
        rows.append((kind, category, pin, limit, actual, slack))
    return rows


def summarize(run, trial_load):
    reports = run / 'openroad/reports'
    original = parse_metrics(run / 'reference/cp0c-route.tsv')
    baseline = parse_metrics(reports / 'baseline_reload.tsv')
    trial = parse_metrics(reports / 'trial_load.tsv')
    units = (reports / 'units.rpt').read_text()
    require(re.search(r'\btime\s+1ns\b', units) and
            re.search(r'\bcapacitance\s+1pF\b', units), 'Unexpected time/capacitance units.')
    keys = ('max_slew_violations', 'max_cap_violations', 'max_fanout_violations',
            'setup_violations', 'hold_violations', 'setup_tt', 'setup_ff', 'hold_tt', 'hold_ff')
    lines = ['CP0C ERC DIAGNOSTIC COMPLETE — CP0C status is unchanged.', '',
             'RC: estimated from saved global-route guides; not extracted detailed-route RC.',
             f'Trial: {trial_load:g} pF external output load, in memory only.',
             'This trial is a sensitivity test, not an adopted interface specification.', '',
             '| Metric | Original live route | Reloaded 15 pF | Trial load |',
             '|---|---:|---:|---:|']
    for key in keys:
        lines.append(f'| {key} | {original[key]:.9g} | {baseline[key]:.9g} | {trial[key]:.9g} |')
    lines += ['', 'Slack values are ns. Compare trial with reloaded baseline; the saved-guide',
              'reconstruction may differ from the original live-process RC estimates.', '']
    for label in ('baseline_reload', 'trial_load'):
        rows = classify_report(reports / f'{label}-erc.rpt')
        counts = collections.Counter((r[0], r[1]) for r in rows)
        (reports / f'{label}-classified.tsv').write_text(
            'check\tcategory\tpin\tlimit\tactual\tslack\n' +
            ''.join('\t'.join(row) + '\n' for row in rows))
        lines.append(f'{label} — reported pin counts:')
        lines.extend(f'- {kind}, {category}: {count}' for (kind, category), count in sorted(counts.items()))
        expected = baseline if label == 'baseline_reload' else trial
        for kind, key in [('max slew', 'max_slew_violations'),
                          ('max capacitance', 'max_cap_violations'),
                          ('max fanout', 'max_fanout_violations')]:
            total = sum(n for (check, _), n in counts.items() if check == kind)
            require(total == expected[key], f'{label}: report/count mismatch for {kind}; retain evidence.')
        lines.append('')
    lines += ['Pin connectivity: openroad/reports/pin-connections.tsv.',
              'No constraint waivers, physical repairs, routing runs, or CP0C PASS stamps were applied.']
    summary = '\n'.join(lines) + '\n'
    (run / 'summary.md').write_text(summary)
    print(summary, flush=True)


def make_bundle(run):
    archive = run.with_suffix('.tar.gz')
    # Keep local ODB for inspection; the upload only needs reports and provenance.
    with tarfile.open(archive, 'w:gz') as tar:
        for p in sorted(run.rglob('*')):
            if p.is_file() and p.suffix != '.odb':
                tar.add(p, arcname=str(p.relative_to(run)), recursive=False)
    print(f'Upload: {archive}', flush=True)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--trial-load-pf', type=float, default=3.0,
                        help='Hypothetical external load, 0 < value < 15 pF (default: 3).')
    args = parser.parse_args()
    require(sys.version_info >= (3, 11), 'Python 3.11 or later is required.')
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64',
            'Run this diagnostic on the native Apple Silicon Mac.')
    require(math.isfinite(args.trial_load_pf) and 0 < args.trial_load_pf < 15,
            'Trial load must be finite and strictly between 0 and 15 pF.')
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()).resolve()
    results = root / 'native/results'
    require(results.is_dir(), 'Missing native/results; run from the CP0C project.')
    require((root / 'native/env.sh').is_file(), 'Missing native/env.sh.')
    flow = Path((results / 'cp0c-current.txt').read_text().strip()).resolve()
    require(flow.parent == results and flow.name.startswith('cp0c-flow-'), 'Invalid current flow path.')
    tool_base = Path.home() / 'tools' / f'croc-openroad-{OPENROAD_PIN}'
    openroad = tool_base / 'install/bin/openroad'
    require(openroad.is_file(), f'Missing pinned OpenROAD: {openroad}')
    require(sha256(root / 'yosys/out/croc_yosys.v') == NETLIST_SHA, 'CP0B netlist changed.')
    lock = results / 'cp0c.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f'A CP0C command may be running. Inspect {lock}/owner.txt; do not run concurrently.') from None
    run = None
    status = 1
    manifests = [flow / 'inputs.sha256', flow / 'tool.sha256', tool_base / 'tool.sha256']
    manifests += [flow / f'stamps/{name}.sha256' for name in STAGES]
    try:
        (lock / 'owner.txt').write_text(f'PID={os.getpid()}\nstage=erc-probe\n')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run = Path(tempfile.mkdtemp(prefix=f'cp0c-erc-probe-{stamp}-', dir=results))
        print(f'Diagnostic: {run}', flush=True)
        (run / 'provenance.txt').write_text(
            f'Croc pin: {CROC_PIN}\nOpenROAD pin: {OPENROAD_PIN}\nSource flow: {flow}\n'
            f'Host: {platform.platform()}\nPython: {sys.version}\nTrial load pF: {args.trial_load_pf}\n')
        shutil.copy2(Path(__file__).resolve(), run / 'probe.py')
        for name in STAGES:
            require((flow / f'stamps/{name}.pass').read_text().strip() == 'EXECUTION_COMPLETE',
                    f'Stage {name} did not complete.')
        verified = []
        for p in manifests:
            verified.extend(verify_manifest(p))
        require(any(Path(p).resolve() == openroad.resolve() for _, p in verified),
                'The pinned executable is not covered by the manifests.')
        (run / 'verified-inputs.sha256').write_text(''.join(f'{h}  {p}\n' for h, p in verified))
        print('Baseline inputs, reports, checkpoints, and executable checksums verified.', flush=True)
        work = run / 'openroad'
        work.mkdir()
        for folder in ('scripts', 'src'):
            shutil.copytree(flow / 'openroad' / folder, work / folder)
        ref = run / 'reference'
        ref.mkdir()
        for name in ('cp0c-route.tsv', 'cp0c-timing.rpt', 'cp0c-units.rpt'):
            shutil.copy2(flow / 'openroad/reports' / name, ref / name)
        shutil.copy2(flow / 'summary.txt', ref / 'baseline-summary.txt')
        require(re.search(r'\bcapacitance\s+1pF\b', (ref / 'cp0c-units.rpt').read_text()),
                'Trial value assumes capacitance units of pF.')
        checkpoint = run / 'checkpoint'
        checkpoint.mkdir()
        with zipfile.ZipFile(flow / 'openroad/save/04_croc.routed.zip') as z:
            for suffix in ('odb', 'sdc'):
                name = f'04_croc.routed.{suffix}'
                require(z.namelist().count(name) == 1, f'Missing or duplicate checkpoint member: {name}')
                with z.open(name) as source, (checkpoint / name).open('wb') as dest:
                    shutil.copyfileobj(source, dest)
        # The baseline is intentionally specific: verify the saved output load
        # remains the upstream 15 pF before labelling its reloaded results.
        sdc = (checkpoint / '04_croc.routed.sdc').read_text()
        loads = re.findall(r'^set_load\s+(?:-\S+\s+)*([0-9.eE+-]+)\s+', sdc, re.M)
        require(loads and all(float(v) == 15.0 for v in loads),
                'Saved SDC has unexpected/missing output loads; stop for review.')
        (run / 'probe.tcl').write_text(TCL)
        env = os.environ.copy()
        env.update(CP0C_ERC_ROOT=str(root), CP0C_ERC_WORK=str(work),
                   CP0C_ERC_TOOL=str(openroad), CP0C_ERC_SCRIPT=str(run / 'probe.tcl'),
                   CP0C_ERC_ODB=str(checkpoint / '04_croc.routed.odb'),
                   CP0C_ERC_SDC=str(checkpoint / '04_croc.routed.sdc'),
                   CP0C_ERC_LOG=str(run / 'openroad.log'),
                   CP0C_ERC_THREADS=(flow / 'threads.txt').read_text().strip(),
                   CP0C_ERC_TRIAL_LOAD_PF=str(args.trial_load_pf))
        require(re.fullmatch(r'[1-9][0-9]*', env['CP0C_ERC_THREADS']), 'Invalid saved thread count.')
        shell = r'''set -euo pipefail
cd "$CP0C_ERC_ROOT"
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
cd "$CP0C_ERC_WORK"
exec "$CP0C_ERC_TOOL" -no_init -exit "$CP0C_ERC_SCRIPT" -log "$CP0C_ERC_LOG"
'''
        with (run / 'console.log').open('w') as log:
            process = subprocess.Popen(['/bin/bash', '-c', shell], cwd=root, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                for line in process.stdout:
                    print(line, end='', flush=True)
                    log.write(line)
                    log.flush()
                rc = process.wait()
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
        require(rc == 0, f'OpenROAD diagnostic exited {rc}; inspect console.log.')
        require('CP0C_ERC_PROBE_COMPLETE (diagnostic only; CP0C status unchanged)' in
                (run / 'console.log').read_text().splitlines(), 'Missing completion marker.')
        for p in manifests:
            verify_manifest(p)
        (run / 'preservation.txt').write_text('All original manifest entries still match after the probe.\n')
        summarize(run, args.trial_load_pf)
        status = 0
    except (Exception, KeyboardInterrupt) as exc:
        if run:
            (run / 'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        print(f'STOPPED: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
    finally:
        try:
            if run:
                (run / 'exit-status.txt').write_text(str(status) + '\n')
                make_bundle(run)
        finally:
            (lock / 'owner.txt').unlink(missing_ok=True)
            lock.rmdir()
    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
