#!/usr/bin/env python3
"""Repair final-route antennas in the reviewed Croc CP0C candidate on native macOS.

python3 native/cp0c-antenna-repair.py ROUTE_REPAIR_DIRECTORY [--preflight-only]
No rebuild of OpenROAD, synthesis, placement or CTS is required.
"""
import argparse
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

from cp0c_erc_support import require, sha256, verify_manifest, parse_metrics, classify_report

PIN = 'c463e998615210dc322c999fc9a9f56885dbd664'
ODB_SHA = '6f3bb3374a61bef22d43c3e947794dbb4f1579c3e083526c2b85edfe3498620b'
SDC_SHA = '86cc9f726f5f369cb19ddf207b15c0d81aecf4d2fa67d1a70f1e6ae665209ba9'
COUNTS = ('setup_violations', 'hold_violations', 'max_slew_violations', 'max_cap_violations', 'max_fanout_violations')
SLACKS = ('setup_tt', 'setup_ff', 'hold_tt', 'hold_ff')


def stream(command, cwd, env, logfile):
    with logfile.open('w') as f:
        p = subprocess.Popen(command, cwd=cwd, env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for line in p.stdout:
                print(line, end='', flush=True)
                f.write(line)
                f.flush()
            return p.wait()
        except BaseException:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            raise


def validate_timing(case, prefix):
    metrics = parse_metrics(case/f'reports/{prefix}.tsv')
    rows = classify_report(case/f'reports/{prefix}-erc.rpt')
    require(all(r[1] == 'I/O' for r in rows), 'Internal ERC findings remain.')
    for kind, key in (('max slew', 'max_slew_violations'), ('max capacitance', 'max_cap_violations'),
                      ('max fanout', 'max_fanout_violations')):
        require(sum(r[0] == kind for r in rows) == metrics[key], 'ERC report/count mismatch.')
    require(metrics['setup_violations'] == metrics['hold_violations'] == metrics['max_fanout_violations'] == 0,
            'Setup, hold or fanout findings remain.')
    (case/'reports/classified.tsv').write_text('check\tcategory\tpin\tlimit\tactual\tslack\n' +
                                              ''.join('\t'.join(r)+'\n' for r in rows))
    return metrics


def validate_stage(case, stage, expected_antenna, reference):
    log = (case/'console.log').read_text()
    require(f'CP0C_ANTENNA_STAGE_COMPLETE {stage}' in log.splitlines(), 'Missing stage-completion marker.')
    require('EST-0026' not in log, 'Estimated RC reported missing routes to pins.')
    units = (case/'reports/units.rpt').read_text()
    require(re.search(r'\btime\s+1ns\b', units) and re.search(r'\bcapacitance\s+1pF\b', units), 'Unexpected units.')
    physical = parse_metrics(case/'reports/physical.tsv')
    require(physical['supply_assignment_findings'] == 0, 'Supply assignment findings remain.')
    metrics = validate_timing(case, 'preflight' if stage == 'preflight' else 'final')
    if stage == 'preflight':
        require(physical['antenna_violating_nets'] == expected_antenna, 'Reloaded antenna count changed.')
        require(all(metrics[k] == reference[k] for k in COUNTS), 'Reloaded timing/ERC counts changed.')
        require(all(abs(metrics[k]-reference[k]) <= .005 for k in SLACKS), 'Reloaded slack differs by more than 5 ps.')
        require(abs(metrics['stdcell_area_um2']-reference['stdcell_area_um2']) < .01, 'Reloaded cell area changed.')
    else:
        require(physical['router_drvs'] == 0, 'Detailed-router violations remain.')
        require(physical['antenna_before'] == expected_antenna, 'Unexpected repair input.')
        require(physical['added_diodes'] > 0, 'No diodes added.')
        # Keep the known pad limitations visible; reject any increased count.
        for k in ('max_slew_violations', 'max_cap_violations'):
            require(metrics[k] <= reference[k], f'I/O violation count increased: {k}')
        for name in ('routed_no_fill.odb', 'routed_no_fill.sdc', 'estimated_tt', 'estimated_ff'):
            require((case/'out'/name).stat().st_size > 0, f'Missing output: {name}')
        for corner in ('tt', 'ff'):
            with (case/f'out/estimated_{corner}').open('rb') as f:
                require(b'*SPEF' in f.read(4096), 'Invalid estimated SPEF output.')
        if physical['antenna_violating_nets'] == 0:
            for name in ('croc_antenna_repaired.odb', 'croc_antenna_repaired.def', 'croc_antenna_repaired.sdc',
                         'croc_antenna_repaired.v', 'croc_antenna_repaired_lvs.v'):
                require((case/'out'/name).stat().st_size > 0, f'Missing final output: {name}')
    return metrics, physical


def next_pass_needed(before, after, pass_number, max_passes):
    require(after >= 0 and int(after) == after, 'Invalid antenna count.')
    if after == 0:
        return False
    require(after < before, f'Antenna count did not decrease ({before} -> {after}); stopped for review.')
    require(pass_number < max_passes, f'{after} antenna nets remain after {max_passes} passes; stopped for review.')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--max-passes', type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    require(sys.version_info >= (3, 11), 'Python 3.11 or later required.')
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Native Apple Silicon macOS required.')
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()).resolve()
    results = root/'native/results'
    source = args.source.resolve()
    require(source.parent == results and source.name.startswith('cp0c-route-repair-'),
            'Use the reviewed route-repair directory under native/results.')
    require((source/'exit-status.txt').read_text().strip() == '1', 'Expected the stopped antenna-47 run.')
    original = parse_metrics(source/'route/reports/physical.tsv')
    require(original == {'router_drvs': 0, 'antenna_violating_nets': 47, 'supply_assignment_findings': 0},
            'Source physical findings differ from the reviewed 0-DRV/47-antenna candidate.')
    reference = parse_metrics(source/'route/reports/final.tsv')
    require([reference[k] for k in COUNTS] == [0, 0, 71, 71, 0], 'Source timing/ERC counts differ from the review.')
    source_odb = source/'route/out/routed.odb'
    source_sdc = source/'route/out/croc_repaired.sdc'
    require(sha256(source_odb) == ODB_SHA and sha256(source_sdc) == SDC_SHA,
            'The source routed ODB or SDC differs from the reviewed checkpoint.')
    require(shutil.disk_usage(results).free >= 8*1024**3, 'At least 8 GiB free space required.')
    toolbase = Path.home()/'tools'/f'croc-openroad-{PIN}'
    tool = toolbase/'install/bin/openroad'
    lock = results/'cp0c.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f'Another CP0C command may be running. Inspect {lock}/owner.txt.') from None
    run = preserved = None
    status = 1
    try:
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=post-route-antenna-repair\n')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run = Path(tempfile.mkdtemp(prefix=f'cp0c-antenna-repair-{stamp}-', dir=results))
        print(f'Candidate: {run}\nVersion: 1.1-antenna-enum-fix\nThreads: 2\nMaximum repair passes: {args.max_passes}', flush=True)
        entries = []
        for manifest in (source/'source.sha256', source/'input.sha256', source/'route/output.sha256', toolbase/'tool.sha256'):
            entries.extend(verify_manifest(manifest))
        required = {tool.resolve(), source_odb, source_sdc, source/'input/targets.tsv'}
        required.update(source/f'route/out/estimated_{c}' for c in ('tt', 'ff'))
        require(required <= {Path(p).resolve() for _, p in entries}, 'Required inputs are not hash-covered.')
        entries.extend((sha256(p), str(p)) for p in source.rglob('*') if p.is_file())
        preserved = run/'source.sha256'
        preserved.write_text(''.join(f'{h}  {p}\n' for h, p in dict.fromkeys(entries)))
        inputs = run/'input'
        inputs.mkdir()
        shutil.copy2(source_odb, inputs/'routed.odb')
        shutil.copy2(source_sdc, inputs/'15pf.sdc')
        shutil.copy2(source/'input/targets.tsv', inputs/'targets.tsv')
        for c in ('tt', 'ff'):
            shutil.copy2(source/f'route/out/estimated_{c}', inputs/f'estimated_{c}')
        (run/'input.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in sorted(inputs.iterdir())))
        helpers = run/'helpers'
        helpers.mkdir()
        here = Path(__file__).resolve().parent
        for name in ('cp0c-antenna-repair.py', 'cp0c-antenna-repair.tcl', 'cp0c_pg_audit.tcl',
                     'cp0c_route_metrics.tcl', 'cp0c_erc_support.py'):
            shutil.copy2(here/name, helpers/name)
        (run/'reference').mkdir()
        shutil.copy2(source/'route/reports/final.tsv', run/'reference/final.tsv')
        shutil.copy2(source/'route/reports/physical.tsv', run/'reference/physical.tsv')
        (run/'provenance.txt').write_text(f'Source: {source}\nOpenROAD pin: {PIN}\nThreads: 2\n'
            'Input: reviewed routed.odb saved before standard-cell fillers.\n'
            'Constraints: saved 15 pF load; dynamic GPIO enables; clocks unchanged.\n'
            'Repair: diode-only, ratio margin 30, one iteration per process, detailed route after each pass.\n'
            'Timing remains global-route RC estimates, not extracted detailed-route RC.\n')

        def execute(stage, dirname, odb, sdc, expected):
            case = run/dirname
            case.mkdir()
            for folder in ('scripts', 'src'):
                shutil.copytree(source/'route'/folder, case/folder)
            (case/'inputs.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in (odb, sdc)))
            env = os.environ.copy()
            env.update(CP0A_ROOT=str(root), CP0A_WORK=str(case), CP0A_TOOL=str(tool), CP0A_HELPERS=str(helpers),
                       CP0A_SCRIPT=str(helpers/'cp0c-antenna-repair.tcl'), CP0A_STAGE=stage, CP0A_ODB=str(odb),
                       CP0A_SDC=str(sdc), CP0A_INPUT=str(inputs), CP0A_EXPECTED=str(expected),
                       CP0P_TARGETS=str(inputs/'targets.tsv'), CP0A_LOG=str(case/'openroad.log'))
            shell = r'''set -euo pipefail
cd "$CP0A_ROOT"
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
cd "$CP0A_WORK"
exec "$CP0A_TOOL" -no_init -exit "$CP0A_SCRIPT" -log "$CP0A_LOG"
'''
            print(f'\nRunning {dirname}: input antenna count {expected}', flush=True)
            rc = stream(['/bin/bash', '-c', shell], root, env, case/'console.log')
            (case/'exit-status.txt').write_text(str(rc)+'\n')
            outputs = sorted(p for p in (case/'out').glob('*') if p.is_file())
            if outputs:
                (case/'output.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in outputs))
            verify_manifest(case/'inputs.sha256')
            require(rc == 0, f'{dirname} stopped with OpenROAD exit {rc}; upload the saved evidence.')
            metrics, physical = validate_stage(case, stage, expected, reference)
            return case, metrics, physical

        _, preflight, _ = execute('preflight', 'preflight', inputs/'routed.odb', inputs/'15pf.sdc', 47)
        lines = ['CP0C ANTENNA PREFLIGHT COMPLETE', 'Reloaded the reviewed 47-antenna routed candidate.', '']
        if not args.preflight_only:
            odb, sdc, before = inputs/'routed.odb', inputs/'15pf.sdc', 47
            for n in range(1, args.max_passes+1):
                case, metrics, physical = execute('repair', f'pass{n:02d}', odb, sdc, before)
                after = int(physical['antenna_violating_nets'])
                print(f'Pass {n}: antenna nets {before} -> {after}; added diodes {int(physical["added_diodes"])}', flush=True)
                with (run/'progress.tsv').open('a') as f:
                    f.write(f'{n}\t{before}\t{after}\t{int(physical["added_diodes"])}\n')
                if not next_pass_needed(before, after, n, args.max_passes):
                    break
                odb, sdc, before = case/'out/routed_no_fill.odb', case/'out/routed_no_fill.sdc', after
            lines = ['CP0C POST-ROUTE ANTENNA REPAIR COMPLETE',
                     'Detailed-router DRVs: 0. Antenna-violating nets: 0. Supply-assignment findings: 0.', '',
                     '| Metric | Reloaded input | Repaired candidate |', '|---|---:|---:|']
            lines += [f'| {k} | {preflight[k]:.9g} | {metrics[k]:.9g} |'
                      for k in (*COUNTS, *SLACKS, 'stdcell_area_um2')]
            lines += ['', 'Slack units: ns; cell area: square micrometres.', f'Final outputs: {case}/out/',
                      'Final files: croc_antenna_repaired.{odb,def,sdc,v}, croc_antenna_repaired_lvs.v.',
                      'The saved routed_no_fill.odb is retained separately for further physical work.', '']
        lines += ['CP0C acceptance remains pending; no PASS stamp or current-flow pointer was changed.',
                  'Known I/O load/enable findings remain visible. Timing uses estimated RC.',
                  'Full GDS DRC/LVS, physical PG continuity and extracted-RC timing are separate checks.']
        (run/'summary.md').write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines), flush=True)
        status = 0
    except (Exception, KeyboardInterrupt) as exc:
        if run:
            (run/'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        print(f'STOPPED: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
    finally:
        try:
            if preserved is not None and preserved.exists():
                try:
                    verify_manifest(preserved)
                    (run/'preservation.txt').write_text('All recorded source/tool/evidence hashes still match.\n')
                except Exception as exc:
                    status = 1
                    with (run/'failure.txt').open('a') as f:
                        f.write(f'Preservation check failed: {exc}\n')
            if run:
                (run/'exit-status.txt').write_text(str(status)+'\n')
                archive = run.with_suffix('.tar.gz')
                with tarfile.open(archive, 'w:gz') as tar:
                    for p in sorted(run.rglob('*')):
                        rel = p.relative_to(run)
                        if p.is_file() and rel.parts[0] != 'input' and 'out' not in rel.parts:
                            tar.add(p, arcname=str(rel), recursive=False)
                print(f'Upload: {archive}', flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True)
            lock.rmdir()
    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
