#!/usr/bin/env python3
"""Run a bounded physical-repair experiment on native Apple Silicon.

Usage: python3 native/cp0c-erc-repair.py trial [--reuse-control PRIOR_RUN]

Runs three separate OpenROAD processes from the original CTS checkpoint:
control_15pf, repair_15pf, repair_3pf. Each does fresh global routing and two
repair passes. No detailed routing, no original-source edits, no CP0C PASS.
The 3 pF case is hypothetical; it does not establish a board specification.
"""
import argparse
import collections
import datetime
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

from cp0c_erc_support import require, sha256, verify_manifest, parse_metrics, classify_report

OPENROAD_PIN = 'c463e998615210dc322c999fc9a9f56885dbd664'
NETLIST_SHA = '477d7fd6205aa4824ffcc53aadc24e7d11de5ac6511c7ff51b02e639e4d93149'
STAGES = ('preflight', 'floorplan', 'place', 'cts', 'route', 'finish')
CASES = (('control_15pf', 15, 0), ('repair_15pf', 15, 1), ('repair_3pf', 3, 1))
DRIVER_VERSION = '1.2-returned-instance'
# With -spef_file out/estimated, this pinned OpenROAD writes these exact
# corner-specific names, without a .spef extension.
SPEF_NAMES = ('estimated_tt', 'estimated_ff')


def stream(command, cwd, env, log_path):
    with log_path.open('w') as log:
        p = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        try:
            for line in p.stdout:
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
            return p.wait()
        except BaseException:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            raise


def validate_spef_files(out):
    for name in SPEF_NAMES:
        path = out / name
        require(path.is_file(), f'Missing estimated SPEF file: {path}')
        with path.open('rb') as f:
            header = f.read(4096)
            f.seek(max(0, path.stat().st_size - 256))
            tail = f.read()
        require(header.startswith(b'*SPEF ') and b'*DESIGN "croc_chip"' in header
                and b'*T_UNIT 1 NS' in header and b'*C_UNIT 1 PF' in header
                and tail.rstrip().endswith(b'*END'),
                f'Unexpected or incomplete estimated SPEF content: {path}')


