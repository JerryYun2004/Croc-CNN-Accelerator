#!/usr/bin/env python3
"""Audit/repair buffer supply pins in a copy, then diagnose GPIO enable modes.

python3 native/cp0c-postcheck.py native/results/cp0c-erc-repair-...
No placement/routing. No acceptance stamp or adopted interface constraints.
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

from cp0c_erc_support import require, sha256, verify_manifest, parse_metrics, classify_report

PIN = 'c463e998615210dc322c999fc9a9f56885dbd664'
CASES = (('dynamic_15pf', '15', 'dynamic'), ('dynamic_3pf', '3', 'dynamic'),
         ('gpio_oe0_3pf', '3', '0'), ('gpio_oe1_3pf', '3', '1'))
COUNT_KEYS = ('max_slew_violations', 'max_cap_violations', 'max_fanout_violations',
              'setup_violations', 'hold_violations')
SLACK_KEYS = ('setup_tt', 'setup_ff', 'hold_tt', 'hold_ff')


def stream(command, cwd, env, logfile):
    with logfile.open('w') as f:
        p = subprocess.Popen(command, cwd=cwd, env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for line in p.stdout:
                print(line, end='', flush=True); f.write(line); f.flush()
            return p.wait()
        except BaseException:
            p.terminate()
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
            raise


def check_reports(case, reference=None):
    log = (case / 'console.log').read_text()
    require('CP0C_POSTCHECK_CASE_COMPLETE' in log.splitlines(), 'Missing case completion marker.')
    require('CP0C_POSTCHECK_PG_OK ' in log, 'Missing supply-connectivity check.')
    units = (case / 'reports/units.rpt').read_text()
    require(re.search(r'\btime\s+1ns\b', units) and re.search(r'\bcapacitance\s+1pF\b', units), 'Unexpected units.')
    metrics = parse_metrics(case / 'reports/metrics.tsv')
    rows = classify_report(case / 'reports/erc.rpt')
    counts = collections.Counter((r[0], r[1]) for r in rows)
    for kind, key in zip(('max slew', 'max capacitance', 'max fanout'), COUNT_KEYS[:3]):
        require(sum(n for (k, _), n in counts.items() if k == kind) == metrics[key], 'ERC report/count mismatch.')
    (case / 'reports/classified.tsv').write_text('check\tcategory\tpin\tlimit\tactual\tslack\n' + ''.join('\t'.join(r)+'\n' for r in rows))
    if reference is not None:
        expected = parse_metrics(reference)
        delta = []
        for key in COUNT_KEYS:
            delta.append(f'{key}\t{expected[key]}\t{metrics[key]}')
        for key in SLACK_KEYS:
            delta.append(f'{key}\t{expected[key]}\t{metrics[key]}')
        (case / 'reports/reproduction.tsv').write_text('metric\tsource\treloaded\n'+'\n'.join(delta)+'\n')
        require(all(metrics[k] == expected[k] for k in COUNT_KEYS),
                'Reloaded SPEF check counts differ from the source; inspect reproduction.tsv before interpreting mode trials.')
        require(all(abs(metrics[k]-expected[k]) <= 0.005 for k in SLACK_KEYS),
                'Reloaded SPEF slack differs by more than 5 ps; inspect reproduction.tsv.')
    return metrics


def bundle(run):
    archive = run.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        for p in sorted(run.rglob('*')):
            rel = p.relative_to(run)
            if p.is_file() and rel.parts[0] != 'input' and p.suffix not in ('.odb', '.spef', '.v'):
                tar.add(p, arcname=str(rel), recursive=False)
    print(f'Upload: {archive}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    require(sys.version_info >= (3, 11), 'Python 3.11 or later required.')
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Native Apple Silicon macOS required.')
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()).resolve()
    results = root / 'native/results'; source = args.source.resolve()
    require(source.parent == results and source.name.startswith('cp0c-erc-repair-'), 'Source must be an experiment under native/results.')
    require((source / 'exit-status.txt').read_text().strip() == '0', 'Source experiment did not complete.')
    toolbase = Path.home() / 'tools' / f'croc-openroad-{PIN}'
    tool = toolbase / 'install/bin/openroad'
    lock = results / 'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError: raise RuntimeError(f'Another CP0C command may be running. Inspect {lock}/owner.txt.') from None
    run = None; status = 1; preserved = None
    try:
        (lock / 'owner.txt').write_text(f'PID={os.getpid()}\nstage=postcheck\n')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run = Path(tempfile.mkdtemp(prefix=f'cp0c-postcheck-{stamp}-', dir=results))
        print(f'Postcheck: {run}\nVersion: 1.0-saved-spef-pg', flush=True)
        manifests = [source / 'original-inputs.sha256', toolbase / 'tool.sha256']
        manifests += [source / f'repair_{load}pf/output.sha256' for load in ('15','3')]
        entries = []
        for manifest in manifests: entries.extend(verify_manifest(manifest))
        require(any(Path(p).resolve() == tool.resolve() for _, p in entries), 'Tool binary is not covered by hashes.')
        a = source / 'repair_15pf'; b = source / 'repair_3pf'
        for name in ('trial.odb', 'trial.v', 'estimated_tt', 'estimated_ff'):
            require(sha256(a/'out'/name) == sha256(b/'out'/name), f'Expected identical repaired physical data: {name}')
        def inventory(path):
            # Arguments also contain process-local STA pointer handles.
            return [line.split('\t')[:3] for line in path.read_text().splitlines()]
        require(inventory(a/'reports/inserted-buffers.tsv') == inventory(b/'reports/inserted-buffers.tsv'),
                'Insertion inventories differ; this postcheck requires the same repaired physical design.')
        # Snapshot and later verify all supplied small evidence and configuration.
        extras = [p for p in source.rglob('*') if p.is_file() and p.suffix not in ('.odb','.v')
                  and p.name not in ('estimated_tt','estimated_ff')]
        entries += [(sha256(p), str(p)) for p in extras]
        preserved = run / 'source.sha256'
        preserved.write_text(''.join(f'{h}  {p}\n' for h,p in dict.fromkeys(entries)))
        here = Path(__file__).resolve().parent; helpers = run/'helpers'; helpers.mkdir()
        for name in ('cp0c-postcheck.py','cp0c-postcheck.tcl','cp0c_erc_support.py'): shutil.copy2(here/name, helpers/name)
        inputs = run/'input'; inputs.mkdir(); reference = run/'reference'; reference.mkdir()
        for name in ('trial.odb','estimated_tt','estimated_ff'): shutil.copy2(b/'out'/name,inputs/name)
        shutil.copy2(b/'reports/inserted-buffers.tsv',inputs/'targets.tsv')
        for load in ('15','3'):
            case = source/f'repair_{load}pf'
            shutil.copy2(case/'out/trial.sdc',inputs/f'{load}pf.sdc')
            shutil.copy2(case/'reports/final.tsv',reference/f'{load}pf.tsv')
        (run/'input.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in sorted(inputs.iterdir())))
        match = re.search(r'^Threads: ([1-9][0-9]*)$',(source/'provenance.txt').read_text(),re.M)
        require(match is not None,'Missing source thread count.'); threads = match.group(1)
        (run/'provenance.txt').write_text(f'Source: {source}\nOpenROAD pin: {PIN}\nThreads: {threads}\n'
            'PG-only edits in a copy; signals/placement/protection flags checked unchanged.\n'
            'RC: imported saved global-route SPEF, not extracted detailed-route RC.\n'
            'GPIO OE cases are diagnostic, not adopted constraints.\n')
        data = {}
        for label,load,mode in CASES:
            case = run/label; case.mkdir()
            for folder in ('scripts','src'): shutil.copytree(b/folder,case/folder)
            first = label == 'dynamic_15pf'
            odb = inputs/'trial.odb' if first else run/'dynamic_15pf/out/pg_fixed.odb'
            env = os.environ.copy()
            env.update(CP0P_ROOT=str(root),CP0P_WORK=str(case),CP0P_TOOL=str(tool),CP0P_SCRIPT=str(helpers/'cp0c-postcheck.tcl'),
                CP0P_ODB=str(odb),CP0P_SDC=str(inputs/f'{load}pf.sdc'),CP0P_SPEF_DIR=str(inputs),CP0P_TARGETS=str(inputs/'targets.tsv'),
                CP0P_THREADS=threads,CP0P_FIX_PG='1' if first else '0',CP0P_GPIO_OE=mode,CP0P_LOG=str(case/'openroad.log'))
            print(f'\nRunning {label}',flush=True)
            shell = r'''set -euo pipefail
cd "$CP0P_ROOT"
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
cd "$CP0P_WORK"
exec "$CP0P_TOOL" -no_init -exit "$CP0P_SCRIPT" -log "$CP0P_LOG"
'''
            rc = stream(['/bin/bash','-c',shell],root,env,case/'console.log')
            (case/'exit-status.txt').write_text(str(rc)+'\n')
            require(rc == 0,f'{label} failed with OpenROAD exit {rc}.')
            data[label] = check_reports(case,reference/f'{load}pf.tsv' if mode == 'dynamic' else None)
            outputs = sorted((case/'out').iterdir())
            (case/'output.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in outputs if p.is_file()))
        lines = ['CP0C POSTCHECK COMPLETE — acceptance remains pending.','',
                 'The two dynamic cases reproduce source counts and slacks within 5 ps.',
                 'Supply-pin net assignments pass; physical power-grid continuity is not checked.',
                 'All four cases keep their saved external driving-cell models and loads.',
                 'OE=0/1 cases hold GPIO enables constant only; they do not validate direction switching.','',
                 '| Metric | Dynamic 15 pF | Dynamic 3 pF | GPIO OE=0, 3 pF | GPIO OE=1, 3 pF |',
                 '|---|---:|---:|---:|---:|']
        for key in (*COUNT_KEYS,*SLACK_KEYS):
            lines.append('| '+key+' | '+' | '.join(f'{data[n][key]:.9g}' for n,_,_ in CASES)+' |')
        lines += ['','Slack units: ns. Read GPIO slews, enable paths and parasitic-annotation reports before drawing conclusions.',
                  'No placement, routing, library edit, accepted constraint change or CP0C PASS was performed.']
        (run/'summary.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines),flush=True)
        status = 0
    except (Exception, KeyboardInterrupt) as exc:
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
                    print(f'PRESERVATION CHECK FAILED: {exc}',file=sys.stderr,flush=True)
            if run: (run/'exit-status.txt').write_text(str(status)+'\n'); bundle(run)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return status


if __name__ == '__main__':
    try: sys.exit(main())
    except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(1)
