#!/usr/bin/env python3
"""Continue the PG-fixed Croc candidate through detailed routing on native macOS.

python3 native/cp0c-route-repair.py POSTCHECK_DIRECTORY --audit RC_AUDIT_DIRECTORY
Produces a separate candidate. Execution success does not grant CP0C acceptance.
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

from cp0c_erc_support import require, sha256, verify_manifest, parse_metrics, classify_report

PIN = 'c463e998615210dc322c999fc9a9f56885dbd664'
COUNTS = ('setup_violations','hold_violations','max_slew_violations','max_cap_violations','max_fanout_violations')
SLACKS = ('setup_tt','setup_ff','hold_tt','hold_ff')


def stream(command, cwd, env, logfile):
    with logfile.open('w') as f:
        p = subprocess.Popen(command,cwd=cwd,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        try:
            for line in p.stdout:
                print(line,end='',flush=True); f.write(line); f.flush()
            return p.wait()
        except BaseException:
            p.terminate()
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
            raise


def validate_audit(audit, source):
    require((audit/'exit-status.txt').read_text().strip() == '0', 'RC audit did not complete.')
    match = re.search(r'^Source: (.+)$',(audit/'provenance.txt').read_text(),re.M)
    require(match and Path(match.group(1)).resolve() == source, 'Audit belongs to a different postcheck.')
    with (audit/'reports/unannotated.tsv').open() as f: rows = list(csv.DictReader(f,delimiter='\t'))
    require(len(rows) == 575 and len({r['driver'] for r in rows}) == 575, 'Unexpected RC audit coverage.')
    require(collections.Counter(r['category'] for r in rows) ==
            {'NO_OTHER_LOADS':475,'NO_NET':20,'LOADED_PAD_NET':80}, 'RC audit categories changed.')
    require(len({r['net'] for r in rows if r['category']=='LOADED_PAD_NET'}) == 48, 'Unexpected pad-net count.')


def check_stage(case, stage, reference=None):
    log = (case/'console.log').read_text()
    require(f'CP0C_REPAIRED_STAGE_COMPLETE {stage}' in log.splitlines(), 'Missing stage-completion marker.')
    require('EST-0026' not in log, 'Parasitic estimation reported missing routes to pins.')
    units = (case/'reports/units.rpt').read_text()
    require(re.search(r'\btime\s+1ns\b',units) and re.search(r'\bcapacitance\s+1pF\b',units), 'Unexpected units.')
    prefix = 'preflight' if stage == 'preflight' else 'final'
    metrics = parse_metrics(case/f'reports/{prefix}.tsv')
    rows = classify_report(case/f'reports/{prefix}-erc.rpt')
    require(all(r[1] == 'I/O' for r in rows), 'Internal ERC findings remain.')
    for kind,key in (('max slew','max_slew_violations'),('max capacitance','max_cap_violations'),('max fanout','max_fanout_violations')):
        require(sum(r[0] == kind for r in rows) == metrics[key], 'ERC report/count mismatch.')
    require(metrics['setup_violations'] == 0 and metrics['hold_violations'] == 0, 'Setup/hold findings remain.')
    (case/'reports/classified.tsv').write_text('check\tcategory\tpin\tlimit\tactual\tslack\n'+''.join('\t'.join(r)+'\n' for r in rows))
    if reference:
        expected = parse_metrics(reference)
        require(all(metrics[k] == expected[k] for k in COUNTS), 'Preflight counts do not reproduce the saved postcheck.')
        require(all(abs(metrics[k]-expected[k]) <= .005 for k in SLACKS), 'Preflight slack differs by more than 5 ps.')
    if stage == 'route':
        physical = parse_metrics(case/'reports/physical.tsv')
        require(all(physical[k] == 0 for k in ('router_drvs','antenna_violating_nets','supply_assignment_findings')), 'Physical findings remain.')
        for name in ('croc_repaired.def','croc_repaired.odb','croc_repaired.sdc','croc_repaired.v','croc_repaired_lvs.v'):
            require((case/'out'/name).stat().st_size > 0, f'Missing output: {name}')
        for corner in ('tt','ff'):
            p = case/f'out/estimated_{corner}'
            with p.open('rb') as f: header = f.read(4096)
            require(b'*SPEF' in header, f'Invalid saved estimated SPEF: {p}')
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source',type=Path)
    parser.add_argument('--audit',type=Path,required=True)
    parser.add_argument('--preflight-only',action='store_true')
    args = parser.parse_args()
    require(sys.version_info >= (3,11), 'Python 3.11 or later required.')
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Native Apple Silicon macOS required.')
    root = Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results = root/'native/results'; source = args.source.resolve(); audit = args.audit.resolve()
    require(source.parent == results and source.name.startswith('cp0c-postcheck-'), 'Source must be a postcheck under native/results.')
    require(audit.parent == results and audit.name.startswith('cp0c-rc-audit-'), 'Audit must be under native/results.')
    require((source/'exit-status.txt').read_text().strip() == '0', 'Postcheck did not complete.')
    validate_audit(audit,source)
    require(shutil.disk_usage(results).free >= 8*1024**3, 'At least 8 GiB free space required for the continuation.')
    toolbase = Path.home()/'tools'/f'croc-openroad-{PIN}'; tool = toolbase/'install/bin/openroad'
    lock = results/'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError: raise RuntimeError(f'Another CP0C command may be running. Inspect {lock}/owner.txt.') from None
    run = None; preserved = None; status = 1
    try:
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=repaired-detailed-route\n')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run = Path(tempfile.mkdtemp(prefix=f'cp0c-route-repair-{stamp}-',dir=results))
        print(f'Candidate: {run}\nVersion: 1.1-timing-initialization\nThreads: 2',flush=True)
        entries = []
        for manifest in (source/'source.sha256',source/'input.sha256',source/'dynamic_15pf/output.sha256',
                         audit/'source.sha256',toolbase/'tool.sha256'):
            entries.extend(verify_manifest(manifest))
        odb = source/'dynamic_15pf/out/pg_fixed.odb'
        required = {tool.resolve(),odb.resolve(),(source/'input/15pf.sdc').resolve(),(source/'input/targets.tsv').resolve()}
        required.update((source/f'input/estimated_{c}').resolve() for c in ('tt','ff'))
        require(required <= {Path(p).resolve() for _,p in entries}, 'Required inputs are not hash-covered.')
        for folder in (source,audit):
            entries.extend((sha256(p),str(p)) for p in folder.rglob('*') if p.is_file())
        preserved = run/'source.sha256'
        preserved.write_text(''.join(f'{h}  {p}\n' for h,p in dict.fromkeys(entries)))
        inputs = run/'input'; inputs.mkdir()
        shutil.copy2(odb,inputs/'pg_fixed.odb')
        for name in ('15pf.sdc','targets.tsv','estimated_tt','estimated_ff'): shutil.copy2(source/'input'/name,inputs/name)
        (run/'input.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in sorted(inputs.iterdir())))
        helpers = run/'helpers'; helpers.mkdir(); here = Path(__file__).resolve().parent
        for name in ('cp0c-route-repair.py','cp0c-route-repair.tcl','cp0c_pg_audit.tcl','cp0c_route_metrics.tcl','cp0c_erc_support.py'):
            shutil.copy2(here/name,helpers/name)
        ref = run/'reference'; ref.mkdir()
        shutil.copy2(source/'dynamic_15pf/reports/metrics.tsv',ref/'15pf.tsv')
        shutil.copy2(audit/'summary.md',ref/'rc-audit-summary.md')
        (run/'provenance.txt').write_text(f'Source: {source}\nRC audit: {audit}\nOpenROAD pin: {PIN}\nThreads: 2\n'
            'Original 15 pF diagnostic SDC; dynamic GPIO enables; no constraint relaxation.\n'
            'Fresh global route, antenna repair, detailed route, fillers. No timing/design optimization.\n'
            'Timing RC remains global-route estimates, not extracted detailed-route RC.\n')
        stages = ['preflight'] if args.preflight_only else ['preflight','route']
        data = {}
        for stage in stages:
            case = run/stage; case.mkdir()
            for folder in ('scripts','src'): shutil.copytree(source/'dynamic_15pf'/folder,case/folder)
            env = os.environ.copy()
            env.update(CP0D_ROOT=str(root),CP0D_WORK=str(case),CP0D_TOOL=str(tool),CP0D_HELPERS=str(helpers),
                CP0D_SCRIPT=str(helpers/'cp0c-route-repair.tcl'),CP0D_STAGE=stage,CP0D_ODB=str(inputs/'pg_fixed.odb'),
                CP0D_SDC=str(inputs/'15pf.sdc'),CP0D_SPEF_DIR=str(inputs),CP0P_TARGETS=str(inputs/'targets.tsv'),CP0D_LOG=str(case/'openroad.log'))
            shell = r'''set -euo pipefail
cd "$CP0D_ROOT"
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
cd "$CP0D_WORK"
exec "$CP0D_TOOL" -no_init -exit "$CP0D_SCRIPT" -log "$CP0D_LOG"
'''
            print(f'\nRunning {stage}',flush=True)
            rc = stream(['/bin/bash','-c',shell],root,env,case/'console.log')
            (case/'exit-status.txt').write_text(str(rc)+'\n')
            outputs = [p for p in (case/'out').glob('*') if p.is_file()]
            if outputs: (case/'output.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in sorted(outputs)))
            require(rc == 0,f'{stage} stopped with OpenROAD exit {rc}; review the saved evidence.')
            data[stage] = check_stage(case,stage,ref/'15pf.tsv' if stage == 'preflight' else None)
        lines = ['CP0C REPAIRED CANDIDATE '+('PREFLIGHT COMPLETE' if args.preflight_only else 'ROUTING COMPLETE'),
                 'CP0C acceptance remains pending; no PASS stamp or current-flow pointer was changed.','',
                 'Constraints: saved 15 pF load and dynamic GPIO enables.',
                 'Timing: global-route RC estimates, not extracted post-route RC.','']
        if 'route' in data:
            lines += ['Detailed-router DRVs: 0. Antenna-violating nets: 0. Supply-assignment findings: 0.',
                      'These checks do not replace full GDS DRC/LVS, power-grid continuity or extracted-RC timing.','',
                      '| Metric | Reloaded repaired input | Routed candidate (estimated RC) |','|---|---:|---:|']
            for key in (*COUNTS,*SLACKS,'stdcell_area_um2'):
                lines.append(f'| {key} | {data["preflight"][key]:.9g} | {data["route"][key]:.9g} |')
            lines += ['', 'Slack units: ns; cell area: square micrometres.',
                      'I/O violations remain visible for interface-contract review. No 3 pF or static-OE assumption was adopted.',
                      'Physical outputs: route/out/croc_repaired.{odb,def,sdc,v} and croc_repaired_lvs.v.']
        (run/'summary.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines),flush=True)
        status = 0
    except (Exception,KeyboardInterrupt) as exc:
        if run: (run/'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        print(f'STOPPED: {type(exc).__name__}: {exc}',file=sys.stderr,flush=True)
    finally:
        try:
            if preserved is not None and preserved.exists():
                try:
                    verify_manifest(preserved)
                    (run/'preservation.txt').write_text('All recorded source/tool/evidence hashes still match.\n')
                except Exception as exc:
                    status = 1
                    with (run/'failure.txt').open('a') as f: f.write(f'Preservation check failed: {exc}\n')
            if run:
                (run/'exit-status.txt').write_text(str(status)+'\n')
                archive = run.with_suffix('.tar.gz')
                with tarfile.open(archive,'w:gz') as tar:
                    for p in sorted(run.rglob('*')):
                        rel = p.relative_to(run)
                        if p.is_file() and rel.parts[0] != 'input' and 'out' not in rel.parts:
                            tar.add(p,arcname=str(rel),recursive=False)
                print(f'Upload: {archive}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return status


if __name__ == '__main__':
    try:sys.exit(main())
    except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(1)
