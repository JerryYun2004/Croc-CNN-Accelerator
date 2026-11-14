#!/usr/bin/env python3
"""Replay reviewed I/O cell checks and inventory remaining hierarchy boundaries."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from cp0c_io_context_support import db,require,sha,write_json

VERSION='1.0-io-cell-context'
FILES=('cp0c-io-context.py','cp0c-io-context.sh','cp0c-io-context-worker.py',
       'cp0c_io_context_support.py','cp0c-io-context-pins.json')

def bounded(argv, log, timeout):
    start=time.monotonic()
    with Path(log).open('w') as f:
        p=subprocess.Popen(list(map(str,argv)),stdout=f,stderr=subprocess.STDOUT,
            env=dict(os.environ,OMP_NUM_THREADS='2'),start_new_session=True)
        try:
            while True:
                remaining=timeout-(time.monotonic()-start)
                if remaining<=0: raise TimeoutError(f'Worker exceeded {timeout}s; see {log}')
                try: rc=p.wait(timeout=min(20,remaining)); break
                except subprocess.TimeoutExpired:
                    print(f'  Running ({int(time.monotonic()-start)}s); log: {log}',flush=True)
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL); p.wait()
            raise
    require(rc==0,f'Worker exited {rc}; see {log}')
    return round(time.monotonic()-start,3)

def archive(run):
    dest=run.with_suffix('.tar.gz')
    with tarfile.open(dest,'w:gz') as tar:
        for p in sorted(run.rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                tar.add(p,arcname=str(Path(run.name)/p.relative_to(run)),recursive=False)
    return dest

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('context_run',type=Path);args=ap.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Use the existing native Apple Silicon environment')
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    import klayout.dbcore as dbcore
    native=subprocess.check_output(['file','-b','-L',dbcore.__file__],text=True).strip()
    require('Mach-O' in native and 'arm64' in native,'KLayout Python module must contain native arm64 code')
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results'; here=Path(__file__).resolve().parent; ctx=args.context_run.resolve()
    pin=json.loads((here/'cp0c-io-context-pins.json').read_text())
    require(ctx.parent==results and ctx.name==pin['context_run'],'Use the reviewed October 21 14:05 SRAM-context run')
    lock=results/'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError:
        owner=(lock/'owner.txt').read_text() if (lock/'owner.txt').is_file() else '(owner file missing)'
        raise RuntimeError(f'Another CP0C stage may be active. Lock: {lock}\nOwner: {owner.strip()}\nInspect its process before recovering this lock.') from None
    run=None; preserve={}; code=1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=io-context\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-io-context-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for name in ('helpers','logs','reports','secondary'): (run/name).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nOne worker at a time; cell limit 30s; hierarchy limit 180s.',flush=True)
        require(shutil.disk_usage(run).free>=1024**3,'At least 1 GiB free disk space required')
        def checked(path, expected=None):
            p=Path(path); require(p.is_file(),'Missing input: '+str(p)); digest=sha(p)
            require(expected is None or digest==expected,'Input checksum differs: '+str(p))
            preserve[str(p.resolve())]=digest;return p
        for name in FILES: shutil.copy2(checked(here/name),run/'helpers'/name)
        checked(dbcore.__file__)
        for rel,h in pin['context_evidence'].items(): checked(ctx/rel,h)
        prior=json.loads((ctx/'reports/conclusion.json').read_text())
        require(prior['status']=='SRAM_MACRO_DIAGNOSTIC_MATCH_VERIFIED' and prior['negative_controls_pass'],
                'Native SRAM diagnostic prerequisite did not pass')
        cp=json.loads((ctx/'provenance.json').read_text()); source=Path(cp['source_run']).resolve()
        require(source.parent==results and source.name==pin['source_run'],'Unexpected SRAM repair source')
        prov=json.loads(checked(source/'provenance.json').read_text())
        require(prov['original_io_load_pf']==15,'Original I/O-load prerequisite changed')
        require(json.loads(checked(source/'reports/result.json').read_text())['sram_power_repair']=='SRAM_PG_REPAIR_VERIFIED',
                'Supply-repair prerequisite did not pass')
        full=checked(source/'full/layout.lvsdb',pin['source_database'])
        for rel,h in pin['lvs_deck'].items(): checked(source/'deck/lvs'/rel,h)
        probe=results/pin['probe_run']
        for rel,h in pin['probe_evidence'].items(): checked(probe/rel,h)
        pp=json.loads((probe/'provenance.json').read_text())
        original=Path(prov['original_lvs']).resolve()
        require(original.parent==results and original==Path(pp['source']).resolve(), 'Probe and repaired source lineage disagree')
        io=checked(original/'input/io.cdl',pin['io_cdl'])
        config={'full_database':str(full),'io_cdl':str(io),
            'marker_gds':str(probe/'input/marker-only.gds'),
            'marker_database':str(probe/'io-marker-only/layout.lvsdb'),
            'original_database':str(probe/'io-original/layout.lvsdb')}
        write_json(run/'input.json',config)
        write_json(run/'provenance.json',{'version':VERSION,'context_run':str(ctx),'source_run':str(source),
            'probe_run':str(probe),'source_database_sha256':pin['source_database'],
            'python':sys.version,'klayout':db.__version__,'native_module':native,
            'geometry_changes':0,'new_extraction':False,'blackboxes':[],
            'implicit_connections':[],'full_chip_comparison':False,'candidate_promoted':False,
            'original_io_load_pf':15,'cp0c':'PENDING'})
        worker=run/'helpers/cp0c-io-context-worker.py';times={}
        print('STAGE: secondary compatibility and fault controls',flush=True)
        times['secondary']=bounded([sys.executable,worker,'secondary',run/'input.json',run/'secondary'],run/'logs/secondary.log',30)
        cell=json.loads((run/'secondary/secondary.json').read_text())
        print(cell['status'],flush=True)
        require(cell['status']=='IO_SECONDARY_DIAGNOSTIC_MATCH_VERIFIED','Positive comparison incomplete; inspect secondary evidence')
        print('STAGE: I/O hierarchy boundary inventory',flush=True)
        times['hierarchy']=bounded([sys.executable,worker,'hierarchy',run/'input.json',run/'reports'],run/'logs/hierarchy.log',180)
        hierarchy=json.loads((run/'reports/hierarchy.json').read_text())
        expected=pin['expected_hierarchy']
        require(hierarchy['io_masters']==expected['io_masters'] and hierarchy['counts']==expected['counts'],
                'Hierarchy coverage differs from reviewed source; inspect hierarchy.json')
        require(hierarchy['substrate_context']['path_count']==expected['substrate_paths'],
                'Substrate path coverage differs from reviewed source')
        write_json(run/'reports/conclusion.json',{'status':'IO_CONTEXT_DIAGNOSTICS_COMPLETE',
            'secondary':cell['status'],'fault_controls_pass':cell['negative_controls_pass'],
            'hierarchy':hierarchy['status'],'io_masters':hierarchy['io_masters'],
            'worker_seconds':times,'full_chip_lvs':'NOT_RERUN','cp0c':'PENDING',
            'next':'Review split-pin and substrate traces before a fresh I/O parent extraction.'})
        (run/'status.txt').write_text('IO_CONTEXT_DIAGNOSTICS_COMPLETE\nIO_SECONDARY_DIAGNOSTIC_MATCH_VERIFIED\nIO_HIERARCHY_REVIEW_REQUIRED\nCP0C_PENDING\n')
        print((run/'status.txt').read_text(),flush=True);code=0
    except (Exception,KeyboardInterrupt) as exc:
        message=f'{type(exc).__name__}: {exc}'
        if run: (run/'failure.txt').write_text(message+'\n')
        print('STOPPED: '+message,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed=[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
                write_json(run/'reports/preservation.json',{'unchanged':not changed,'changed':changed})
                if changed: code=1
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                (run/'exit-status.txt').write_text(str(code)+'\n')
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                print(f'Upload: {archive(run)}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return code

if __name__=='__main__':
    try: sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
