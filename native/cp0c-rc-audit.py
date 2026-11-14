#!/usr/bin/env python3
"""Audit unannotated drivers from a successful CP0C postcheck, without routing.

python3 native/cp0c-rc-audit.py native/results/cp0c-postcheck-...
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
COUNTS = ('setup_violations', 'hold_violations', 'max_slew_violations',
          'max_cap_violations', 'max_fanout_violations')
SLACKS = ('setup_tt', 'setup_ff', 'hold_tt', 'hold_ff')


def annotation(path):
    text = path.read_text()
    counts = re.findall(r'^Found (\d+) (unannotated|partially unannotated) drivers\.$', text, re.M)
    require(len(counts) == 2, 'Unexpected parasitic annotation report format.')
    count = {kind: int(number) for number, kind in counts}
    require(count.get('partially unannotated') == 0, 'Partial annotation requires separate investigation.')
    names = [line.strip() for line in text.splitlines() if line.startswith(' ')]
    require(len(names) == count.get('unannotated') and len(set(names)) == len(names), 'Annotation names/count mismatch.')
    require(names, 'No unannotated drivers to audit.')
    return names


def summarize(run, names):
    current = annotation(run/'reports/parasitic-annotation.rpt')
    require(set(current) == set(names), 'Reloaded annotation coverage differs from the postcheck.')
    before = parse_metrics(run/'reference/metrics.tsv')
    after = parse_metrics(run/'reports/metrics.tsv')
    require(all(before[k] == after[k] for k in COUNTS), 'Reloaded violation counts changed.')
    require(all(abs(before[k]-after[k]) <= 0.005 for k in SLACKS), 'Reloaded slack changed by more than 5 ps.')
    with (run/'reports/unannotated.tsv').open() as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    require(len(rows) == len(names) and {r['driver'] for r in rows} == set(names), 'Incomplete driver inventory.')
    counts = collections.Counter(r['category'] for r in rows)
    allowed = {'NO_NET', 'NO_OTHER_LOADS', 'LOADED_PAD_NET', 'LOADED_INTERNAL_OR_OTHER'}
    require(not (set(counts)-allowed), 'Unknown driver category.')
    lines = ['CP0C RC AUDIT COMPLETE — acceptance remains pending.', '',
             'Saved postcheck timing and annotation counts reproduced.', '',
             '| Driver category | Count |', '|---|---:|']
    for key in sorted(allowed): lines.append(f'| {key} | {counts[key]} |')
    pad_nets = {r['net'] for r in rows if r['category'] == 'LOADED_PAD_NET'}
    other_nets = {r['net'] for r in rows if r['category'] == 'LOADED_INTERNAL_OR_OTHER'}
    lines += ['', f'Unique loaded pad nets: {len(pad_nets)}.',
              f'Unique loaded internal/other nets needing investigation: {len(other_nets)}.',
              'NO_NET and NO_OTHER_LOADS explain absence of interconnect RC; they do not establish that unused logic is intentional.',
              'Loaded pad nets remain RC coverage gaps. Existing wire shapes do not prove their resistance/capacitance was modeled.',
              'This read-only audit does not run routing, extract RC, alter constraints, or grant CP0C PASS.']
    (run/'summary.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines), flush=True)


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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    require(sys.version_info >= (3, 11), 'Python 3.11 or later required.')
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Native Apple Silicon macOS required.')
    root = Path(subprocess.check_output(['git','rev-parse','--show-toplevel'], text=True).strip()).resolve()
    results = root/'native/results'; source = args.source.resolve()
    require(source.parent == results and source.name.startswith('cp0c-postcheck-'), 'Source must be a postcheck under native/results.')
    require((source/'exit-status.txt').read_text().strip() == '0', 'Source postcheck did not complete.')
    toolbase = Path.home()/'tools'/f'croc-openroad-{PIN}'
    tool = toolbase/'install/bin/openroad'
    lock = results/'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError: raise RuntimeError(f'Another CP0C command may be running. Inspect {lock}/owner.txt.') from None
    run = None; preserved = None; status = 1
    try:
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=rc-audit\n')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run = Path(tempfile.mkdtemp(prefix=f'cp0c-rc-audit-{stamp}-', dir=results))
        print(f'RC audit: {run}\nVersion: 1.1-object-index', flush=True)
        entries = []
        for manifest in (source/'source.sha256', source/'input.sha256',
                         source/'dynamic_15pf/output.sha256', toolbase/'tool.sha256'):
            entries.extend(verify_manifest(manifest))
        odb = source/'dynamic_15pf/out/pg_fixed.odb'
        required = {tool.resolve(), odb.resolve(), (source/'input/3pf.sdc').resolve()}
        # Explicit set update keeps SPEF source coverage visible.
        required.update((source/f'input/estimated_{c}').resolve() for c in ('tt','ff'))
        covered = {Path(p).resolve() for _,p in entries}
        require(required <= covered, 'Required physical inputs are not covered by source manifests.')
        entries.extend((sha256(p),str(p)) for p in source.rglob('*') if p.is_file())
        preserved = run/'source.sha256'
        preserved.write_text(''.join(f'{h}  {p}\n' for h,p in dict.fromkeys(entries)))
        for folder in ('scripts','src'): shutil.copytree(source/'dynamic_3pf'/folder,run/folder)
        helpers = run/'helpers'; helpers.mkdir()
        here = Path(__file__).resolve().parent
        for name in ('cp0c-rc-audit.py','cp0c-rc-audit.tcl','cp0c_erc_support.py'): shutil.copy2(here/name, helpers/name)
        ref = run/'reference'; ref.mkdir()
        shutil.copy2(source/'dynamic_3pf/reports/metrics.tsv',ref/'metrics.tsv')
        shutil.copy2(source/'dynamic_3pf/reports/parasitic-annotation.rpt',ref/'annotation.rpt')
        names = annotation(ref/'annotation.rpt')
        (ref/'drivers.txt').write_text('\n'.join(names)+'\n')
        (run/'provenance.txt').write_text(f'Source: {source}\nODB: {odb}\nOpenROAD pin: {PIN}\n'
            'Read-only audit; saved global-route SPEF; dynamic GPIO; saved 3 pF SDC.\n')
        env = os.environ.copy()
        env.update(CP0R_ROOT=str(root), CP0R_WORK=str(run), CP0R_TOOL=str(tool),
            CP0R_SCRIPT=str(helpers/'cp0c-rc-audit.tcl'), CP0R_ODB=str(odb),
            CP0R_SDC=str(source/'input/3pf.sdc'), CP0R_SPEF_DIR=str(source/'input'),
            CP0R_DRIVERS=str(ref/'drivers.txt'), CP0R_LOG=str(run/'openroad.log'))
        shell = r'''set -euo pipefail
cd "$CP0R_ROOT"
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
cd "$CP0R_WORK"
exec "$CP0R_TOOL" -no_init -exit "$CP0R_SCRIPT" -log "$CP0R_LOG"
'''
        rc = stream(['/bin/bash','-c',shell],root,env,run/'console.log')
        require(rc == 0 and 'CP0C_RC_AUDIT_COMPLETE' in (run/'console.log').read_text().splitlines(), 'OpenROAD audit did not complete.')
        units = (run/'reports/units.rpt').read_text()
        require(re.search(r'\btime\s+1ns\b',units) and re.search(r'\bcapacitance\s+1pF\b',units), 'Unexpected units.')
        summarize(run,names); status = 0
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
            if run:
                (run/'exit-status.txt').write_text(str(status)+'\n')
                archive = run.with_suffix('.tar.gz')
                with tarfile.open(archive,'w:gz') as tar:
                    for p in sorted(run.rglob('*')):
                        if p.is_file(): tar.add(p,arcname=str(p.relative_to(run)),recursive=False)
                print(f'Upload: {archive}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True); lock.rmdir()
    return status


if __name__ == '__main__':
    try: sys.exit(main())
    except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr); sys.exit(1)
