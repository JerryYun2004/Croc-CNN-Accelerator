#!/usr/bin/env python3
"""Collect exact RC-graph evidence and prepare the remaining CP0C repairs.

python3 native/cp0c-closure-trace.py ROUTED_RC_DIRECTORY
Reads existing ODB/SDC/SPEF. No routing, extraction or physical edits.
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

from cp0c_erc_support import require, sha256, verify_manifest, parse_metrics
from cp0c_rc_common import classify, stream
from cp0c_spef_trace import rows, table, analyze, repair_plan

PIN = 'c463e998615210dc322c999fc9a9f56885dbd664'
ODB_SHA = '6968ce394b95df5611f9dbb22d126bec7ecefae637e08c204d3cf628dc831c06'
SDC_SHA = 'f0216b26bf10e67bee572a0c8058413f6f0cbfb2c9d167368fb17bc92efe202c'
SPEF_SHA = 'ef4e50b73a9afd09cd4258b7b4c5a4d0da8a4cb97bf3eb517c14ee3039e7d65b'
COUNTS = ('setup_violations','hold_violations','max_slew_violations','max_cap_violations','max_fanout_violations')
SLACKS = ('setup_tt','setup_ff','hold_tt','hold_ff')


def prepare_targets(source, inputs):
    missing = rows(source/'replay/reports/partially-unannotated-loads.tsv')
    require(len(missing)==196 and len({r['driver'] for r in missing})==76, 'Expected 76 drivers / 196 missing loads.')
    require(len({(r['driver'],r['missing_load']) for r in missing})==196, 'Duplicate missing-load pair.')
    targets = [dict(kind='partial',driver=r['driver'],load=r['missing_load']) for r in missing]
    classified = classify(source/'replay/reports/final-erc.rpt')
    internal = [r for r in classified if r[1]=='Internal']
    require(collections.Counter(r[0] for r in internal)=={'max slew':5,'max fanout':3}, 'Unexpected internal ERC targets.')
    for row in internal:
        if row[0]=='max slew':
            require('gen_sram_bank[1]' in row[2] and '/A_' in row[2], 'Unexpected slew target.')
            targets.append(dict(kind='slew',driver='',load=row[2]))
        else:
            targets.append(dict(kind='fanout',driver=row[2],load=''))
    table(inputs/'targets.tsv',['kind','driver','load'],targets)
    table(inputs/'classified.tsv',['check','category','pin','limit','actual','reported_slack'],
          [dict(zip(['check','category','pin','limit','actual','reported_slack'],r)) for r in classified])
    return missing


def io_report(classified, destination):
    io = [r for r in rows(classified) if r['category']=='I/O']
    table(destination/'io-findings.tsv',list(io[0]),io)
    counts = collections.Counter(r['check'] for r in io)
    require(counts=={'max slew':71,'max capacitance':71},'Unexpected I/O findings.')
    (destination/'io-contract.md').write_text('''# I/O closure decision — unchanged requirement

Current constraint: 15 pF on outputs, with dynamic GPIO enables.
The original Croc constraint explains this as two 5 pF receiver inputs plus a
5 pF trace. This is a design assumption, not a measured board load.

The current evidence has 71 I/O slew and 71 I/O capacitance findings.
`io-findings.tsv` lists every pin, its limit and measured/modelled value.
These include multiple reported endpoints per physical pad, so these counts
must not be interpreted as 142 distinct faulty pads.

For example, the current output-pad model reports approximately 15 pF against
about 4.21 pF maximum capacitance, and approximately 3.55 ns slew against a
1.2 ns limit. Internal buffering cannot remove the external pad load.

Choose a physical interface requirement before changing constraints:

1. Retain 15 pF: verify a suitable pad/library operating point or redesign the
   interface so each chip pad sees a supported load. This requires circuit or
   board/interface work; it is not a max-capacitance waiver.
2. Use a lower-load interface: document actual receiver, package and PCB loads,
   then revise the load constraint with margin and rerun all applicable GPIO
   input/output mode checks. An earlier 3 pF experiment was diagnostic only.

This package retains 15 pF. It does not apply a lower load, freeze GPIO enables,
change clock periods, waive library limits, or claim I/O closure.
''')


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source',type=Path)
    args=parser.parse_args()
    require(sys.version_info >= (3,11),'Python 3.11+ required.')
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Native Apple Silicon macOS required.')
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results'; source=args.source.resolve()
    require(source.parent==results and source.name.startswith('cp0c-routed-rc-'),'Use the completed routed-RC directory.')
    require((source/'exit-status.txt').read_text().strip()=='0','Source diagnostic did not complete.')
    require((source/'preservation.txt').read_text().strip()=='All recorded source/tool/evidence hashes still match.','Missing preservation evidence.')
    odb=source/'input/routed.odb'; sdc=source/'input/15pf.sdc'; spef=source/'extract/out/routed-lef.spef'
    require(sha256(odb)==ODB_SHA and sha256(sdc)==SDC_SHA and sha256(spef)==SPEF_SHA,'Reviewed ODB, SDC or SPEF differs.')
    baseline=parse_metrics(source/'replay/reports/final.tsv')
    require([baseline[k] for k in COUNTS]==[0,0,76,71,3],'Source timing/ERC differs.')
    require(shutil.disk_usage(results).free>=2*1024**3,'At least 2 GiB free space required.')
    toolbase=Path.home()/'tools'/f'croc-openroad-{PIN}'; tool=toolbase/'install/bin/openroad'
    lock=results/'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError: raise RuntimeError(f'Another CP0C command may be running. Inspect {lock}/owner.txt.') from None
    run=preserved=None; status=1
    try:
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=closure-trace\n')
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run=Path(tempfile.mkdtemp(prefix=f'cp0c-closure-trace-{stamp}-',dir=results))
        print(f'Evidence: {run}\nVersion: 1.0-targeted-rc-graph\nReads existing routed RC; no extraction or routing.',flush=True)
        entries=[]
        for manifest in (source/'source.sha256',source/'input.sha256',source/'extract/output.sha256',toolbase/'tool.sha256'):
            entries.extend(verify_manifest(manifest))
        require({tool.resolve(),odb,sdc,spef,source/'input/targets.tsv'} <= {Path(p).resolve() for _,p in entries},'Required input not hash-covered.')
        entries.extend((sha256(p),str(p)) for p in source.rglob('*') if p.is_file())
        preserved=run/'source.sha256'
        preserved.write_text(''.join(f'{h}  {p}\n' for h,p in dict.fromkeys(entries)))
        inputs=run/'input'; inputs.mkdir()
        shutil.copy2(odb,inputs/'routed.odb'); shutil.copy2(sdc,inputs/'15pf.sdc')
        shutil.copy2(source/'input/targets.tsv',inputs/'pg-targets.tsv')
        missing=prepare_targets(source,inputs)
        (run/'input.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in sorted(inputs.iterdir())))
        helpers=run/'helpers'; helpers.mkdir(); here=Path(__file__).resolve().parent
        for name in ('cp0c-closure-trace.py','cp0c-closure-trace.tcl','cp0c_spef_trace.py','cp0c_pg_audit.tcl',
                     'cp0c_route_metrics.tcl','cp0c_erc_support.py','cp0c_rc_common.py','cp0c_annotation_sections.tcl'):
            shutil.copy2(here/name,helpers/name)
        reference=run/'reference'; reference.mkdir()
        for folder in ('scripts','src'): shutil.copytree(source/'replay'/folder,run/folder)
        shutil.copy2(inputs/'classified.tsv',reference/'classified.tsv')
        shutil.copy2(inputs/'targets.tsv',reference/'targets.tsv')
        shutil.copy2(source/'replay/reports/final.tsv',reference/'final.tsv')
        shutil.copy2(source/'replay/reports/partially-unannotated-loads.tsv',reference/'missing-loads.tsv')
        (run/'provenance.txt').write_text(f'Source: {source}\nOpenROAD pin: {PIN}\nSPEF: {spef}\nSPEF SHA256: {SPEF_SHA}\n'
            'Reads original preserved ODB/SDC and existing routed-wire LEF SPEF. No physical edits or re-extraction.\n'
            'Wire junction IDs and connected/ordered flags are database metadata, not independent geometric connectivity signoff.\n'
            'All proposed repairs remain blocked until RC coverage is resolved. I/O remains 15 pF / dynamic enables.\n')
        env=os.environ.copy()
        env.update(CP0T_ROOT=str(root),CP0T_WORK=str(run),CP0T_TOOL=str(tool),CP0T_HELPERS=str(helpers),
                   CP0T_SCRIPT=str(helpers/'cp0c-closure-trace.tcl'),CP0T_ODB=str(inputs/'routed.odb'),CP0T_SDC=str(inputs/'15pf.sdc'),
                   CP0T_SPEF=str(spef),CP0T_INPUT=str(inputs),CP0P_TARGETS=str(inputs/'pg-targets.tsv'),CP0T_LOG=str(run/'openroad.log'))
        shell=r'''set -euo pipefail
cd "$CP0T_ROOT"
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
cd "$CP0T_WORK"
exec "$CP0T_TOOL" -no_init -exit "$CP0T_SCRIPT" -log "$CP0T_LOG"
'''
        rc=stream(['/bin/bash','-c',shell],root,env,run/'console.log')
        (run/'openroad-exit-status.txt').write_text(str(rc)+'\n')
        require(rc==0,f'OpenROAD stopped with exit {rc}; upload the evidence.')
        log=(run/'console.log').read_text()
        require('CP0C_CLOSURE_TRACE_COMPLETE' in log.splitlines(),'Missing native completion marker.')
        units=(run/'reports/units.rpt').read_text()
        require(re.search(r'\btime\s+1ns\b',units) and re.search(r'\bcapacitance\s+1pF\b',units),'Unexpected units.')
        current=parse_metrics(run/'reports/final.tsv')
        require(all(current[k]==baseline[k] for k in COUNTS),'Control ERC counts did not reproduce.')
        require(all(abs(current[k]-baseline[k])<=.005 for k in SLACKS),'Control timing differs by more than 5 ps.')
        require(parse_metrics(run/'reports/annotation-counts.tsv')=={'full':575,'partial':76,'loads':196},'Annotation counts changed.')
        observed=rows(run/'reports/observed-partial.tsv')
        require({(r['driver'],r['missing_load']) for r in observed}=={(r['driver'],r['missing_load']) for r in missing},'Missing-load set changed.')
        require(parse_metrics(run/'reports/physical.tsv')=={'physical_signature_unchanged':1,'antenna_violating_nets':0,'supply_assignment_findings':0},'Physical audit differs.')
        pg=parse_metrics(run/'reports/final-pg-counts.tsv')
        require(pg['findings']==0 and pg['target_pins']==170,'Supply coverage differs.')
        counts=analyze(spef,run/'reports')
        require(sum(counts.values())==196,'Graph diagnosis did not cover all 196 loads.')
        plans=repair_plan(run/'reports',inputs/'classified.tsv')
        io_report(inputs/'classified.tsv',run/'reports')
        warnings=[line for line in log.splitlines() if re.search(r'\[(WARNING|ERROR)\b',line)]
        (run/'warnings.txt').write_text('\n'.join(warnings)+'\n')
        lines=['CP0C TARGETED CLOSURE TRACE COMPLETE','CP0C_PENDING — diagnostic and repair preparation only.','',
               'Native control reproduces the 76 partially annotated drivers / 196 missing loads.',
               '| Missing-load classification | Count |','|---|---:|']
        lines += [f'| {k} | {v} |' for k,v in sorted(counts.items())]
        lines += ['',f'Warnings requiring review: {len(warnings)}',f'Prepared internal repair targets: {len(plans)}',
                  'Exact original SPEF header/name map and selected net blocks: reports/selected-nets.spef',
                  'Per-load evidence: reports/missing-load-diagnosis.tsv',
                  'Physical pin metadata: reports/net-pins.tsv and nets.tsv',
                  'Repair proposals: reports/repair-plan.tsv (BLOCKED_RC_COVERAGE; not applied)',
                  'I/O requirement decision: reports/io-contract.md; all existing findings: io-findings.tsv','',
                  'Absent SPEF pins with a positive original wire junction suggest an extraction/serialization discrepancy.',
                  'A disconnected SPEF branch requires comparison with original wire geometry before declaring a physical open.',
                  'Pins reachable in SPEF need STA naming/import investigation. Flags and junction IDs alone are not geometric proof.',
                  'No design PASS, constraint relaxation, or hardware edit is performed.']
        (run/'summary.md').write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines),flush=True)
        verify_manifest(run/'input.sha256')
        status=0
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
                    status=1
                    with (run/'failure.txt').open('a') as f: f.write(f'Preservation failed: {exc}\n')
            if run:
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                (run/'exit-status.txt').write_text(str(status)+'\n')
                archive=run.with_suffix('.tar.gz')
                with tarfile.open(archive,'w:gz') as tar:
                    for p in sorted(run.rglob('*')):
                        rel=p.relative_to(run)
                        if p.is_file() and rel.parts[0] not in ('input','out','save'):
                            tar.add(p,arcname=str(rel),recursive=False)
                print(f'Upload: {archive}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True); lock.rmdir()
    return status


if __name__=='__main__':
    try: sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr); sys.exit(1)
