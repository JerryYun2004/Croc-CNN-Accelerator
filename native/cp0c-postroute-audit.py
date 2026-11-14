#!/usr/bin/env python3
"""Audit the antenna-clean saved candidate without changing or rerouting it.

python3 native/cp0c-postroute-audit.py ANTENNA_REPAIR_DIRECTORY
Runs saved-SPEF and fresh-guide timing in separate OpenROAD processes.
Audit completion does not mean design acceptance.
"""
import argparse
import collections
import csv
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

from cp0c_erc_support import require, sha256, verify_manifest, parse_metrics

PIN = 'c463e998615210dc322c999fc9a9f56885dbd664'
ODB_SHA = '6968ce394b95df5611f9dbb22d126bec7ecefae637e08c204d3cf628dc831c06'
SDC_SHA = 'f0216b26bf10e67bee572a0c8058413f6f0cbfb2c9d167368fb17bc92efe202c'
COUNTS = ('setup_violations', 'hold_violations', 'max_slew_violations', 'max_cap_violations', 'max_fanout_violations')
SLACKS = ('setup_tt', 'setup_ff', 'hold_tt', 'hold_ff')


def classify(path):
    """Preserve a blank slack when OpenSTA omits that column in a fanout row."""
    rows, kind = [], None
    for line in path.read_text().splitlines():
        if line.strip() in ('max slew', 'max capacitance', 'max fanout'):
            kind = line.strip()
        if '(VIOLATED)' not in line:
            continue
        parts = line.split()
        require(kind is not None and parts[-1] == '(VIOLATED)', f'Unrecognized ERC row: {line}')
        require(len(parts) == 5 or (kind == 'max fanout' and len(parts) == 4), f'Unrecognized columns: {line}')
        pin, limit, actual = parts[:3]
        slack = parts[3] if len(parts) == 5 else ''
        category = 'I/O' if pin.startswith(('gpio', 'pad_')) else 'Internal'
        rows.append((kind, category, pin, limit, actual, slack))
    return rows


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


