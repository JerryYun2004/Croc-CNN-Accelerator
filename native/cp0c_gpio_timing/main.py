#!/usr/bin/env python3
"""Audit GPIO input/output/enable timing without modifying the final design."""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from base_support import require, sha, write_json, rows, table, metrics, compare_metrics, audit_annotation, sdc_semantics
from runtime import bounded, archive, tcl_quote
from support import CASES, GPIOS, variant_sdc, audit_runtime_sdc, slew_rows, compare_slews, snapshot, input_control

VERSION='1.0-gpio-modes'

def stable_constraints(path):
    # Only these GPIO diagnostics and signal output loads may differ.
    result=[]
    for line in sdc_semantics(path).splitlines():
        if line.startswith('set_load ') and any('{'+p+'}' in line for p in GPIOS | {'jtag_tdo_o','status_o','uart_tx_o'} | {f'unused{i}_o' for i in range(4)}):
            continue
        if line.startswith(('set_driving_cell ','set_input_transition ','set_case_analysis ')) and re.search(r'gpio\d+_io',line):
            continue
        result.append(line)
    return sorted(result)

def collect_case(case_dir, case):
    reports=case_dir/'reports'
    audit=audit_runtime_sdc(case_dir/'out/runtime.sdc',case)
    snap=snapshot(reports); vertices=[]; padchecks=[]; paths=[]; arc_counts=[]; globalchecks=[]
    from base_support import erc_rows
    for corner in ('tt','ff'):
        globalchecks += [dict(case=case['name'],corner=corner,**r)
                         for r in erc_rows(reports/f'global-{corner}-erc.rpt')]
    table(reports/'global-checks.tsv', ['case','corner','check','pin','limit','actual','reported_slack','violated'],globalchecks)
    for port in sorted(GPIOS):
        for corner in ('tt','ff'):
            for suffix,expected in [('pad',f'pad_{port}/pad'),('port',port),('data',f'pad_{port}/c2p'),
                                    ('enable',f'pad_{port}/c2p_en'),('receive',f'pad_{port}/p2c')]:
                data=slew_rows(reports/'slews'/f'{port}-{corner}-{suffix}.rpt')
                require(all(r['pin']==expected for r in data),'Slew report belongs to another pin')
                expected_sides={'load','driver'} if suffix in ('pad','port') else {'single'}
                require({r['side'] for r in data}==expected_sides, 'Incomplete GPIO vertex coverage')
                vertices += [dict(case=case['name'],port=port,corner=corner,**r) for r in data]
            checks=erc_rows(reports/'pads'/f'{port}-{corner}.rpt',violators_only=False)
            require(checks,'Empty pad check report')
            padchecks += [dict(case=case['name'],port=port,corner=corner,**r) for r in checks]
            for arc in ('data','enable','receive'):
                for delay in ('min','max'):
                    p=reports/'arcs'/f'{port}-{corner}-{arc}-{delay}.rpt'
                    text=p.read_text()
                    arc_counts.append(dict(port=port,corner=corner,arc=arc,delay=delay,
                                           reported_arcs=text.count('Arc type:')))
                    # These are structural model reports, even when a mode
                    # disables a timing path. Do not infer active-path safety.
                    require('Arc type:' in text,'Missing structural arc report: '+str(p))
    for corner in ('tt','ff'):
        for kind in ('data','enable','receive'):
            p=reports/'paths'/f'{kind}-{corner}.rpt'
            paths.append(dict(kind=kind,corner=corner,reported_paths=p.read_text().count('Startpoint:')))
    write_json(reports/'parsed.json',dict(snapshot=snap,runtime_sdc=audit,path_counts=paths,structural_arcs=arc_counts))
    table(reports/'vertices.tsv',list(vertices[0]),vertices)
    table(reports/'pad-checks.tsv',list(padchecks[0]),padchecks)
    return snap,vertices,paths

