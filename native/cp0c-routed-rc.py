#!/usr/bin/env python3
"""Build and inspect a routed-wire LEF RC model; preserve the saved candidate.

python3 native/cp0c-routed-rc.py POSTROUTE_AUDIT_DIRECTORY
Diagnostic model only: no calibrated coupling/process extraction, no CP0C PASS.
"""
import argparse
import collections
import csv
import datetime
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

from cp0c_erc_support import require, sha256, verify_manifest, parse_metrics
from cp0c_rc_common import classify, stream

PIN = 'c463e998615210dc322c999fc9a9f56885dbd664'
ODB_SHA = '6968ce394b95df5611f9dbb22d126bec7ecefae637e08c204d3cf628dc831c06'
SDC_SHA = 'f0216b26bf10e67bee572a0c8058413f6f0cbfb2c9d167368fb17bc92efe202c'
COUNTS = ('setup_violations', 'hold_violations', 'max_slew_violations', 'max_cap_violations', 'max_fanout_violations')
SLACKS = ('setup_tt', 'setup_ff', 'hold_tt', 'hold_ff')


def read_rows(path):
    with path.open() as f:
        return list(csv.DictReader(f, delimiter='\t'))


def spef_stats(path):
    """Reject empty, nonfinite, negative or multi-valued RC data before replay."""
    counts = collections.Counter()
    units = {}
    section = None
    in_net = False
    with path.open() as f:
        require(f.readline().startswith('*SPEF '), 'Not a SPEF file.')
        for line in f:
            p = line.split()
            if not p:
                continue
            key = p[0]
            if key in ('*C_UNIT', '*R_UNIT', '*T_UNIT'):
                units[key] = ' '.join(p[1:])
            elif key == '*D_NET':
                require(not in_net and len(p) == 3, 'Malformed or incomplete SPEF net.')
                value = float(p[-1])
                require(math.isfinite(value) and value >= 0, 'Invalid SPEF net capacitance.')
                counts['nets'] += 1
                counts['positive_net_capacitances'] += value > 0
                in_net, section = True, None
            elif key in ('*CONN', '*CAP', '*RES'):
                require(in_net, 'SPEF section outside a net.')
                section = key
            elif key == '*END':
                require(in_net, 'SPEF END without a net.')
                in_net, section = False, None
                counts['ends'] += 1
            elif in_net and section == '*CONN' and key in ('*I', '*P'):
                counts['connections'] += 1
            elif in_net and section in ('*CAP', '*RES') and key.isdigit():
                value = float(p[-1])
                require(math.isfinite(value) and value >= 0, 'Invalid RC value in SPEF.')
                tag = 'capacitances' if section == '*CAP' else 'resistances'
                counts[tag] += 1
                counts['positive_'+tag] += value > 0
    require(not in_net and counts['nets'] == counts['ends'], 'Incomplete SPEF output.')
    require(all(counts[k] > 0 for k in ('nets', 'connections', 'positive_net_capacitances',
                                      'positive_capacitances', 'positive_resistances')), 'SPEF has no useful RC network.')
    require(set(units) == {'*C_UNIT', '*R_UNIT', '*T_UNIT'}, 'Missing SPEF units.')
    return counts, units


