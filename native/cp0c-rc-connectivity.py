#!/usr/bin/env python3
"""Test the pinned OpenRCX SHORT-mapping workaround and replay timing.

python3 native/cp0c-rc-connectivity.py CLOSURE_TRACE_DIRECTORY
Separate output only. No rebuild, routing, physical ECO or constraint changes.
"""
import argparse
import collections
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
from cp0c_spef_trace import rows, table, extract_graphs, diagnose, canon

PIN = 'c463e998615210dc322c999fc9a9f56885dbd664'
ODB_SHA = '6968ce394b95df5611f9dbb22d126bec7ecefae637e08c204d3cf628dc831c06'
SDC_SHA = 'f0216b26bf10e67bee572a0c8058413f6f0cbfb2c9d167368fb17bc92efe202c'
OLD_SPEF_SHA = 'ef4e50b73a9afd09cd4258b7b4c5a4d0da8a4cb97bf3eb517c14ee3039e7d65b'
COUNTS = ('setup_violations','hold_violations','max_slew_violations','max_cap_violations','max_fanout_violations')
SLACKS = ('setup_tt','setup_ff','hold_tt','hold_ff')
REACHABLE = 'REACHABLE_IN_SPEF_REVIEW_STA_MAPPING'


def spef_stats(path):
    counts = collections.Counter()
    units = {}
    section = None
    in_net = False
    minimum = math.inf
    with path.open() as f:
        require(f.readline().startswith('*SPEF '), 'Not a SPEF file.')
        for line in f:
            p = line.split()
            if not p: continue
            key = p[0]
            if key in ('*C_UNIT','*R_UNIT','*T_UNIT'):
                units[key] = ' '.join(p[1:])
            elif key == '*D_NET':
                require(not in_net and len(p)==3, 'Malformed SPEF net.')
                value = float(p[-1])
                require(math.isfinite(value) and value>=0, 'Invalid net capacitance.')
                counts['nets'] += 1
                in_net, section = True, None
            elif key in ('*CONN','*CAP','*RES'):
                require(in_net, 'Section outside SPEF net.')
                section = key
            elif key == '*END':
                require(in_net, 'SPEF END without net.')
                counts['ends'] += 1
                in_net, section = False, None
            elif in_net and section=='*CONN' and key in ('*I','*P'):
                counts['connections'] += 1
            elif in_net and section in ('*CAP','*RES') and key.isdigit():
                require(len(p)==4 if section=='*RES' else len(p) in (3,4), 'Malformed RC row.')
                value = float(p[-1])
                require(math.isfinite(value) and value>=0, 'Invalid SPEF RC.')
                tag = 'resistances' if section=='*RES' else 'capacitances'
                counts[tag] += 1
                counts['positive_'+tag] += value>0
                if section=='*RES' and value>0: minimum = min(minimum,value)
    require(not in_net and counts['nets']==counts['ends'], 'Incomplete SPEF.')
    require(all(counts[k]>0 for k in ('nets','connections','positive_resistances','positive_capacitances')), 'Empty RC model.')
    require(units=={'*C_UNIT':'1 PF','*R_UNIT':'1 OHM','*T_UNIT':'1 NS'}, 'Unexpected SPEF units.')
    counts['minimum_positive_resistance_ohm'] = minimum
    return dict(counts)


def graph_comparison(trace_reports, new_spef, destination):
    nets = [r['net'] for r in rows(trace_reports/'nets.tsv')]
    before = extract_graphs(trace_reports/'selected-nets.spef', nets, destination/'before-selected.spef')
    after = extract_graphs(new_spef, nets, destination/'after-selected.spef')
    comparisons = []
    for r in rows(trace_reports/'target-map.tsv'):
        if r['kind']!='partial': continue
        net, driver, load = r['net'],r['physical_driver'],r['physical_load']
        old = diagnose(before.get(canon(net)),driver,load)
        require(old=='DISCONNECTED_RC_COMPONENT', 'Original graph no longer reproduces the reviewed disconnection.')
        new = diagnose(after.get(canon(net)),driver,load)
        comparisons.append(dict(net=net,driver=driver,load=load,before=old,after=new))
    require(len(comparisons)==196 and len({(r['driver'],r['load']) for r in comparisons})==196,
            'Expected exactly 196 original missing-load pairs.')
    table(destination/'graph-comparison.tsv',list(comparisons[0]),comparisons)
    counts = collections.Counter(r['after'] for r in comparisons)
    return counts