def case_summary(case):
    log = (case / 'console.log').read_text()
    require('CP0C_ERC_REPAIR_CASE_COMPLETE' in log.splitlines(), 'Missing case completion marker.')
    # The earlier diagnostic reloaded approximate guides with missing pin
    # routes. This experiment requires a clean fresh routing estimate.
    require('[WARNING EST-0026]' not in log, 'Missing pin routes in fresh RC estimation; case is invalid.')
    reports = case / 'reports'
    units = (reports / 'units.rpt').read_text()
    require(re.search(r'\btime\s+1ns\b', units) and re.search(r'\bcapacitance\s+1pF\b', units),
            'Unexpected timing/capacitance units.')
    metrics = parse_metrics(reports / 'final.tsv')
    rows = classify_report(reports / 'final-erc.rpt')
    counts = collections.Counter((row[0], row[1]) for row in rows)
    for kind, key in [('max slew', 'max_slew_violations'),
                      ('max capacitance', 'max_cap_violations'),
                      ('max fanout', 'max_fanout_violations')]:
        require(sum(v for (k, _), v in counts.items() if k == kind) == metrics[key],
                f'ERC report/count mismatch: {kind}')
    (reports / 'classified.tsv').write_text('check\tcategory\tpin\tlimit\tactual\tslack\n' +
        ''.join('\t'.join(row) + '\n' for row in rows))
    outputs = sorted((case / 'out').glob('*'))
    require(all((case / 'out' / name).is_file() for name in ('trial.odb', 'trial.sdc', 'trial.v')),
            'Missing trial physical outputs.')
    validate_spef_files(case / 'out')
    (case / 'output.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in outputs if p.is_file()))
    return metrics, counts


def reuse_control(previous, run, flow, helpers, threads):
    """Copy a completed control after checking its inputs, recipe and outputs."""
    previous = previous.resolve()
    require(previous.parent == run.parent and previous != run
            and previous.name.startswith('cp0c-erc-repair-'),
            'Reuse requires a previous experiment directory under native/results.')
    require((previous / 'original-inputs.sha256').read_bytes()
            == (run / 'original-inputs.sha256').read_bytes(),
            'Reuse rejected: original input/tool manifests differ.')
    require(f'Threads: {threads}' in (previous / 'provenance.txt').read_text().splitlines(),
            'Reuse rejected: implementation thread count differs.')
    # Only repair-only procedures changed in 1.2. The reporting procedure and
    # complete top-level recipe must match, including the load setup and gates.
    marker = 'proc erc_snapshot {label} {'
    old_tcl = (previous / 'helpers/cp0c-erc-repair.tcl').read_text()
    new_tcl = (helpers / 'cp0c-erc-repair.tcl').read_text()
    require(old_tcl.count(marker) == new_tcl.count(marker) == 1
            and old_tcl.split(marker, 1)[1] == new_tcl.split(marker, 1)[1],
            'Reuse rejected: control/reporting Tcl recipe differs.')
    source = previous / 'control_15pf'
    require((source / 'exit-status.txt').read_text().strip() == '0',
            'Reuse rejected: previous control did not exit successfully.')
    for folder in ('scripts', 'src'):
        def tree_hashes(base):
            return {str(p.relative_to(base)): sha256(p) for p in base.rglob('*') if p.is_file()}
        require(tree_hashes(source / folder) == tree_hashes(flow / 'openroad' / folder),
                f'Reuse rejected: {folder} differs from the current flow snapshot.')
    entries = verify_manifest(source / 'output.sha256')
    require({Path(p).resolve() for _, p in entries}
            == {p.resolve() for p in (source / 'out').iterdir() if p.is_file()},
            'Reuse rejected: output manifest does not cover the previous output directory.')
    required = {'trial.odb', 'trial.v', 'trial.sdc', *SPEF_NAMES}
    require(required.issubset({Path(p).name for _, p in entries}),
            'Reuse rejected: required physical outputs are not covered by hashes.')
    dest = run / 'control_15pf'
    shutil.copytree(source, dest)
    # Revalidate reports, units, RC warnings and SPEF contents in the copy.
    # Write new absolute output hashes only in the new experiment.
    result = case_summary(dest)
    (dest / 'reused-from.txt').write_text(f'{source}\n')
    (dest / 'source-output.sha256').write_bytes((source / 'output.sha256').read_bytes())
    print(f'Reused verified control: {source}', flush=True)
    return result


def summarize(run, results):
    lines = ['CP0C ERC REPAIR EXPERIMENT COMPLETE — CP0C acceptance is unchanged.', '',
             'All cases start independently from the same original CTS checkpoint.',
             'Control uses the same routing/repair recipe without the 85 targeted buffers.',
             '3 pF is a hypothetical output load, not an adopted board specification.',
             'Timing uses fresh global-route RC estimates, not detailed-route extraction.', '',
             '| Metric | Control 15 pF | Repaired 15 pF | Repaired 3 pF |',
             '|---|---:|---:|---:|']
    for key in ('max_slew_violations', 'max_cap_violations', 'max_fanout_violations',
                'setup_violations', 'hold_violations', 'setup_tt', 'setup_ff',
                'hold_tt', 'hold_ff', 'stdcell_area_um2'):
        values = ' | '.join(f'{results[name][0][key]:.9g}' for name, _, _ in CASES)
        lines.append(f'| {key} | {values} |')
    lines += ['', 'Slack units: ns. Area units: square micrometres.', '']
    reused = run / 'control_15pf/reused-from.txt'
    if reused.exists():
        lines += [f'Control copied and revalidated from: {reused.read_text().strip()}', '']
    for name, _, _ in CASES:
        lines.append(f'{name}:')
        counts = results[name][1]
        lines.extend(f'- {kind}, {category}: {n}' for (kind, category), n in sorted(counts.items()))
        if not counts:
            lines.append('- No reported slew/capacitance/fanout violations in this estimate.')
        internal = sum(n for (_, category), n in counts.items() if category != 'I/O')
        lines.append(f'- Internal electrical findings: {internal}')
        lines.append('')
    lines += ['Next gate: review this evidence before detailed routing.',
              'No case is a CP0C PASS. Detailed route, antenna checks, final timing,',
              'and functional/equivalence validation of the modified netlist remain pending.']
    text = '\n'.join(lines) + '\n'
    (run / 'summary.md').write_text(text)
    print(text, flush=True)


def bundle(run):
    archive = run.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        for p in sorted(run.rglob('*')):
            # Large physical outputs remain locally, with recorded hashes.
            is_estimated_spef = p.parent.name == 'out' and p.name in SPEF_NAMES
            if p.is_file() and not is_estimated_spef and p.suffix not in ('.odb', '.spef', '.v', '.guide'):
                tar.add(p, arcname=str(p.relative_to(run)), recursive=False)
    print(f'Upload: {archive}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('stage', choices=['trial'])
    parser.add_argument('--reuse-control', type=Path, metavar='PRIOR_RUN',
                        help='Reuse a completed control with matching inputs, recipe and output hashes.')
    args = parser.parse_args()
    require(sys.version_info >= (3, 11), 'Python 3.11 or later is required.')
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Requires native Apple Silicon macOS.')
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()).resolve()
    results_dir = root / 'native/results'
    flow = Path((results_dir / 'cp0c-current.txt').read_text().strip()).resolve()
    require(flow.parent == results_dir and flow.name.startswith('cp0c-flow-'), 'Invalid current flow.')
    require(sha256(root / 'yosys/out/croc_yosys.v') == NETLIST_SHA, 'Original CP0B netlist changed.')
    tool_base = Path.home() / 'tools' / f'croc-openroad-{OPENROAD_PIN}'
    tool = tool_base / 'install/bin/openroad'
    lock = results_dir / 'cp0c.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f'A CP0C command may be running. Inspect {lock}/owner.txt.') from None
    run = None
    status = 1
    try:
        (lock / 'owner.txt').write_text(f'PID={os.getpid()}\nstage=erc-repair-trial\n')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run = Path(tempfile.mkdtemp(prefix=f'cp0c-erc-repair-{stamp}-', dir=results_dir))
        print(f'Experiment: {run}', flush=True)
        print(f'Driver version: {DRIVER_VERSION}', flush=True)
        manifests = [flow / 'inputs.sha256', flow / 'tool.sha256', tool_base / 'tool.sha256']
        manifests += [flow / f'stamps/{stage}.sha256' for stage in STAGES]
        verified = []
        for stage in STAGES:
            require((flow / f'stamps/{stage}.pass').read_text().strip() == 'EXECUTION_COMPLETE',
                    f'Incomplete original stage: {stage}')
        for manifest in manifests:
            verified.extend(verify_manifest(manifest))
        require(any(Path(p).resolve() == tool.resolve() for _, p in verified), 'Tool is not covered by manifests.')
        (run / 'original-inputs.sha256').write_text(''.join(f'{h}  {p}\n' for h, p in verified))
        here = Path(__file__).resolve().parent
        helpers = run / 'helpers'
        helpers.mkdir()
        for name in ('cp0c-erc-repair.py', 'cp0c-erc-repair.tcl', 'cp0c_erc_support.py'):
            shutil.copy2(here / name, helpers / name)
        threads = (flow / 'threads.txt').read_text().strip()
        require(re.fullmatch(r'[1-9][0-9]*', threads), 'Invalid saved thread count.')
        (run / 'provenance.txt').write_text(f'Driver version: {DRIVER_VERSION}\nOriginal flow: {flow}\nOpenROAD pin: {OPENROAD_PIN}\n'
            f'Host: {platform.platform()}\nThreads: {threads}\n'
            'Source checkpoint: 03_croc.cts.zip\nTargeted buffer insertions per repair case: 85\n')
        checkpoint = run / 'checkpoint'
        checkpoint.mkdir()
        with zipfile.ZipFile(flow / 'openroad/save/03_croc.cts.zip') as z:
            for suffix in ('odb', 'sdc'):
                name = f'03_croc.cts.{suffix}'
                require(z.namelist().count(name) == 1, f'Missing/duplicate checkpoint member: {name}')
                with z.open(name) as src, (checkpoint / name).open('wb') as dst:
                    shutil.copyfileobj(src, dst)
        sdc = (checkpoint / '03_croc.cts.sdc').read_text()
        loads = re.findall(r'^set_load\s+(?:-\S+\s+)*([0-9.eE+-]+)\s+', sdc, re.M)
        require(loads and all(float(v) == 15 for v in loads), 'Unexpected original output loads.')
        all_results = {}
        for name, load, repair in CASES:
            if name == 'control_15pf' and args.reuse_control is not None:
                all_results[name] = reuse_control(args.reuse_control, run, flow, helpers, threads)
                continue
            print(f'\nRunning {name}: external load {load} pF; targeted repair={repair}', flush=True)
            case = run / name
            case.mkdir()
            for folder in ('scripts', 'src'):
                shutil.copytree(flow / 'openroad' / folder, case / folder)
            env = os.environ.copy()
            env.update(CP0C_ERC_ROOT=str(root), CP0C_ERC_WORK=str(case), CP0C_ERC_TOOL=str(tool),
                CP0C_ERC_SCRIPT=str(helpers / 'cp0c-erc-repair.tcl'), CP0C_ERC_THREADS=threads,
                CP0C_ERC_ODB=str(checkpoint / '03_croc.cts.odb'), CP0C_ERC_SDC=str(checkpoint / '03_croc.cts.sdc'),
                CP0C_ERC_LOAD_PF=str(load), CP0C_ERC_REPAIR=str(repair), CP0C_ERC_LOG=str(case / 'openroad.log'))
            shell = r'''set -euo pipefail
cd "$CP0C_ERC_ROOT"
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
cd "$CP0C_ERC_WORK"
exec "$CP0C_ERC_TOOL" -no_init -exit "$CP0C_ERC_SCRIPT" -log "$CP0C_ERC_LOG"
'''
            rc = stream(['/bin/bash', '-c', shell], root, env, case / 'console.log')
            (case / 'exit-status.txt').write_text(str(rc) + '\n')
            require(rc == 0, f'{name} failed with OpenROAD exit {rc}.')
            all_results[name] = case_summary(case)
        for manifest in manifests:
            verify_manifest(manifest)
        (run / 'preservation.txt').write_text('All original manifest entries still match.\n')
        summarize(run, all_results)
        status = 0
    except (Exception, KeyboardInterrupt) as exc:
        if run:
            (run / 'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        print(f'STOPPED: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
    finally:
        try:
            if run:
                (run / 'exit-status.txt').write_text(str(status) + '\n')
                bundle(run)
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