def comparison_to_prior(source, case, snap, vertices):
    prior=case['prior']; ref=source/'sweep/reports'; changes=[]
    for port in sorted(GPIOS):
        for corner in ('tt','ff'):
            c=[r for r in vertices if r['port']==port and r['corner']==corner and r['pin']==f'pad_{port}/pad']
            p=slew_rows(ref/f'{prior}-pads/{port}-{corner}-slews.rpt')
            changes += [dict(port=port,corner=corner,**d) for d in compare_slews(c,p)]
    old=metrics(ref/f'{prior}.tsv')
    return dict(case=case['name'],metrics_match=compare_metrics(snap['metrics'],old,tolerance=1e-5),
                metric_deltas={k:snap['metrics'][k]-v for k,v in old.items()},
                slew_vertices_match=not changes,changed_slew_values=changes,
                interpretation='A difference identifies fresh-process versus incremental-sweep behavior; it is not automatically a physical violation or an approved correction.')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('final_timing_run',type=Path); args=ap.parse_args()
    require(sys.version_info>=(3,11),'Use the existing Python 3.11+ environment')
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Run on the existing native Apple Silicon Mac')
    here=Path(__file__).resolve().parent; pins=json.loads((here/'pins.json').read_text())
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results'; source=args.final_timing_run.resolve()
    require(source.parent==results and source.is_dir(),'Input must be the completed final-timing run under this repository native/results')
    lock=results/'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError:
        owner=(lock/'owner.txt').read_text() if (lock/'owner.txt').is_file() else '(no owner record)'
        raise RuntimeError(f'Another CP0C stage may be active; inspect {lock}/owner.txt\n{owner}') from None
    run=None; preserve={}; code=1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=gpio-timing\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-gpio-timing-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for d in ('helpers','reports','reference','cases','logs'): (run/d).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nTen fresh OpenROAD processes, one at a time; two threads; 180 seconds per process.',flush=True)
        require(shutil.disk_usage(run).free>=2*1024**3,'At least 2 GiB free disk is required')
        def checked(path,digest=None):
            path=Path(path); require(path.is_file(),f'Missing input: {path}')
            h=sha(path); require(digest is None or h==digest,f'Input checksum differs: {path}')
            preserve[str(path.resolve())]=h; return path
        for p in sorted(here.iterdir()):
            if p.is_file(): shutil.copy2(checked(p),run/'helpers'/p.name)
        for rel,digest in pins['source_files'].items(): checked(source/rel,digest)
        previous=json.loads((source/'reports/conclusion.json').read_text())
        require(previous['status']=='FINAL_TIMING_LOAD_CHARACTERIZATION_COMPLETE' and
                previous['original_constraints_restored'] and not previous['source_geometry_changed'], 'Final timing prerequisite is incomplete')
        provenance=json.loads((source/'provenance.json').read_text())
        physical=results/pins['physical_run']
        require(Path(provenance['physical_source']).resolve()==physical,'Physical lineage differs')
        odb=checked(physical/'out/croc_sram_power.odb',pins['physical_odb'])
        spef=checked(source/'extract/out/final-lef.spef',pins['spef'])
        sdc=checked(source/'reference/original.sdc',pins['sdc'])
        tool=checked(Path.home()/('tools/croc-openroad-'+pins['openroad_pin'])/'install/bin/openroad',pins['openroad_sha256'])
        description=subprocess.check_output(['file','-b','-L',str(tool)],text=True).strip()
        require('Mach-O' in description and 'arm64' in description,'OpenROAD is not a native arm64 executable')
        libs={c:[] for c in ('tt','ff')}
        for name,digest in pins['libraries'].items():
            lib=checked(root/'ihp13/pdk/lib'/name,digest)
            libs['tt' if '_typ_' in name else 'ff'].append(lib)
        for paths in libs.values():
            require(len(paths)==3,'Expected three libraries per corner')
            paths.sort(key=lambda p: 0 if p.name.startswith('sg13g2_stdcell_') else 1 if p.name.startswith('sg13cmos5l_io_') else 2)
        config=run/'helpers/config.tcl'
        config.write_text(''.join('set ::gt_libs('+c+') [list '+' '.join(tcl_quote(p) for p in paths)+']\n' for c,paths in libs.items()))
        shutil.copy2(sdc,run/'reference/original.sdc')
        write_json(run/'provenance.json',dict(version=VERSION,source=str(source),physical_source=str(physical),
            odb_sha256=pins['physical_odb'],spef_sha256=pins['spef'],sdc_sha256=pins['sdc'],
            openroad_pin=pins['openroad_pin'],opensta_pin=pins['opensta_pin'],openroad_native=description,
            cases=CASES,source_geometry_changed=False,accepted_constraint_change=False,candidate_promoted=False,
            model_limitations=['One approximate LEF RC model for TT/FF; no slow corner.',
                '80 loaded pad-driver RC exceptions remain; no package/board RC signoff.',
                '.5 pF is a diagnostic sample and may require Liberty table extrapolation.',
                'Fixed OE cases exclude direction switching; synthetic input slews are not an interface specification.']))
        times={}; summary=[]; all_vertices={}; snapshots={}; comparisons=[]; warnings=[]; annotation={}
        stable=None
        for case in CASES:
            name=case['name']; folder=run/'cases'/name; folder.mkdir()
            case_sdc=folder/'input.sdc'
            edits=variant_sdc(sdc,case_sdc,case)
            write_json(folder/'configuration.json',dict(**case,sdc_edits=edits,diagnostic_only=True))
            env=dict(os.environ,OMP_NUM_THREADS='2',GT_CONFIG=str(config),GT_HELPERS=str(run/'helpers'),
                     GT_ODB=str(odb),GT_SDC=str(case_sdc),GT_SPEF=str(spef),GT_CASE=name)
            print('STAGE: '+name,flush=True); log=run/'logs'/(name+'.log')
            times[name]=bounded([tool,'-no_init','-exit',run/'helpers/worker.tcl'],folder,env,log,180)
            text=log.read_text(errors='replace')
            require('CP0C_GPIO_TIMING_WORKER_COMPLETE '+name in text.splitlines(),'Native completion marker missing')
            w=[dict(case=name,message=l) for l in text.splitlines() if re.search(r'\[(WARNING|ERROR)\b',l)]
            warnings+=w
            require(not any('[ERROR' in x['message'] for x in w),'OpenROAD reported an error')
            units=(folder/'reports/units.rpt').read_text()
            require(re.search(r'\btime\s+1ns\b',units) and re.search(r'\bcapacitance\s+1pF\b',units),'Unexpected units')
            require(metrics(folder/'reports/physical.tsv')=={'signal_signature_unchanged':1,'saved_design_written':0},'Physical preservation failed')
            annotation[name]=audit_annotation(folder/'reports',source/'sweep/reports/unannotated.tsv')
            snap,vertices,paths=collect_case(folder,case)
            snapshots[name]=snap; all_vertices[name]=vertices
            remaining=stable_constraints(folder/'out/runtime.sdc')
            if stable is None: stable=remaining
            else: require(remaining==stable,'Unrelated constraints changed in diagnostic case')
            summary.append(dict(case=name,load_pf=case['load'],oe='dynamic' if case['oe'] is None else case['oe'],
                input_stimulus_ns=case['stimulus'],warning_count=len(w),internal_erc_findings=snap['internal_erc_findings'],
                check_setup_findings=bool(snap['check_setup']),**snap['metrics']))
            if 'prior' in case:
                comp=comparison_to_prior(source,case,snap,vertices); comparisons.append(comp)
                write_json(folder/'reports/fresh-vs-prior.json',comp)
                if name=='dynamic_15pf':
                    require(comp['metrics_match'] and comp['slew_vertices_match'] and not w and not snap['check_setup'],
                            'Original 15 pF control failed to reproduce')
                    require(snap['internal_erc_findings']==0 and not snap['clock_check_findings'],'Control has new internal or clock findings')
            print(f'  setup={int(snap["metrics"]["setup_violations"])} hold={int(snap["metrics"]["hold_violations"])} '
                  f'slew={int(snap["metrics"]["max_slew_violations"])} cap={int(snap["metrics"]["max_cap_violations"])}',flush=True)
        sensitivity=input_control(all_vertices['receive_fast_3pf'],all_vertices['receive_slow_3pf'])
        write_json(run/'reports/input-stimulus-control.json',sensitivity)
        write_json(run/'reports/fresh-vs-prior.json',comparisons)
        write_json(run/'reports/annotation.json',annotation)
        write_json(run/'reports/warnings.json',warnings)
        table(run/'reports/cases.tsv',list(summary[0]),summary)
        combined=[r for data in all_vertices.values() for r in data]
        table(run/'reports/vertices.tsv',list(combined[0]),combined)
        differences=any(not c['metrics_match'] or not c['slew_vertices_match'] for c in comparisons)
        status='GPIO_TIMING_DIAGNOSTICS_COMPLETE' if sensitivity['verified'] and not warnings else 'GPIO_TIMING_REVIEW_REQUIRED'
        write_json(run/'reports/conclusion.json',dict(status=status,workers=times,
            original_15pf_reproduced=True,all_cases_collected=True,input_stimulus_control_verified=sensitivity['verified'],
            fresh_vs_incremental_difference=differences,warning_count=len(warnings),
            physical_geometry_changed=False,original_inputs_preserved=True,accepted_constraint_change=False,
            approved_external_load_pf=None,cp0c='PENDING',candidate_promoted=False,
            next='Review fresh-process differences, receive stimuli and data/enable arc calculations before selecting a justified I/O model or implementation change.',
            scope='Diagnostic completion is not GPIO electrical closure, direction-turnaround verification, or CP0C signoff.'))
        (run/'status.txt').write_text(status+'\nCP0C_PENDING\n')
        code=0 if status=='GPIO_TIMING_DIAGNOSTICS_COMPLETE' else 2
    except (Exception,KeyboardInterrupt) as exc:
        message=f'{type(exc).__name__}: {exc}'
        if run:
            (run/'failure.txt').write_text(message+'\n')
            (run/'status.txt').write_text('GPIO_TIMING_STAGE_FAILED\nCP0C_PENDING\n')
        print('STOPPED: '+message,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed=[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
                write_json(run/'reports/preservation.json',dict(unchanged=not changed,changed=changed))
                if changed:
                    code=1; (run/'status.txt').write_text('GPIO_TIMING_PRESERVATION_FAILED\nCP0C_PENDING\n')
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                (run/'exit-status.txt').write_text(str(code)+'\n')
                print((run/'status.txt').read_text(),flush=True)
                print(f'Upload: {archive(run)}\nReports: {run}/reports',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True); lock.rmdir()
    return code

if __name__=='__main__':
    try: sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr); sys.exit(1)
