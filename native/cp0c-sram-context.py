#!/usr/bin/env python3
"""Audit physical SRAM context, then replay bounded macro comparisons on Mac."""
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
from cp0c_sram_context_support import require,sha,write_json

VERSION='1.0-array-context'
TIMEOUT=300
FILES=('cp0c-sram-context.py','cp0c-sram-context.sh','cp0c-sram-context-worker.py',
       'cp0c_sram_context_support.py','cp0c-sram-context-pins.json')

def bounded(argv, log, env, timeout=TIMEOUT):
    started=time.monotonic()
    with Path(log).open('w') as f:
        p=subprocess.Popen(list(map(str,argv)),stdout=f,stderr=subprocess.STDOUT,
                           env=env,start_new_session=True)
        try:
            while True:
                remain=timeout-(time.monotonic()-started)
                if remain<=0:raise TimeoutError(f'Worker exceeded {timeout}s; see {log}')
                try:rc=p.wait(timeout=min(20,remain));break
                except subprocess.TimeoutExpired:
                    print(f'  Running ({int(time.monotonic()-started)}s); log: {log}',flush=True)
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
            raise
    require(rc==0,f'Worker exited {rc}; see {log}')

def conclusion(results):
    require('translated' in results,'Missing translated comparison')
    if not results['translated']:
        return {'status':'SRAM_MACRO_REVIEW_REQUIRED','translated_macro_match':False,
                'negative_controls_meaningful':False,'cp0c':'PENDING'}
    require('baseline' in results and results['baseline'] is False,'Baseline missing or unexpectedly matches')
    controls=('wrong_width','wrong_layer','open')
    require(all(k in results for k in controls),'Missing post-match controls')
    ok=all(results[k] is False for k in controls)
    return {'status':'SRAM_MACRO_DIAGNOSTIC_MATCH_VERIFIED' if ok else 'SRAM_MACRO_CONTROL_REVIEW_REQUIRED',
            'translated_macro_match':True,'negative_controls_meaningful':True,
            'negative_controls_pass':ok,'cp0c':'PENDING'}

