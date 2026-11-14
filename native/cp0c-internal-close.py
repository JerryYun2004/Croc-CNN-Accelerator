#!/usr/bin/env python3
"""Apply the fixed eight-net internal ECO and verify a separate routed candidate.

python3 native/cp0c-internal-close.py RC_CONNECTIVITY_DIRECTORY
Native macOS, 2 threads. Original 15 pF/dynamic-enable constraints retained.
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

from cp0c_erc_support import require,sha256,verify_manifest,parse_metrics
from cp0c_rc_common import stream
from cp0c_spef_trace import rows,table
from cp0c_internal_plan import make_plan
from cp0c_internal_support import spef_stats,inspect_replay

PIN = 'c463e998615210dc322c999fc9a9f56885dbd664'
ODB_SHA = '6968ce394b95df5611f9dbb22d126bec7ecefae637e08c204d3cf628dc831c06'
SDC_SHA = 'f0216b26bf10e67bee572a0c8058413f6f0cbfb2c9d167368fb17bc92efe202c'
SPEF_SHA = 'b4afc37a547822290d2874700c5fb8ed423a7b5409f7efbc78413540f4c1717e'
COUNTS = ('setup_violations','hold_violations','max_slew_violations','max_cap_violations','max_fanout_violations')
SLACKS = ('setup_tt','setup_ff','hold_tt','hold_ff')


def stage_physical(case,eco=False):
    p = parse_metrics(case/'reports/physical.tsv')
    require(p['antenna_violating_nets']==p['supply_assignment_findings']==0,'Physical checks failed.')
    pg = parse_metrics(case/'reports/final-pg-counts.tsv')
    require(pg['findings']==0 and pg['target_pins']==170,'Original 85-buffer supply coverage differs.')
    if eco:
        require(p['router_drvs']==0 and p['inserted_buffers']==13 and p['new_buffer_supply_pins']==26
                and p['structural_buffer_check']==1,'ECO scope/physical checks failed.')
        require(len(rows(case/'reports/inserted-buffers.tsv'))==13,'Inserted-buffer report mismatch.')
        require(len(rows(case/'reports/new-buffer-pg.tsv'))==26,'New-buffer supply report mismatch.')
    else:
        require(p['design_has_routing']==p['physical_signature_unchanged']==1,'Read-only physical audit differs.')
    return p


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source',type=Path)
    args = parser.parse_args()
    require(sys.version_info>=(3,11),'Python 3.11+ required.')
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Native Apple Silicon macOS required.')
    root = Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results = root/'native/results'; source = args.source.resolve()
    require(source.parent==results and source.name.startswith('cp0c-rc-connectivity-'),'Use the completed RC-connectivity run.')
    require((source/'exit-status.txt').read_text().strip()=='0','Source run did not complete.')
    require((source/'rc-coverage-status.txt').read_text().strip()=='RC_COVERAGE_RESOLVED_FOR_INTERNAL_REPAIR','RC coverage is not resolved.')
    require((source/'preservation.txt').read_text().strip()=='All recorded source/tool/evidence hashes still match.','Source preservation missing.')
    match = re.search(r'^Trace: (.+)$',(source/'provenance.txt').read_text(),re.M)
    require(match,'Source trace provenance missing.')
    trace = Path(match[1]).resolve()
    require(trace.parent==results and trace.name.startswith('cp0c-closure-trace-'),'Unexpected trace path.')
    odb,sdc,spef = source/'input/routed.odb',source/'input/15pf.sdc',source/'extract/out/routed-lef.spef'
    require(sha256(odb)==ODB_SHA and sha256(sdc)==SDC_SHA and sha256(spef)==SPEF_SHA,'Reviewed ODB/SDC/corrected SPEF differs.')
    baseline = parse_metrics(source/'replay/reports/final.tsv')
    require([baseline[k] for k in COUNTS]==[0,0,76,71,3],'Source timing/ERC differs.')
    a = parse_metrics(source/'replay/reports/audit.tsv')
    require(a=={'check_setup_clean':1,'unannotated_drivers':575,'partially_unannotated_drivers':0,'partially_unannotated_loads':0},'Source annotation differs.')
    require(shutil.disk_usage(results).free>=8*1024**3,'At least 8 GiB free space required.')
    toolbase = Path.home()/'tools'/f'croc-openroad-{PIN}'; tool = toolbase/'install/bin/openroad'
    lock = results/'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError: raise RuntimeError(f'Another CP0C command may be running. Inspect {lock}/owner.txt.') from None
    run = preserved = None; status = 1
    try:
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=internal-close\n')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        run = Path(tempfile.mkdtemp(prefix=f'cp0c-internal-close-{stamp}-',dir=results))
        print(f'Candidate: {run}\nVersion: 1.0-targeted-13-buffers\nThreads: 2\n'
              'Eight nets, 13 buffers. Incremental route, bounded antenna repair, corrected LEF RC.',flush=True)
        entries = []
        for manifest in (source/'source.sha256',source/'input.sha256',source/'extract/output.sha256',toolbase/'tool.sha256'):
            entries.extend(verify_manifest(manifest))
        required = {tool.resolve(),odb,sdc,spef,source/'input/targets.tsv',trace/'reports/repair-plan.tsv',trace/'reports/net-pins.tsv'}
        require(required<={Path(p).resolve() for _,p in entries},'Required inputs not hash-covered.')
        entries.extend((sha256(p),str(p)) for p in source.rglob('*') if p.is_file())
        preserved = run/'source.sha256'
        preserved.write_text(''.join(f'{h}  {p}\n' for h,p in dict.fromkeys(entries)))
        inputs = run/'input'; inputs.mkdir()
        for name in ('routed.odb','15pf.sdc','targets.tsv'): shutil.copy2(source/'input'/name,inputs/name)
        make_plan(trace/'reports',source/'replay/reports/internal-findings.tsv',inputs/'actions.tsv')
        (run/'input.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in sorted(inputs.iterdir())))
        helpers = run/'helpers'; helpers.mkdir(); here = Path(__file__).resolve().parent
        helper_names = ('cp0c-internal-close.py','cp0c-internal-eco.tcl','cp0c-internal-model.tcl',
            'cp0c_internal_plan.py','cp0c_internal_support.py','cp0c_erc_support.py','cp0c_rc_common.py',
            'cp0c_spef_trace.py','cp0c_pg_audit.tcl','cp0c_route_metrics.tcl',
            'cp0c_postroute_annotation.tcl','cp0c_annotation_sections.tcl')
        for name in helper_names: shutil.copy2(here/name,helpers/name)
        reference = run/'reference'; reference.mkdir()
        for source_path,dest in ((source/'replay/reports/final.tsv','before-final.tsv'),
            (source/'replay/reports/unannotated.tsv','known-unannotated.tsv'),
            (source/'replay/reports/internal-findings.tsv','before-internal.tsv'),
            (source/'reference/io-contract.md','io-contract.md'),(inputs/'actions.tsv','actions.tsv')):
            shutil.copy2(source_path,reference/dest)
        (run/'provenance.txt').write_text(f'Source: {source}\nTrace: {trace}\nOpenROAD pin: {PIN}\n'
            'Fixed eight-net ECO: five SRAM isolation buffers plus eight fanout branch buffers.\n'
            'Maximum three ordinary loads per fanout branch; original antenna diodes retained on roots.\n'
            'Incremental global routing and detailed routing; at most two extra antenna repair passes.\n'
            'Fresh corrected LEF extraction with max_res=0.000001, then independent SPEF timing replay.\n'
            'Original 15 pF SDC and dynamic GPIO enables; no generic timing/design optimizer or signoff claim.\n')
        warnings = []; physical = None
        new_spef = run/'extract/out/routed-lef.spef'
        candidate_odb = run/'eco/out/croc_internal.odb'
        candidate_sdc = run/'eco/out/croc_internal.sdc'
        for stage in ('control','eco','extract','replay'):
            case = run/stage; case.mkdir()
            for folder in ('scripts','src'): shutil.copytree(source/'replay'/folder,case/folder)
            env = os.environ.copy()
            script = helpers/('cp0c-internal-eco.tcl' if stage=='eco' else 'cp0c-internal-model.tcl')
            input_odb = inputs/'routed.odb' if stage in ('control','eco') else candidate_odb
            input_sdc = inputs/'15pf.sdc' if stage in ('control','eco') else candidate_sdc
            env.update(CP0E_ROOT=str(root),CP0E_WORK=str(case),CP0E_TOOL=str(tool),CP0E_SCRIPT=str(script),
                CP0E_HELPERS=str(helpers),CP0E_ODB=str(input_odb),CP0E_SDC=str(input_sdc),
                CP0E_ACTIONS=str(inputs/'actions.tsv'),CP0E_LOG=str(case/'openroad.log'),
                CP0R_HELPERS=str(helpers),CP0R_ODB=str(input_odb),CP0R_SDC=str(input_sdc),
                CP0R_MODE='extract' if stage=='extract' else 'replay',
                CP0R_SPEF=str(spef if stage=='control' else new_spef),CP0P_TARGETS=str(inputs/'targets.tsv'))
            shell = r'''set -euo pipefail
cd "$CP0E_ROOT"
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
cd "$CP0E_WORK"
exec "$CP0E_TOOL" -no_init -exit "$CP0E_SCRIPT" -log "$CP0E_LOG"
'''
            print(f'\nRunning {stage}',flush=True)
            rc = stream(['/bin/bash','-c',shell],root,env,case/'console.log')
            (case/'exit-status.txt').write_text(str(rc)+'\n')
            outputs = sorted(p for p in (case/'out').glob('*') if p.is_file())
            if outputs: (case/'output.sha256').write_text(''.join(f'{sha256(p)}  {p}\n' for p in outputs))
            require(rc==0,f'{stage} stopped with exit {rc}; upload the retained evidence.')
            log = (case/'console.log').read_text()
            marker = 'CP0C_INTERNAL_ECO_COMPLETE' if stage=='eco' else 'CP0C_RC_CONNECTIVITY_COMPLETE '+env['CP0R_MODE']
            require(marker in log.splitlines(),'Missing native completion marker.')
            new_warnings = [f'{stage}: {s}' for s in log.splitlines() if re.search(r'\[(WARNING|ERROR)\b',s)]
            warnings.extend(new_warnings)
            units = (case/'reports/units.rpt').read_text()
            require(re.search(r'\btime\s+1ns\b',units) and re.search(r'\bcapacitance\s+1pF\b',units),'Unexpected units.')
            physical_case = stage_physical(case,stage=='eco')
            if stage=='control':
                met,audit,internal,covered = inspect_replay(case,reference/'known-unannotated.tsv')
                require(covered and internal==8 and not new_warnings,'Control coverage or ERC changed.')
                require(all(met[k]==baseline[k] for k in COUNTS),'Control counts did not reproduce.')
                require(all(abs(met[k]-baseline[k])<=.005 for k in SLACKS),'Control timing differs by over 5 ps.')
            elif stage=='eco':
                physical = physical_case
                require(sha256(candidate_sdc)==SDC_SHA,'Candidate SDC was changed.')
                for name in ('croc_internal.odb','croc_internal.def','croc_internal.v','croc_internal_lvs.v','routed_no_fill.odb'):
                    require((case/'out'/name).stat().st_size>0,f'Missing physical output {name}.')
            elif stage=='extract':
                stats = spef_stats(new_spef)
                (case/'reports/spef-stats.tsv').write_text(''.join(f'{k}\t{v}\n' for k,v in sorted(stats.items())))
                # Save the 8 root nets and all 13 new leaf nets for follow-up.
                # Scope is known from the insertion records; global STA checks all nets.
                from cp0c_spef_trace import extract_graphs
                roots = {r['root_net'] for r in rows(inputs/'actions.tsv')}
                leafs = {r['leaf_net'] for r in rows(run/'eco/reports/buffer-nets.tsv')}
                extract_graphs(new_spef,sorted(roots|leafs),case/'reports/eco-nets.spef')
        met,audit,internal,covered = inspect_replay(run/'replay',reference/'known-unannotated.tsv')
        area_delta = met['stdcell_area_um2']-baseline['stdcell_area_um2']
        expected_area = physical['buffer_area_um2']+physical['added_diode_area_um2']
        require(abs(area_delta-expected_area)<.001,'Cell area change does not match added buffers/diodes.')
        accepted = covered and internal==0 and met['setup_violations']==met['hold_violations']==0 and audit['check_setup_clean']==1 and not warnings
        outcome = 'INTERNAL_CLOSURE_CANDIDATE_CLEAN' if accepted else 'INTERNAL_CLOSURE_REQUIRES_REVIEW'
        (run/'internal-status.txt').write_text(outcome+'\n')
        (run/'warnings.txt').write_text('\n'.join(warnings)+'\n')
        lines = ['CP0C TARGETED INTERNAL REPAIR COMPLETE',outcome,'CP0C_PENDING','',
            f'Internal ERC findings: 8 -> {internal}',
            f'Partially annotated drivers: {int(audit["partially_unannotated_drivers"])}',
            f'Missing STA loads: {int(audit["partially_unannotated_loads"])}',
            f'Warnings requiring review: {len(warnings)}',
            f'Added buffers: 13; added antenna diodes: {int(physical["added_diodes"])}',
            f'Cell area increase: {area_delta:.6f} um^2',
            f'Original instances moved during legalization: {int(physical["moved_original_instances"])}','',
            '| Metric | Before | After |','|---|---:|---:|']
        lines += [f'| {k} | {baseline[k]:.9g} | {met[k]:.9g} |' for k in (*COUNTS,*SLACKS,'stdcell_area_um2')]
        lines += ['', 'Router DRVs: 0; antenna-violating nets: 0; supply-assignment findings: 0.',
            'Original signal connections checked modulo the explicitly inserted noninverting buffers.',
            'This structural check is not formal equivalence, LVS or physical power continuity signoff.',
            'Timing uses corrected approximate LEF RC, without calibrated coupling/interconnect process corners.',
            '15 pF I/O findings and known full-annotation exceptions remain separate open items.',
            'Candidate: eco/out/croc_internal.{odb,def,sdc,v}; no baseline pointer or CP0C PASS changed.']
        (run/'summary.md').write_text('\n'.join(lines)+'\n'); print('\n'.join(lines),flush=True)
        verify_manifest(run/'input.sha256'); verify_manifest(run/'eco/output.sha256'); verify_manifest(run/'extract/output.sha256')
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
