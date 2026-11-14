#!/usr/bin/env python3
"""Reproduce GPIO disable-slew behavior in a fresh one-pad timing fixture."""
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
from base_support import require, sha, write_json, table
from runtime import bounded, archive, tcl_quote
from model import read_model, bounds, compare_report
from support import CASES, collect, gate

VERSION='1.0-single-pad-model'

def prior_audit(source,models):
    records=[]
    for gpio in range(32):
        for corner in ('tt','ff'):
            for kind,kinds in [('data',['data']),('enable',['enable','disable'])]:
                rel=f'cases/dynamic_3pf/reports/arcs/gpio{gpio}_io-{corner}-{kind}-max.rpt'
                data=compare_report(models[corner],(source/rel).read_text(),kinds)
                records += [dict(gpio=gpio,corner=corner,**r) for r in data]
    require(len(records)==384,'Prior GPIO coverage differs')
    require(all(r['within_characterized_domain'] for r in records),'Prior 3 pF sample needs extrapolation review')
    return records

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('gpio_timing_run',type=Path); args=ap.parse_args()
    require(sys.version_info>=(3,11),'Use the existing Python 3.11+ environment')
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Run on the existing native Apple Silicon Mac')
    here=Path(__file__).resolve().parent; pins=json.loads((here/'pins.json').read_text())
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results'; source=args.gpio_timing_run.resolve()
    require(source.parent==results and source.is_dir(),'Input must be the reviewed GPIO timing directory in this repository native/results')
    lock=results/'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError:
        owner=(lock/'owner.txt').read_text() if (lock/'owner.txt').is_file() else '(no owner record)'
        raise RuntimeError(f'Another CP0C stage may be active; inspect {lock}/owner.txt\n{owner}') from None
    run=None; preserve={}; code=1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=pad-model\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-pad-model-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for d in ('helpers','reports','reference','cases','logs'): (run/d).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nNine fresh one-pad processes, one at a time; two threads; 60 seconds per process.',flush=True)
        require(shutil.disk_usage(run).free>=512*1024**2,'At least 512 MiB free disk is required')
        def checked(path,digest=None):
            path=Path(path); require(path.is_file(),f'Missing input: {path}')
            h=sha(path); require(digest is None or h==digest,f'Input checksum differs: {path}')
            preserve[str(path.resolve())]=h; return path
        for p in sorted(here.iterdir()):
            if p.is_file(): shutil.copy2(checked(p),run/'helpers'/p.name)
        for rel,digest in pins['source_files'].items():
            p=checked(source/rel,digest); dest=run/'reference'/rel
            dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dest)
        prior=json.loads((source/'reports/conclusion.json').read_text())
        require(prior['status']=='GPIO_TIMING_DIAGNOSTICS_COMPLETE' and prior['all_cases_collected'] and
                prior['input_stimulus_control_verified'] and prior['original_inputs_preserved'], 'GPIO prerequisite is incomplete')
        require(json.loads((source/'reports/preservation.json').read_text())=={'unchanged':True,'changed':[]},'Prior inputs changed')
        tool=checked(Path.home()/('tools/croc-openroad-'+pins['openroad_pin'])/'install/bin/openroad',pins['openroad_sha256'])
        description=subprocess.check_output(['file','-b','-L',str(tool)],text=True).strip()
        require('Mach-O' in description and 'arm64' in description,'OpenROAD is not a native arm64 executable')
        libs={c:checked(root/'ihp13/pdk/lib'/v['name'],v['sha256']) for c,v in pins['libraries'].items()}
        tech=checked(root/'technology/lef/sg13cmos5l_tech.lef',pins['lef']['sg13cmos5l_tech.lef'])
        io=checked(root/'technology/lef/sg13cmos5l_io.lef',pins['lef']['sg13cmos5l_io.lef'])
        print('STAGE: independent Liberty table audit and replay of 32 GPIOs at TT/FF',flush=True)
        models={c:read_model(p) for c,p in libs.items()}
        for c,m in models.items(): write_json(run/'reports'/f'{c}-model.json',m)
        limits=[dict(corner=c,**r) for c,m in models.items() for r in bounds(m)]
        table(run/'reports/table-bounds.tsv',limits)
        old=prior_audit(source,models); table(run/'reports/prior-oracle.tsv',old)
        write_json(run/'reports/prior-oracle.json',dict(verified=True,gpios=32,corners=['tt','ff'],arcs=len(old),
            comparisons=2*len(old),max_delay_error_ns=max(r['delay_error_ns'] for r in old),
            max_slew_error_ns=max(r['slew_error_ns'] for r in old)))
        write_json(run/'provenance.json',dict(version=VERSION,source=str(source),openroad_pin=pins['openroad_pin'],
            opensta_pin=pins['opensta_pin'],io_pdk_pin=pins['io_pdk_pin'],openroad_native=description,
            cases=CASES,io_libraries_only=True,external_pad_input_slew_ns=.2,core_receiver_load_pf=.05,
            wire_capacitance_pf=0,extracted_rc=False,virtual_clock_ns=20,
            physical_design_loaded=False,physical_design_written=False,accepted_constraint_change=False,
            libraries_modified=False,cp0c='PENDING'))
        times={}; snapshots={}; warnings=[]; all_arcs=[]; summary=[]
        for case in CASES:
            name=case['name']; folder=run/'cases'/name; folder.mkdir()
            config=folder/'config.tcl'
            lines=[f'set ::pm_lib({c}) {tcl_quote(p)}' for c,p in libs.items()]
            lines += [f'set ::pm_tech {tcl_quote(tech)}',f'set ::pm_io_lef {tcl_quote(io)}',
                      f'set ::pm_slew {case["slew"]}',f'set ::pm_load {case["load"]}',f'set ::pm_oe {case["oe"]}']
            config.write_text('\n'.join(lines)+'\n'); write_json(folder/'configuration.json',case)
            env=dict(os.environ,OMP_NUM_THREADS='2',PM_CONFIG=str(config),PM_HELPERS=str(run/'helpers'),PM_CASE=name)
            print('STAGE: '+name,flush=True); log=run/'logs'/(name+'.log')
            times[name]=bounded([tool,'-no_init','-exit',run/'helpers/worker.tcl'],folder,env,log,60)
            text=log.read_text(errors='replace')
            require('CP0C_PAD_MODEL_WORKER_COMPLETE '+name in text.splitlines(),'Native completion marker missing')
            warnings += [dict(case=name,message=line) for line in text.splitlines() if re.search(r'\[(WARNING|ERROR)\b',line)]
            require('[ERROR' not in text,'OpenROAD reported an error')
            result=collect(folder,case,models); snapshots[name]=result
            write_json(folder/'reports/parsed.json',result)
            all_arcs += [dict(case=name,**r) for r in result['arcs']]
            summary.append(dict(case=name,load_pf=case['load'],input_slew_ns=case['slew'],oe=case['oe'],
                tt_erc_rows=sum(r['corner']=='tt' for r in result['erc']),ff_erc_rows=sum(r['corner']=='ff' for r in result['erc']),
                oracle_arcs=len(result['arcs']),check_setup_has_findings=bool(result['check_setup'].strip())))
            print(f'  TT violations={summary[-1]["tt_erc_rows"]}; FF violations={summary[-1]["ff_erc_rows"]}; oracle arcs={len(result["arcs"])}',flush=True)
        table(run/'reports/fixture-oracle.tsv',all_arcs); table(run/'reports/cases.tsv',summary)
        controls=gate(snapshots,models); write_json(run/'reports/controls.json',controls)
        write_json(run/'reports/warnings.json',warnings)
        lower_bound=all(r['min_above_limit'] for r in limits if r['corner']=='tt' and r['arc']=='disable')
        status='PAD_DISABLE_MODEL_REPRODUCED' if controls['verified'] and lower_bound and not warnings else 'PAD_MODEL_REVIEW_REQUIRED'
        write_json(run/'reports/conclusion.json',dict(status=status,workers=times,prior_native_arcs_verified=len(old),
            fixture_arcs_verified=len(all_arcs),controls_verified=controls['verified'],warning_count=len(warnings),
            tt_disable_table_minima_above_limit=lower_bound,all_cases_collected=True,
            high_z_waveform_physically_validated=False,physical_design_changed=False,
            accepted_constraint_change=False,approved_external_load_pf=None,full_chip_sta_rerun=False,
            cp0c='PENDING',candidate_promoted=False,
            next='Review the model evidence and define I/O load, transition and bus-turnaround requirements. Any model correction or scoped exception needs justification before a fresh full-chip run.',
            scope='Numerical model reproduction only. No transistor simulation, package/board analysis, model correction, timing waiver, or CP0C signoff.'))
        shutil.copy2(run/'helpers/io-requirements.md',run/'reports/io-requirements.md')
        (run/'status.txt').write_text(status+'\nCP0C_PENDING\n'); code=0 if status=='PAD_DISABLE_MODEL_REPRODUCED' else 2
    except (Exception,KeyboardInterrupt) as exc:
        message=f'{type(exc).__name__}: {exc}'
        if run:
            (run/'failure.txt').write_text(message+'\n')
            (run/'status.txt').write_text('PAD_MODEL_STAGE_FAILED\nCP0C_PENDING\n')
        print('STOPPED: '+message,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed=[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
                write_json(run/'reports/preservation.json',dict(unchanged=not changed,changed=changed))
                if changed:
                    code=1; (run/'status.txt').write_text('PAD_MODEL_PRESERVATION_FAILED\nCP0C_PENDING\n')
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