def archive(run):
    out=run.with_suffix('.tar.gz')
    with tarfile.open(out,'w:gz') as tar:
        for p in sorted(run.rglob('*')):
            if p.is_file() and p.suffix not in ('.lvsdb','.netlist') and '__pycache__' not in p.parts:
                tar.add(p,arcname=str(Path(run.name)/p.relative_to(run)),recursive=False)
    return out

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('bitcell_run',type=Path);args=ap.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Use the existing native Apple Silicon environment')
    import klayout.db as db
    import klayout.dbcore as dbcore
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    desc=subprocess.check_output(['file','-b','-L',dbcore.__file__],text=True).strip()
    require('Mach-O' in desc and 'arm64' in desc,'KLayout Python module must contain native arm64 code')
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results';bitcell=args.bitcell_run.resolve();here=Path(__file__).resolve().parent
    pin=json.loads((here/'cp0c-sram-context-pins.json').read_text())
    require(bitcell.parent==results and bitcell.name==pin['bitcell_run'],'Use the reviewed October 21 10:11 bitcell run')
    lock=results/'cp0c.lock'
    try:lock.mkdir()
    except FileExistsError:raise RuntimeError(f'Another CP0C stage may be active; inspect {lock}/owner.txt') from None
    run=None;preserve={};exit_code=1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=sram-context\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-sram-context-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for n in ('helpers','reports','logs','comparisons'):(run/n).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nOne worker at a time; preparation limit 180s; comparison limit 300s.',flush=True)
        require(shutil.disk_usage(run).free>=4*1024**3,'At least 4 GiB free disk space required')
        def checked(path,expected=None):
            p=Path(path);require(p.is_file(),'Missing input: '+str(p));h=sha(p)
            require(expected is None or h==expected,'Input checksum differs: '+str(p))
            preserve[str(p.resolve())]=h;return p
        for name in FILES:shutil.copy2(checked(here/name),run/'helpers'/name)
        for rel,digest in pin['bitcell_evidence'].items():checked(bitcell/rel,digest)
        bc=json.loads((bitcell/'reports/conclusion.json').read_text())
        require(bc['status']=='SRAM_BITCELL_BOUNDARY_REVIEW_REQUIRED' and bc['resistor_controls_pass'],
                'Isolated bitcell prerequisite differs')
        source=Path(json.loads((bitcell/'provenance.json').read_text())['source_run']).resolve()
        require(source.parent==results and source.name==pin['source_run'],'Wrong repaired SRAM source')
        prov=json.loads(checked(source/'provenance.json').read_text())
        prior=json.loads(checked(source/'reports/result.json').read_text())
        require(prior['sram_power_repair']=='SRAM_PG_REPAIR_VERIFIED' and prov['original_io_load_pf']==15,
                'SRAM supply repair / original I/O load prerequisite differs')
        database=checked(source/'full/layout.lvsdb',pin['source_database'])
        gds_run=Path(prov['original_gds']).resolve();original=Path(prov['original_lvs']).resolve()
        require(gds_run.parent==results and original.parent==results,'Source provenance points outside this repository')
        gds=checked(gds_run/'input/RM_IHPSG13_1P_512x32_c2_bm_bist.gds',pin['sram_gds'])
        checked(original/'input/sram.cdl',pin['sram_cdl'])
        for rel,h in pin['lvs_deck'].items():checked(source/'deck/lvs'/rel,h)
        checked(dbcore.__file__)
        config={'database':str(database),'sram_gds':str(gds),'prepared':str(run/'reports'),
                'bitcell_database':str(bitcell/'probes/bitcell/translated/layout.lvsdb')}
        write_json(run/'input.json',config)
        write_json(run/'provenance.json',{'version':VERSION,'source_run':str(source),'bitcell_run':str(bitcell),
            'source_database_sha256':pin['source_database'],'python':sys.version,'klayout':db.__version__,
            'native_module':desc,'extraction_reused':True,'new_extraction_performed':False,
            'geometry_changes':0,'implicit_connections':[],'blackboxes':[],
            'original_io_load_pf':15,'full_chip_comparison':False,'cp0c':'PENDING'})
        env=dict(os.environ,OMP_NUM_THREADS='2')
        worker=run/'helpers/cp0c-sram-context-worker.py'
        def invoke(mode,dest):
            print('STAGE: '+mode,flush=True)
            bounded([sys.executable,worker,mode,run/'input.json',dest],run/'logs'/f'{mode}.log',env,
                    timeout=180 if mode=='prepare' else TIMEOUT)
        invoke('prepare',run/'reports')
        require(json.loads((run/'reports/prepared.json').read_text())['status']=='SRAM_CONTEXT_PREPARED','Preparation incomplete')
        outcomes={}
        for variant in ('translated','baseline','wrong_width','wrong_layer','open'):
            if variant!='translated' and not outcomes.get('translated'):break
            dest=run/'comparisons'/variant;dest.mkdir()
            invoke(variant,dest)
            result=json.loads((dest/'result.json').read_text());audit=json.loads((dest/'graph-audit.json').read_text())
            require(audit['status']=='SAVED_MACRO_GRAPHS_AUDITED' and audit['strict_match']==result['strict_match'],
                    'Saved macro audit differs')
            outcomes[variant]=result['strict_match'];write_json(run/'reports/outcomes.json',outcomes)
            print(f"  {variant}: strict_match={result['strict_match']} ({result['compare_seconds']:.1f}s comparison)",flush=True)
            require(not (variant=='baseline' and result['strict_match']),'Baseline unexpectedly passed')
        final=conclusion(outcomes);write_json(run/'reports/conclusion.json',final)
        (run/'status.txt').write_text(final['status']+'\nCP0C_PENDING\n');print((run/'status.txt').read_text(),flush=True)
        exit_code=2 if final['status']=='SRAM_MACRO_CONTROL_REVIEW_REQUIRED' else 0
    except (Exception,KeyboardInterrupt) as exc:
        message=f'{type(exc).__name__}: {exc}'
        if run:
            (run/'failure.txt').write_text(message+'\n')
            if isinstance(exc,TimeoutError):(run/'status.txt').write_text('SRAM_CONTEXT_TIMEOUT\nCP0C_PENDING\n')
        print('STOPPED: '+message,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed=[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
                write_json(run/'reports/preservation.json',{'unchanged':not changed,'changed':changed})
                if changed:exit_code=1
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                (run/'exit-status.txt').write_text(str(exit_code)+'\n')
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                print(f'Upload: {archive(run)}\nReplay netlists retained in: {run}/comparisons',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return exit_code

if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