def inspect_replay(case, reference):
    metrics = parse_metrics(case/'reports/final.tsv')
    audit = parse_metrics(case/'reports/audit.tsv')
    classified = classify(case/'reports/final-erc.rpt')
    for kind,key in (('max slew','max_slew_violations'),('max capacitance','max_cap_violations'),('max fanout','max_fanout_violations')):
        require(sum(r[0]==kind for r in classified)==metrics[key], 'ERC count/report mismatch.')
    fields = ['check','category','pin','limit','actual','reported_slack']
    table(case/'reports/classified.tsv',fields,[dict(zip(fields,r)) for r in classified])
    table(case/'reports/internal-findings.tsv',fields,[dict(zip(fields,r)) for r in classified if r[1]=='Internal'])
    full = rows(case/'reports/unannotated.tsv')
    partial = (case/'reports/partially-unannotated-drivers.txt').read_text().splitlines()
    loads = rows(case/'reports/partially-unannotated-loads.tsv')
    require(len(full)==audit['unannotated_drivers'], 'Full-driver count mismatch.')
    require(len(partial)==len(set(partial))==audit['partially_unannotated_drivers'], 'Partial-driver count mismatch.')
    require(len(loads)==audit['partially_unannotated_loads'], 'Missing-load count mismatch.')
    require(all(r['driver'] in set(partial) and r['missing_load'] for r in loads), 'Orphan missing load.')
    known = {(r['driver'],r['category'],r['net']) for r in rows(reference)}
    unexpected = [r for r in full if (r['driver'],r['category'],r['net']) not in known]
    table(case/'reports/unexpected-unannotated.tsv',['driver','category','net'],
          [{k:r[k] for k in ('driver','category','net')} for r in unexpected])
    return metrics,audit,sum(r[1]=='Internal' for r in classified),not partial and not loads and not unexpected


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('trace',type=Path)
    args = parser.parse_args()
    require(sys.version_info>=(3,11), 'Python 3.11+ required.')
    require(platform.system()=='Darwin' and platform.machine()=='arm64', 'Native Apple Silicon macOS required.')
    root = Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results = root/'native/results'
    trace = args.trace.resolve()
    require(trace.parent==results and trace.name.startswith('cp0c-closure-trace-'), 'Use the completed closure-trace directory.')
    require((trace/'exit-status.txt').read_text().strip()=='0', 'Trace did not complete.')
    require((trace/'preservation.txt').read_text().strip()=='All recorded source/tool/evidence hashes still match.', 'Trace preservation not confirmed.')
    provenance = (trace/'provenance.txt').read_text().splitlines()
    source_lines = [s.removeprefix('Source: ') for s in provenance if s.startswith('Source: ')]
    require(len(source_lines)==1, 'Ambiguous routed-RC source.')
    source = Path(source_lines[0]).resolve()
    require(source.parent==results and source.name.startswith('cp0c-routed-rc-'), 'Unexpected routed-RC source.')
    odb,sdc,old_spef = trace/'input/routed.odb',trace/'input/15pf.sdc',source/'extract/out/routed-lef.spef'
    require(sha256(odb)==ODB_SHA and sha256(sdc)==SDC_SHA and sha256(old_spef)==OLD_SPEF_SHA, 'Reviewed ODB/SDC/SPEF differs.')
    baseline = parse_metrics(trace/'reports/final.tsv')
    require([baseline[k] for k in COUNTS]==[0,0,76,71,3], 'Trace timing/ERC differs.')
    require(parse_metrics(trace/'reports/annotation-counts.tsv')=={'full':575,'partial':76,'loads':196}, 'Trace coverage differs.')
    require(shutil.disk_usage(results).free>=4*1024**3, 'At least 4 GiB free disk space required.')
    toolbase = Path.home()/'tools'/f'croc-openroad-{PIN}'
    tool = toolbase/'install/bin/openroad'
    lock = results/'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError: raise RuntimeError(f'Another CP0C command may be running. Inspect {lock}/owner.txt.') from None
    run = preserved = None
    status = 1
    try:
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=rc-connectivity\n')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run = Path(tempfile.mkdtemp(prefix=f'cp0c-rc-connectivity-{stamp}-',dir=results))
        print(f'Evidence: {run}\nVersion: 1.0-positive-threshold\nOne extraction plus fresh timing replay, 2 threads.\n'
              'Only extraction option change: -max_res 0 -> 0.000001 ohm.',flush=True)
        entries = []
        for manifest in (trace/'source.sha256',trace/'input.sha256',source/'extract/output.sha256',toolbase/'tool.sha256'):
            entries.extend(verify_manifest(manifest))
        require({tool.resolve(),odb,sdc,old_spef,trace/'input/pg-targets.tsv'} <= {Path(p).resolve() for _,p in entries}, 'Inputs not hash-covered.')
        entries.extend((sha256(p),str(p)) for p in trace.rglob('*') if p.is_file())
        preserved = run/'source.sha256'
        preserved.write_text(''.join(f'{h}  {p}\n' for h,p in dict.fromkeys(entries)))
        inputs = run/'input'; inputs.mkdir()
        for source_name,dest_name in (('routed.odb','routed.odb'),('15pf.sdc','15pf.sdc'),('pg-targets.tsv','targets.tsv')):
            shutil.copy2(trace/'input'/source_name,inputs/dest_name)
        (run/'input.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in sorted(inputs.iterdir())))
        helpers = run/'helpers'; helpers.mkdir()
        here = Path(__file__).resolve().parent
        for name in ('cp0c-rc-connectivity.py','cp0c-rc-connectivity.tcl','cp0c_spef_trace.py','cp0c_rc_common.py',
                     'cp0c_pg_audit.tcl','cp0c_route_metrics.tcl','cp0c_erc_support.py','cp0c_postroute_annotation.tcl','cp0c_annotation_sections.tcl'):
            shutil.copy2(here/name,helpers/name)
        reference = run/'reference'; reference.mkdir()
        shutil.copy2(source/'replay/reports/unannotated.tsv',reference/'known-unannotated.tsv')
        shutil.copy2(trace/'reports/final.tsv',reference/'before-final.tsv')
        shutil.copy2(trace/'reports/repair-plan.tsv',reference/'previous-repair-plan.tsv')
        shutil.copy2(trace/'reports/io-contract.md',reference/'io-contract.md')
        (run/'provenance.txt').write_text(f'Trace: {trace}\nRouted RC source: {source}\nOpenROAD pin: {PIN}\n'
            'Workaround: positive max_res=0.000001 ohm; retain no_merge_via_res and other extraction options.\n'
            'Pinned initJunctionIdMaps otherwise returns before building SHORT-junction maps.\n'
            'Hypothesis requires successful native graph and STA coverage checks. No SPEF stitching.\n'
            'Original ODB/SDC preserved; disposable in-memory wire ordering is performed by OpenRCX.\n'
            'One approximate LEF RC model for TT/FF cell libraries, without calibrated coupling/process RC corners.\n'
            'No CP0C PASS; 15 pF and dynamic GPIO enables retained.\n')
        reports = run/'reports'; reports.mkdir()
        spef = run/'extract/out/routed-lef.spef'
        warnings = []
        for mode in ('extract','replay'):
            case = run/mode; case.mkdir()
            for folder in ('scripts','src'): shutil.copytree(trace/folder,case/folder)
            env = os.environ.copy()
            env.update(CP0R_ROOT=str(root),CP0R_WORK=str(case),CP0R_TOOL=str(tool),CP0R_HELPERS=str(helpers),
                       CP0R_SCRIPT=str(helpers/'cp0c-rc-connectivity.tcl'),CP0R_MODE=mode,CP0R_ODB=str(inputs/'routed.odb'),
                       CP0R_SDC=str(inputs/'15pf.sdc'),CP0R_SPEF=str(spef),CP0P_TARGETS=str(inputs/'targets.tsv'),
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
            print(f'\nRunning {mode}',flush=True)
            rc = stream(['/bin/bash','-c',shell],root,env,case/'console.log')
            (case/'exit-status.txt').write_text(str(rc)+'\n')
            outputs = sorted(p for p in (case/'out').glob('*') if p.is_file())
            if outputs: (case/'output.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in outputs))
            require(rc==0, f'{mode} stopped with exit {rc}; upload this evidence.')
            log = (case/'console.log').read_text()
            require(f'CP0C_RC_CONNECTIVITY_COMPLETE {mode}' in log.splitlines(), 'Missing native completion marker.')
            warnings.extend(f'{mode}: {s}' for s in log.splitlines() if re.search(r'\[(WARNING|ERROR)\b',s))
            require(parse_metrics(case/'reports/physical.tsv')=={'antenna_violating_nets':0,'supply_assignment_findings':0,
                    'design_has_routing':1,'physical_signature_unchanged':1}, 'Physical audit changed.')
            pg = parse_metrics(case/'reports/final-pg-counts.tsv')
            require(pg['findings']==0 and pg['target_pins']==170, 'Supply assignment coverage differs.')
            units = (case/'reports/units.rpt').read_text()
            require(re.search(r'\btime\s+1ns\b',units) and re.search(r'\bcapacitance\s+1pF\b',units), 'Unexpected units.')
            if mode=='extract':
                stats = spef_stats(spef)
                inventory_matches = stats['nets']==34668 and stats['connections']==119571
                (case/'reports/spef-stats.tsv').write_text(''.join(f'{k}\t{v}\n' for k,v in sorted(stats.items())))
                graph_counts = graph_comparison(trace/'reports',spef,reports)
        metrics,audit,internal,sta_covered = inspect_replay(run/'replay',reference/'known-unannotated.tsv')
        require(abs(metrics['stdcell_area_um2']-baseline['stdcell_area_um2'])<1e-6, 'Cell area changed unexpectedly.')
        covered = sta_covered and graph_counts[REACHABLE]==196 and inventory_matches and not warnings
        (run/'warnings.txt').write_text('\n'.join(warnings)+'\n')
        coverage_status = 'RC_COVERAGE_RESOLVED_FOR_INTERNAL_REPAIR' if covered else 'RC_COVERAGE_STILL_BLOCKED'
        (run/'rc-coverage-status.txt').write_text(coverage_status+'\n')
        lines = ['CP0C RC CONNECTIVITY WORKAROUND COMPLETE',coverage_status,'CP0C_PENDING','',
                 f'Original missing loads reachable in new SPEF: {graph_counts[REACHABLE]}/196',
                 f'Partially annotated drivers: 76 -> {int(audit["partially_unannotated_drivers"])}',
                 f'Missing loads reported by STA: 196 -> {int(audit["partially_unannotated_loads"])}',
                 f'Fully unannotated drivers: 575 -> {int(audit["unannotated_drivers"])}',
                 f'Warnings requiring review: {len(warnings)}',f'Current internal ERC findings: {internal}','',
                 f'Extracted net/pin counts unchanged: {inventory_matches}',
                 '| Metric | Before | After |','|---|---:|---:|']
        lines += [f'| {k} | {baseline[k]:.9g} | {metrics[k]:.9g} |' for k in (*COUNTS,*SLACKS,'stdcell_area_um2')]
        lines += ['', 'New timing is for this approximate LEF RC model; this is not calibrated extraction or signoff.',
                  'Known full-annotation exceptions still need disposition. Physical connectivity is not independently signed off.',
                  'Current repair targets: replay/reports/internal-findings.tsv. Earlier eight-target plan is reference only.',
                  'The source physical candidate and 15 pF/dynamic-enable constraints were retained.',
                  'Upload this archive, including when coverage remains blocked. No additional sweep is scheduled.']
        (run/'summary.md').write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines),flush=True)
        verify_manifest(run/'input.sha256'); verify_manifest(run/'extract/output.sha256')
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
                    with (run/'failure.txt').open('a') as f: f.write(f'Preservation failed: {exc}\n')
            if run:
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                (run/'exit-status.txt').write_text(str(status)+'\n')
                archive = run.with_suffix('.tar.gz')
                with tarfile.open(archive,'w:gz') as tar:
                    for p in sorted(run.rglob('*')):
                        rel = p.relative_to(run)
                        if p.is_file() and rel.parts[0]!='input' and 'out' not in rel.parts and 'save' not in rel.parts:
                            tar.add(p,arcname=str(rel),recursive=False)
                print(f'Upload: {archive}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True); lock.rmdir()
    return status


if __name__=='__main__':
    try: sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr); sys.exit(1)
