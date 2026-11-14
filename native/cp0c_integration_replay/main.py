#!/usr/bin/env python3
"""Replay the saved native full-chip extraction without changing physical inputs."""
import argparse, json, os, platform, shutil, signal, subprocess, sys, tarfile, tempfile, time
from datetime import datetime
from pathlib import Path
from cp0c_integration_common import db, require, sha, write_json

VERSION = '1.1-structural-guidance'
FAULTS = ('io_wrong_supply', 'gpio_swap', 'sram_supply_open', 'missing_io_resistor')

def bounded(argv, log, timeout=600):
    start=time.monotonic(); peak=0; missing_samples=0
    with Path(log).open('w') as f:
        p=subprocess.Popen(list(map(str,argv)),stdout=f,stderr=subprocess.STDOUT,
            env=dict(os.environ,OMP_NUM_THREADS='2',PYTHONDONTWRITEBYTECODE='1'),start_new_session=True)
        try:
            while True:
                remaining=timeout-(time.monotonic()-start)
                if remaining<=0: raise TimeoutError(f'Worker exceeded {timeout}s; see {log}')
                try: rc=p.wait(timeout=min(10,remaining)); break
                except subprocess.TimeoutExpired:
                    rss=subprocess.run(['ps','-o','rss=','-p',str(p.pid)],capture_output=True,text=True,timeout=5)
                    if rss.returncode==0 and rss.stdout.strip():
                        peak=max(peak,int(rss.stdout.strip()))
                        missing_samples=0
                        require(peak<10*1024**2,'Worker exceeded 10 GiB RSS limit')
                    else:
                        missing_samples+=1
                        if platform.system()=='Darwin':
                            require(missing_samples<3,'Unable to sample native worker RSS with ps')
                    lines=Path(log).read_text(errors='replace').splitlines()
                    phase=next((s for s in reversed(lines) if s.startswith(('PHASE:','COMPARE:'))),'starting')
                    memory=f'{peak/1024:.0f} MiB' if peak else 'unavailable'
                    print(f'  {int(time.monotonic()-start)}s; sampled peak {memory}; {phase}',flush=True)
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL); p.wait()
            raise
    require(rc==0,f'Worker exited {rc}; see {log}')
    return {'seconds':round(time.monotonic()-start,3),'sampled_peak_rss_kib':peak or None,'limit_seconds':timeout}