def inspect_replay(case, reference):
    metrics = parse_metrics(case/'reports/final.tsv')
    audit = parse_metrics(case/'reports/audit.tsv')
    rows = classify(case/'reports/final-erc.rpt')
    for kind, key in (('max slew', 'max_slew_violations'), ('max capacitance', 'max_cap_violations'),
                      ('max fanout', 'max_fanout_violations')):
        require(sum(r[0] == kind for r in rows) == metrics[key], 'ERC report/count mismatch.')
    (case/'reports/classified.tsv').write_text('check\tcategory\tpin\tlimit\tactual\treported_slack\n' +
                                              ''.join('\t'.join(row)+'\n' for row in rows))
    full = read_rows(case/'reports/unannotated.tsv')
    partial = (case/'reports/partially-unannotated-drivers.txt').read_text().splitlines()
    loads = read_rows(case/'reports/partially-unannotated-loads.tsv')
    require(len(full) == audit['unannotated_drivers'], 'Full annotation coverage mismatch.')
    require(len(partial) == len(set(partial)) == audit['partially_unannotated_drivers'], 'Partial driver coverage mismatch.')
    require(len(loads) == audit['partially_unannotated_loads'], 'Partial load coverage mismatch.')
    partial_set = set(partial)
    require(all(r['driver'] in partial_set and r['missing_load'] for r in loads), 'Orphan partial load.')
    known = {(r['driver'], r['category'], r['net']) for r in read_rows(reference)}
    unexpected = [r for r in full if (r['driver'], r['category'], r['net']) not in known]
    (case/'reports/unexpected-unannotated.tsv').write_text('driver\tcategory\tnet\n'+
        ''.join('\t'.join(r[k] for k in ('driver','category','net'))+'\n' for r in unexpected))
    return metrics, audit, sum(r[1] == 'Internal' for r in rows), not partial and not unexpected


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    require(sys.version_info >= (3, 11), 'Python 3.11 or later required.')
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Native Apple Silicon macOS required.')
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()).resolve()
    results = root/'native/results'
    source = args.source.resolve()
    require(source.parent == results and source.name.startswith('cp0c-postroute-audit-'), 'Use the completed postroute-audit directory.')
    require((source/'exit-status.txt').read_text().strip() == '0', 'Source audit did not complete.')
    require((source/'preservation.txt').read_text().strip() == 'All recorded source/tool/evidence hashes still match.', 'Source preservation not confirmed.')
    odb, sdc = source/'input/routed.odb', source/'input/15pf.sdc'
    require(sha256(odb) == ODB_SHA and sha256(sdc) == SDC_SHA, 'ODB/SDC differs from the reviewed physical candidate.')
    baseline = {}
    for mode, partial in (('saved_spef', 7657), ('fresh_guides', 1133)):
        baseline[mode] = parse_metrics(source/mode/'reports/final.tsv')
        require(parse_metrics(source/mode/'reports/audit.tsv')['partially_unannotated_drivers'] == partial,
                'Use the reviewed completed v1.1 audit.')
    require(shutil.disk_usage(results).free >= 4*1024**3, 'At least 4 GiB free space required.')
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
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=routed-lef-rc\n')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run = Path(tempfile.mkdtemp(prefix=f'cp0c-routed-rc-{stamp}-', dir=results))
        print(f'Evidence: {run}\nVersion: 1.0-routed-lef-rc\nTwo fresh native processes, 2 threads.\n'
              'Approximate LEF RC from actual wires; no physical repair or rerouting.', flush=True)
        entries = []
        for manifest in (source/'source.sha256', source/'input.sha256', source/'fresh_guides/output.sha256', toolbase/'tool.sha256'):
            entries.extend(verify_manifest(manifest))
        require({tool.resolve(), odb, sdc, source/'input/targets.tsv'} <= {Path(p).resolve() for _,p in entries}, 'Required input not hash-covered.')
        entries.extend((sha256(p), str(p)) for p in source.rglob('*') if p.is_file())
        preserved = run/'source.sha256'
        preserved.write_text(''.join(f'{h}  {p}\n' for h,p in dict.fromkeys(entries)))
        inputs = run/'input'
        inputs.mkdir()
        for name in ('routed.odb', '15pf.sdc', 'targets.tsv'):
            shutil.copy2(source/'input'/name, inputs/name)
        (run/'input.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in sorted(inputs.iterdir())))
        helpers = run/'helpers'
        helpers.mkdir()
        here = Path(__file__).resolve().parent
        for name in ('cp0c-routed-rc.py', 'cp0c-routed-rc.tcl', 'cp0c_rc_common.py', 'cp0c_pg_audit.tcl',
                     'cp0c_route_metrics.tcl', 'cp0c_erc_support.py', 'cp0c_postroute_annotation.tcl', 'cp0c_annotation_sections.tcl'):
            shutil.copy2(here/name, helpers/name)
        reference = run/'reference'
        reference.mkdir()
        for mode in ('saved_spef','fresh_guides'):
            shutil.copy2(source/mode/'reports/final.tsv', reference/f'{mode}.tsv')
        shutil.copy2(source/'fresh_guides/reports/unannotated.tsv', reference/'known-unannotated.tsv')
        (run/'provenance.txt').write_text(f'Source: {source}\nOpenROAD pin: {PIN}\n'
            'OpenRCX v2 -lef_rc, actual routed wires, original five-metal LEF RC.\n'
            'Same LEF interconnect model for TT/FF cell libraries; no RC process corners or coupling characterization.\n'
            'No old SPEF, guide estimate, rerouting, buffer insertion, constraint change or physical DB write.\n'
            'OpenRCX may reorder wires in its disposable in-memory database. Source ODB is preserved.\n'
            'Only replay reads the generated SPEF, into a fresh process with the original ODB/SDC.\n')
        spef = run/'extract/out/routed-lef.spef'
        warnings = []
        for mode in ('extract','replay'):
            case = run/mode
            case.mkdir()
            for folder in ('scripts','src'):
                shutil.copytree(source/'fresh_guides'/folder, case/folder)
            env = os.environ.copy()
            env.update(CP0R_ROOT=str(root), CP0R_WORK=str(case), CP0R_TOOL=str(tool), CP0R_HELPERS=str(helpers),
                       CP0R_SCRIPT=str(helpers/'cp0c-routed-rc.tcl'), CP0R_MODE=mode, CP0R_ODB=str(inputs/'routed.odb'),
                       CP0R_SDC=str(inputs/'15pf.sdc'), CP0R_SPEF=str(spef), CP0P_TARGETS=str(inputs/'targets.tsv'),
                       CP0R_LOG=str(case/'openroad.log'))
            shell = r'''set -euo pipefail
cd "$CP0R_ROOT"
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
cd "$CP0R_WORK"
exec "$CP0R_TOOL" -no_init -exit "$CP0R_SCRIPT" -log "$CP0R_LOG"
'''
            print(f'\nRunning {mode}', flush=True)
            rc = stream(['/bin/bash','-c',shell], root, env, case/'console.log')
            (case/'exit-status.txt').write_text(str(rc)+'\n')
            outputs = sorted(p for p in (case/'out').glob('*') if p.is_file())
            if outputs:
                (case/'output.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in outputs))
            require(rc == 0, f'{mode} stopped with OpenROAD exit {rc}; upload this evidence.')
            log = (case/'console.log').read_text()
            require(f'CP0C_ROUTED_RC_COMPLETE {mode}' in log.splitlines(), 'Missing phase completion marker.')
            warnings.extend(f'{mode}: {line}' for line in log.splitlines() if re.search(r'\[(WARNING|ERROR)\b', line))
            require(parse_metrics(case/'reports/physical.tsv') == {'antenna_violating_nets':0,
                    'supply_assignment_findings':0, 'design_has_routing':1, 'physical_signature_unchanged':1}, 'Physical audit differs.')
            pg = parse_metrics(case/'reports/final-pg-counts.tsv')
            require(pg['findings'] == 0 and pg['target_pins'] == 170, 'Supply assignment coverage differs.')
            units = (case/'reports/units.rpt').read_text()
            require(re.search(r'\btime\s+1ns\b',units) and re.search(r'\bcapacitance\s+1pF\b',units), 'Unexpected timing units.')
            if mode == 'extract':
                counts, units = spef_stats(spef)
                (case/'reports/spef-stats.tsv').write_text(''.join(f'{k}\t{v}\n' for k,v in sorted(counts.items()))+
                                                         ''.join(f'{k}\t{v}\n' for k,v in sorted(units.items())))
        metrics, audit, internal, covered = inspect_replay(run/'replay', reference/'known-unannotated.tsv')
        model_covered = covered and not warnings
        internal_clean = model_covered and internal == 0 and metrics['setup_violations'] == metrics['hold_violations'] == 0 and audit['check_setup_clean'] == 1
        (run/'warnings.txt').write_text('\n'.join(warnings)+'\n')
        lines = ['CP0C ROUTED LEF RC DIAGNOSTIC COMPLETE', 'CP0C remains pending.', '',
                 f'Coverage and warning checks clean: {model_covered}',
                 f'Internal timing/ERC clean under this approximate model: {internal_clean}',
                 f'Warnings requiring review: {len(warnings)}', f'Internal ERC findings: {internal}',
                 f'Fully unannotated drivers: {int(audit["unannotated_drivers"])}',
                 f'Partially unannotated drivers: {int(audit["partially_unannotated_drivers"])}', '',
                 '| Metric | Original saved SPEF | Reloaded guides | Actual wires / LEF RC |', '|---|---:|---:|---:|']
        lines += [f'| {k} | {baseline["saved_spef"][k]:.9g} | {baseline["fresh_guides"][k]:.9g} | {metrics[k]:.9g} |'
                  for k in (*COUNTS,*SLACKS,'stdcell_area_um2')]
        lines += ['', 'Annotation coverage is not physical connectivity signoff. Known pad/no-load exceptions remain under review.',
                  'LEF RC is an approximate single interconnect model, without calibrated coupling or process corners.',
                  'I/O constraints remain 15 pF with dynamic enables. No timing repair was performed.',
                  'Source/tool/evidence hashes are checked again before returning. No CP0C PASS stamp is written.']
        (run/'summary.md').write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines),flush=True)
        verify_manifest(run/'input.sha256')
        verify_manifest(run/'extract/output.sha256')
        status = 0
    except (Exception, KeyboardInterrupt) as exc:
        if run:
            (run/'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        print(f'STOPPED: {type(exc).__name__}: {exc}',file=sys.stderr,flush=True)
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
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                (run/'exit-status.txt').write_text(str(status)+'\n')
                archive = run.with_suffix('.tar.gz')
                with tarfile.open(archive,'w:gz') as tar:
                    for p in sorted(run.rglob('*')):
                        rel = p.relative_to(run)
                        if p.is_file() and rel.parts[0] != 'input' and 'out' not in rel.parts:
                            tar.add(p,arcname=str(rel),recursive=False)
                print(f'Upload: {archive}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True)
            lock.rmdir()
    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr)
        sys.exit(1)