def inspect(case, mode):
    log = (case/'console.log').read_text()
    require(f'CP0C_POSTROUTE_AUDIT_COMPLETE {mode}' in log.splitlines(), 'Missing audit completion marker.')
    units = (case/'reports/units.rpt').read_text()
    require(re.search(r'\btime\s+1ns\b', units) and re.search(r'\bcapacitance\s+1pF\b', units), 'Unexpected units.')
    metrics = parse_metrics(case/'reports/final.tsv')
    physical = parse_metrics(case/'reports/physical.tsv')
    audit = parse_metrics(case/'reports/audit.tsv')
    rows = classify(case/'reports/final-erc.rpt')
    for kind, key in (('max slew', 'max_slew_violations'), ('max capacitance', 'max_cap_violations'),
                      ('max fanout', 'max_fanout_violations')):
        require(sum(row[0] == kind for row in rows) == metrics[key], 'ERC report/count mismatch.')
    (case/'reports/classified.tsv').write_text('check\tcategory\tpin\tlimit\tactual\treported_slack\n' +
                                              ''.join('\t'.join(row)+'\n' for row in rows))
    with (case/'reports/unannotated.tsv').open() as f:
        unannotated = list(csv.DictReader(f, delimiter='\t'))
    require(len(unannotated) == audit['unannotated_drivers'], 'Annotation classification coverage mismatch.')
    partial_names = (case/'reports/partially-unannotated-drivers.txt').read_text().splitlines()
    with (case/'reports/partially-unannotated-loads.tsv').open() as f:
        partial_loads = list(csv.DictReader(f, delimiter='\t'))
    require(len(partial_names) == len(set(partial_names)) == audit['partially_unannotated_drivers'],
            'Partial driver coverage mismatch.')
    require(len(partial_loads) == audit['partially_unannotated_loads'], 'Partial load coverage mismatch.')
    partial_set = set(partial_names)
    require(all(row['driver'] in partial_set and row['missing_load'] for row in partial_loads),
            'Unresolved partial-annotation load entry.')
    warnings = [line for line in log.splitlines() if '[WARNING EST-0026]' in line and 'Missing route to pin' in line]
    (case/'reports/missing-route-warnings.txt').write_text('\n'.join(warnings)+'\n')
    categories = collections.Counter(row['category'] for row in unannotated)
    annotation_expected = categories == {'NO_OTHER_LOADS': 475, 'NO_NET': 20, 'LOADED_PAD_NET': 80}
    pg = parse_metrics(case/'reports/final-pg-counts.tsv')
    require(pg['findings'] == 0 and pg['target_pins'] == 170, 'PG assignment audit changed.')
    require(physical['antenna_violating_nets'] == 0 and audit['physical_signature_unchanged'] == 1,
            'Physical candidate changed or has antenna findings.')
    model_ready = not warnings and 'EST-0026' not in log and annotation_expected and not partial_names
    internal = sum(row[1] == 'Internal' for row in rows)
    if mode == 'fresh_guides':
        for corner in ('tt', 'ff'):
            with (case/f'out/estimated_{corner}').open('rb') as f:
                require(b'*SPEF' in f.read(4096), 'Invalid estimated SPEF output.')
    return dict(metrics=metrics, physical=physical, audit=audit, internal_erc=internal,
                missing_route_warnings=len(warnings), annotation_categories=dict(categories),
                model_ready=model_ready)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    require(sys.version_info >= (3, 11), 'Python 3.11 or later required.')
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Native Apple Silicon macOS required.')
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()).resolve()
    results = root/'native/results'
    source = args.source.resolve()
    require(source.parent == results and source.name.startswith('cp0c-antenna-repair-'),
            'Use the reviewed antenna-repair directory under native/results.')
    require((source/'exit-status.txt').read_text().strip() == '1', 'Expected the stopped timing/ERC run.')
    physical = parse_metrics(source/'pass01/reports/physical.tsv')
    require(physical == {'router_drvs': 0, 'antenna_before': 47, 'antenna_violating_nets': 0,
                         'added_diodes': 57, 'supply_assignment_findings': 0}, 'Source physical metrics differ.')
    reference = parse_metrics(source/'pass01/reports/final.tsv')
    require([reference[k] for k in COUNTS] == [0, 2, 72, 71, 3], 'Source timing/ERC counts differ.')
    odb = source/'pass01/out/croc_antenna_repaired.odb'
    sdc = source/'pass01/out/croc_antenna_repaired.sdc'
    require(sha256(odb) == ODB_SHA and sha256(sdc) == SDC_SHA, 'Source ODB/SDC differs from the reviewed candidate.')
    require(shutil.disk_usage(results).free >= 3*1024**3, 'At least 3 GiB free space required.')
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
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=postroute-model-audit\n')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run = Path(tempfile.mkdtemp(prefix=f'cp0c-postroute-audit-{stamp}-', dir=results))
        print(f'Audit: {run}\nVersion: 1.1-annotation-sections\nThreads: 2\nNo physical repair or rerouting.', flush=True)
        entries = []
        for manifest in (source/'source.sha256', source/'input.sha256', source/'pass01/output.sha256', toolbase/'tool.sha256'):
            entries.extend(verify_manifest(manifest))
        required = {tool.resolve(), odb, sdc, source/'input/targets.tsv'}
        required.update(source/f'pass01/out/estimated_{c}' for c in ('tt', 'ff'))
        require(required <= {Path(p).resolve() for _, p in entries}, 'Required inputs are not hash-covered.')
        entries.extend((sha256(p), str(p)) for p in source.rglob('*') if p.is_file())
        preserved = run/'source.sha256'
        preserved.write_text(''.join(f'{h}  {p}\n' for h, p in dict.fromkeys(entries)))
        inputs = run/'input'
        inputs.mkdir()
        shutil.copy2(odb, inputs/'routed.odb')
        shutil.copy2(sdc, inputs/'15pf.sdc')
        shutil.copy2(source/'input/targets.tsv', inputs/'targets.tsv')
        for c in ('tt', 'ff'):
            shutil.copy2(source/f'pass01/out/estimated_{c}', inputs/f'estimated_{c}')
        input_manifest = run/'input.sha256'
        input_manifest.write_text(''.join(f'{sha256(p)}  {p}\n' for p in sorted(inputs.iterdir())))
        helpers = run/'helpers'
        helpers.mkdir()
        here = Path(__file__).resolve().parent
        for name in ('cp0c-postroute-audit.py', 'cp0c-postroute-audit.tcl', 'cp0c_pg_audit.tcl',
                     'cp0c_route_metrics.tcl', 'cp0c_erc_support.py', 'cp0c_postroute_annotation.tcl',
                     'cp0c_annotation_sections.tcl'):
            shutil.copy2(here/name, helpers/name)
        (run/'reference').mkdir()
        for name in ('final.tsv', 'physical.tsv', 'added-diodes.tsv'):
            shutil.copy2(source/'pass01/reports'/name, run/'reference'/name)
        (run/'provenance.txt').write_text(f'Source: {source}\nOpenROAD pin: {PIN}\nThreads: 2\n'
            'Same filled antenna-clean ODB and saved 15 pF/dynamic-OE SDC in both fresh processes.\n'
            'Modes: saved estimated SPEF replay, then estimation from reloaded ODB guides.\n'
            'No routing, physical repair, constraint relaxation, or CP0C PASS stamp.\n'
            'RC remains an estimate, not extracted post-route RC.\n')
        data = {}
        for mode in ('saved_spef', 'fresh_guides'):
            case = run/mode
            case.mkdir()
            for folder in ('scripts', 'src'):
                shutil.copytree(source/'pass01'/folder, case/folder)
            env = os.environ.copy()
            env.update(CP0Q_ROOT=str(root), CP0Q_WORK=str(case), CP0Q_TOOL=str(tool), CP0Q_HELPERS=str(helpers),
                       CP0Q_SCRIPT=str(helpers/'cp0c-postroute-audit.tcl'), CP0Q_MODE=mode, CP0Q_ODB=str(inputs/'routed.odb'),
                       CP0Q_SDC=str(inputs/'15pf.sdc'), CP0Q_INPUT=str(inputs),
                       CP0P_TARGETS=str(inputs/'targets.tsv'), CP0Q_LOG=str(case/'openroad.log'))
            shell = r'''set -euo pipefail
cd "$CP0Q_ROOT"
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
cd "$CP0Q_WORK"
exec "$CP0Q_TOOL" -no_init -exit "$CP0Q_SCRIPT" -log "$CP0Q_LOG"
'''
            print(f'\nRunning {mode}', flush=True)
            rc = stream(['/bin/bash', '-c', shell], root, env, case/'console.log')
            (case/'exit-status.txt').write_text(str(rc)+'\n')
            outputs = sorted(p for p in (case/'out').glob('*') if p.is_file())
            if outputs:
                (case/'output.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in outputs))
            require(rc == 0, f'{mode} stopped with OpenROAD exit {rc}; upload the evidence.')
            data[mode] = inspect(case, mode)
        old, fresh = data['saved_spef'], data['fresh_guides']
        reproduced = all(old['metrics'][k] == reference[k] for k in COUNTS)
        reproduced &= all(abs(old['metrics'][k]-reference[k]) <= .005 for k in SLACKS)
        model_ready = reproduced and fresh['model_ready'] and fresh['physical']['design_has_routing'] == 1
        core_clean = (model_ready and fresh['internal_erc'] == 0 and
                      fresh['metrics']['setup_violations'] == fresh['metrics']['hold_violations'] == 0 and
                      fresh['audit']['check_setup_clean'] == 1)
        lines = ['CP0C POST-ROUTE AUDIT COMPLETE', 'This is an audit result, not CP0C acceptance.', '',
                 f'Saved-SPEF control reproduces the stopped run: {reproduced}',
                 f'Fresh guide RC model passes coverage checks: {model_ready}',
                 f'Fresh-model internal timing/ERC checks clean: {core_clean}', '',
                 '| Metric | Saved SPEF replay | Fresh ODB-guide estimate |', '|---|---:|---:|']
        lines += [f'| {k} | {old["metrics"][k]:.9g} | {fresh["metrics"][k]:.9g} |'
                  for k in (*COUNTS, *SLACKS, 'stdcell_area_um2')]
        lines += ['', f'Printed fresh-model missing-route warnings: {fresh["missing_route_warnings"]}',
                  f'Partially unannotated drivers, saved/fresh: {int(old["audit"]["partially_unannotated_drivers"])}/{int(fresh["audit"]["partially_unannotated_drivers"])}',
                  f'Reported missing loads, saved/fresh: {int(old["audit"]["partially_unannotated_loads"])}/{int(fresh["audit"]["partially_unannotated_loads"])}',
                  f'Fresh-model unannotated driver categories: {fresh["annotation_categories"]}',
                  'Diode load inventories: each mode/reports/diode-nets.tsv and diode-connections.tsv.',
                  'Warning-free guide estimates are not extracted-RC signoff or proof of physical net continuity.',
                  'I/O constraints remain 15 pF with dynamic enables. Original files are preserved.']
        (run/'summary.md').write_text('\n'.join(lines)+'\n')
        (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
        print('\n'.join(lines), flush=True)
        verify_manifest(input_manifest)
        # Expected findings are the object of this audit; record them without
        # reporting a design PASS. Execution errors above still return nonzero.
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