def archive(run):
    dest=Path(str(run)+'.tar.gz'); pending=Path(str(dest)+'.partial')
    with tarfile.open(pending,'w:gz') as t:
        for p in sorted(run.rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts or p.suffix=='.lvsdb': continue
            t.add(p,arcname=str(Path(run.name)/p.relative_to(run)),recursive=False)
    os.replace(pending,dest)
    return dest

def validate_saved(ctx,pins,checked):
    require(ctx.name==pins['source_run'],'Use the completed extraction from '+pins['source_run'])
    for rel,digest in pins['files'].items(): checked(ctx/rel,digest)
    checked(ctx/'full/prepared.lvsdb',pins['prepared_sha256'])
    data=json.loads((ctx/'full/extraction.json').read_text())
    require(data['status']=='FULL_CHIP_EXTRACTION_COMPLETE' and data['new_extraction'] is True,
            'Full-chip extraction is incomplete')
    require(data['source_geometry_changed'] is False and data['blackboxes']==[] and
            data['net_joins']==0 and data['implicit_connections']==[],'Unexpected extraction scope')
    require(json.loads((ctx/'full/extraction-errors.json').read_text())==[],'Extraction errors are present')
    require(json.loads((ctx/'reports/preservation.json').read_text())=={'unchanged':True,'changed':[]},
            'Predecessor preservation failed')
    return json.loads((ctx/'provenance.json').read_text())

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('integration_run',type=Path);args=ap.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Use the existing native Apple Silicon environment')
    require(db.__version__=='0.30.9','Expected existing KLayout 0.30.9')
    import klayout.dbcore as dbcore
    desc=subprocess.check_output(['file','-b','-L',dbcore.__file__],text=True).strip()
    require('Mach-O' in desc and 'arm64' in desc,'KLayout Python module is not native arm64')
    here=Path(__file__).resolve().parent;pins=json.loads((here/'replay-pins.json').read_text())
    original_pins=json.loads((here/'cp0c-integration-pins.json').read_text())
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results';ctx=args.integration_run.resolve()
    require(ctx.parent==results,'Input must be an existing run under this repository native/results')
    require(ctx.is_dir(),'Input run is missing')
    lock=results/'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError:
        owner=(lock/'owner.txt').read_text() if (lock/'owner.txt').is_file() else '(no owner record)'
        raise RuntimeError(f'Another CP0C stage may be active; inspect {lock}/owner.txt\n{owner}') from None
    run=None;preserve={};code=1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=integration-replay\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-integration-replay-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for folder in ('helpers','reports','logs','comparisons','full'): (run/folder).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nComparison only: saved extraction; one worker at a time.',flush=True)
        require(shutil.disk_usage(run).free>=3*1024**3,'At least 3 GiB of free disk is required')
        def checked(path,digest=None):
            path=Path(path);require(path.is_file(),'Missing input: '+str(path));h=sha(path)
            require(digest is None or h==digest,'Input checksum differs: '+str(path))
            preserve[str(path.resolve())]=h;return path
        for p in sorted(here.iterdir()):
            if p.is_file() and p.suffix in ('.py','.json'): shutil.copy2(checked(p),run/'helpers'/p.name)
        checked(dbcore.__file__,original_pins['native_python_module_sha256'])
        prior=validate_saved(ctx,pins,checked)
        source=Path(prior['source_run']).resolve()
        require(source.parent==results and source.name==original_pins['source_run'],'Physical source lineage differs')
        require(prior['original_io_load_pf']==15,'Original I/O load differs')
        for rel,digest in pins['physical_files'].items(): checked(source/rel,digest)
        (run/'reports/prior').mkdir()
        for rel,digest in pins['acceptance_files'].items():
            path=Path(rel)
            require(not path.is_absolute() and '..' not in path.parts,'Unsafe acceptance input path')
            src=checked(results/path,digest)
            shutil.copy2(src,run/'reports/prior'/src.name)
        checked(root/'native/cp0c-integration-replay.sh')
        cfg={'full':str(ctx/'full'),'pins':str(run/'helpers/cp0c-integration-pins.json'),
             'output_database':str(run/'full/compared.lvsdb')}
        write_json(run/'input.json',cfg)
        write_json(run/'provenance.json',{'version':VERSION,'source_run':str(ctx),'physical_source':str(source),
            'prepared_sha256':pins['prepared_sha256'],'klayout':db.__version__,'native_module':desc,
            'new_extraction':False,'original_io_load_pf':15,'source_geometry_changed':False,
            'change':'Diode-tolerant search colors and whole clamp-chain hints; native tolerances and graph checks unchanged',
            'reference_scope':'Same annotated diagnostic reference as full-chip extraction',
            'blackboxes':[],'net_joins':0,'candidate_promoted':False,'cp0c':'PENDING'})
        times={};outcomes={};worker=run/'helpers/worker.py'
        for variant in ('baseline',)+FAULTS:
            out=run/'comparisons'/variant;out.mkdir()
            print('STAGE: '+variant,flush=True)
            times[variant]=bounded([sys.executable,worker,run/'input.json',out,variant],run/'logs'/(variant+'.log'))
            r=json.loads((out/'result.json').read_text())
            require(r['variant']==variant and r['full_chip_comparison'] and not r['new_extraction'],
                    'Unexpected worker result')
            outcomes[variant]={'raw_match':r['raw_match'],'strict_match':r['strict_match']}
            if variant=='baseline' and not r['strict_match']: break
        passed=(len(outcomes)==1+len(FAULTS) and outcomes['baseline']['strict_match'] and
                all(not outcomes[n]['raw_match'] and not outcomes[n]['strict_match'] for n in FAULTS))
        status='FULL_CHIP_DIAGNOSTIC_MATCH_VERIFIED' if passed else 'FULL_CHIP_REPLAY_REVIEW_REQUIRED'
        write_json(run/'reports/conclusion.json',{'status':status,'outcomes':outcomes,'workers':times,
            'fault_controls_complete':len(outcomes)==1+len(FAULTS),'new_extraction':False,
            'source_geometry_changed':False,'reference_scope':'Annotated diagnostic reference; not vendor-CDL signoff',
            'all_workers_exited_normally':True,'candidate_promoted':False,'cp0c':'PENDING',
            'remaining_acceptance_items':['Original 15 pF I/O slew/capacitance findings',
                'FEOL/density checks outside prior interconnect DRC scope',
                'Final-candidate timing coverage and baseline manifest review'],
            'next':'Review native results alongside existing timing/DRC evidence and annotation scope before baseline acceptance.'})
        (run/'status.txt').write_text(status+'\nCP0C_PENDING\n');code=0 if passed else 2
    except (Exception,KeyboardInterrupt) as exc:
        message=f'{type(exc).__name__}: {exc}'
        if run:
            (run/'failure.txt').write_text(message+'\n')
            (run/'status.txt').write_text('FULL_CHIP_REPLAY_STAGE_FAILED\nCP0C_PENDING\n')
        print('STOPPED: '+message,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed=[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
                write_json(run/'reports/preservation.json',{'unchanged':not changed,'changed':changed})
                if changed:
                    code=1;(run/'status.txt').write_text('REPLAY_PRESERVATION_FAILED\nCP0C_PENDING\n')
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                (run/'exit-status.txt').write_text(str(code)+'\n')
                print((run/'status.txt').read_text(),flush=True)
                print(f'Upload: {archive(run)}\nComparison database: {run}/full/compared.lvsdb',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return code

if __name__=='__main__':
    try: sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
