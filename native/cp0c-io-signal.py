#!/usr/bin/env python3
"""Run three bounded output/GPIO harness extractions with the existing native Mac tools."""
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
from cp0c_io_signal_support import db,require,sha,write_json,TARGETS

VERSION='1.2-raw-fingers'
FILES=('cp0c-io-signal.py','cp0c-io-signal.sh','cp0c-io-signal-worker.py','cp0c-io-signal.lvs',
       'cp0c_io_signal_support.py','cp0c-io-signal-pins.json',
       'cp0c-io-signal-replay.py','cp0c-io-signal-replay-pins.json')

def bounded(argv,log,timeout,env=None,cwd=None):
    start=time.monotonic()
    with Path(log).open('w') as f:
        p=subprocess.Popen(list(map(str,argv)),stdout=f,stderr=subprocess.STDOUT,
            env=env or dict(os.environ,OMP_NUM_THREADS='2'),cwd=cwd,start_new_session=True)
        try:
            while True:
                remain=timeout-(time.monotonic()-start)
                if remain<=0:raise TimeoutError(f'Worker exceeded {timeout}s; see {log}')
                try:rc=p.wait(timeout=min(20,remain));break
                except subprocess.TimeoutExpired:print(f'  Running ({int(time.monotonic()-start)}s); log: {log}',flush=True)
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
            raise
    require(rc==0,f'Worker exited {rc}; see {log}')
    return round(time.monotonic()-start,3)

def archive(run):
    path=run.with_suffix('.tar.gz')
    with tarfile.open(path,'w:gz') as tar:
        for p in sorted(run.rglob('*')):
            rel=p.relative_to(run)
            if p.is_file() and rel.parts[0]!='deck' and '__pycache__' not in rel.parts:
                tar.add(p,arcname=str(Path(run.name)/rel),recursive=False)
    return path

def validate_pad(ctx,pin,checked):
    for name,digest in pin['pad_files'].items():checked(ctx/name,digest)
    require((ctx/'exit-status.txt').read_text().strip()=='0','Input-pad prerequisite failed')
    final=json.loads((ctx/'reports/conclusion.json').read_text())
    require(final['status']=='IO_PAD_DIAGNOSTIC_MATCH_VERIFIED' and final['negative_controls_pass']
            and final['fault_controls']==22 and final['context_controls']==3
            and final['physical_jumper_control_pass'],'Input-pad prerequisite differs')
    require(json.loads((ctx/'reports/preservation.json').read_text())=={'unchanged':True,'changed':[]},
            'Input-pad preservation failed')
    return json.loads((ctx/'provenance.json').read_text())

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('input_pad_run',type=Path);args=ap.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Use the existing native Apple Silicon environment')
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    import klayout.dbcore as dbcore
    module_desc=subprocess.check_output(['file','-b','-L',dbcore.__file__],text=True).strip()
    require('Mach-O' in module_desc and 'arm64' in module_desc,'Python KLayout module is not native arm64')
    here=Path(__file__).resolve().parent;pin=json.loads((here/'cp0c-io-signal-pins.json').read_text())
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results';ctx=args.input_pad_run.resolve()
    require(ctx.parent==results and ctx.name==pin['pad_run'],'Use the completed October 21 23:46 input-pad run')
    lock=results/'cp0c.lock'
    try:lock.mkdir()
    except FileExistsError:
        owner=(lock/'owner.txt').read_text() if (lock/'owner.txt').is_file() else '(missing owner record)'
        raise RuntimeError(f'Another CP0C stage may be active; inspect {lock}/owner.txt\n{owner}') from None
    run=None;preserve={};code=1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=io-signal\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-io-signal-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for n in ('helpers','reports','logs','deck','input','probes','comparisons'):(run/n).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nThree isolated extractions, one at a time; two threads; 120s per extraction.',flush=True)
        require(shutil.disk_usage(run).free>=1024**3,'At least 1 GiB free disk required')
        def checked(path,expected=None):
            p=Path(path);require(p.is_file(),'Missing input: '+str(p));digest=sha(p)
            require(expected is None or expected==digest,'Input checksum differs: '+str(p))
            preserve[str(p.resolve())]=digest;return p
        for name in FILES:shutil.copy2(checked(here/name),run/'helpers'/name)
        checked(dbcore.__file__)
        cp=validate_pad(ctx,pin,checked)
        source=Path(cp['source_run']).resolve()
        require(source.parent==results and source.name==pin['source_run'],'Repaired source lineage differs')
        sp=json.loads(checked(source/'provenance.json').read_text())
        require(cp['original_io_load_pf']==sp['original_io_load_pf']==15,'Original I/O load provenance differs')
        checked(source/'full/layout.lvsdb',pin['source_database'])
        original=Path(sp['original_lvs']).resolve();gds_run=Path(sp['original_gds']).resolve()
        require(original.parent==results and gds_run.parent==results,'Source paths leave this repository')
        io=checked(gds_run/'input/sg13cmos5l_io.gds',pin['io_gds'])
        checked(original/'input/io.cdl',pin['io_cdl'])
        reader=checked(original/'input/io-reader.cdl',pin['io_reader_cdl'])
        for rel,h in pin['lvs_deck'].items():
            src=checked(source/'deck/lvs'/rel,h);dst=run/'deck'/rel
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
        shutil.copy2(here/'cp0c-io-signal.lvs',run/'deck/cp0c-io-signal.lvs')
        base=Path.home()/'tools/croc-klayout-0.30.9';tool=checked(base/'install/strmrun',pin['strmrun'])
        require(os.access(tool,os.X_OK),'strmrun is not executable')
        desc=subprocess.check_output(['file','-b','-L',str(tool)],text=True).strip()
        require('Mach-O' in desc and 'arm64' in desc,'strmrun must be native arm64')
        manifest=checked(base/'runtime.sha256').read_text().splitlines();require(manifest,'Empty runtime manifest')
        for line in manifest:
            digest,name=line.split('  ',1);p=Path(name);checked(p if p.is_absolute() else base/p,digest)
        for name in ('bd','db','pex','tl','gsi','lib','rdb','lym','rba','drc','lvs','pya'):
            require((tool.parent/f'libklayout_{name}.0.dylib').is_file(),'Missing runtime library '+name)
        env=dict(os.environ,DYLD_LIBRARY_PATH=str(tool.parent),OMP_NUM_THREADS='2')
        cfg={'io_gds':str(io),'io_reader_cdl':str(reader),'probes':str(run/'probes')}
        write_json(run/'input.json',cfg)
        write_json(run/'provenance.json',{'version':VERSION,'input_pad_run':str(ctx),'source_run':str(source),
            'io_gds_sha256':pin['io_gds'],'klayout':db.__version__,'tool':str(tool),'tool_description':desc,
            'python':sys.version,'native_python_module':module_desc,'original_io_load_pf':15,
            'source_geometry_changed':False,'new_extraction':True,'extraction_tops':{k:v['top'] for k,v in TARGETS.items()},
            'copy_geometry_change':'GPIO child PolyRes marker; external TopMetal1 IOVSS and IOVDD jumpers in disposable wrappers',
            'harness_boundary_port':'sub!','net_joins':0,'physical_harness_connection':'external IOVSS and IOVDD metal jumpers','implicit_connections':[],'blackboxes':[],
            'full_chip_lvs':False,'candidate_promoted':False,'cp0c':'PENDING'})
        worker=run/'helpers/cp0c-io-signal-worker.py';times={}
        print('STAGE: prepare output/GPIO pads, physical supply jumpers and reference fixtures',flush=True)
        times['prepare']=bounded([sys.executable,worker,'prepare',run/'input.json',run/'input'],run/'logs/prepare.log',60)
        prepared=json.loads((run/'input/prepared.json').read_text())
        for key,t in prepared.items():
            for variant in (('candidate','unmarked') if key=='gpio' else ('candidate',)):
                dest=run/'probes'/key/variant;dest.mkdir(parents=True)
                refs=t['references'] if variant=='candidate' else {'translated':t['references']['translated']}
                models=dict(t['models'])
                if variant=='unmarked':models.pop('rppd')
                witnesses=sum(v for k,v in models.items() if k in ('sg13_lv_nmos','sg13_hv_nmos','ptap1','dantenna','rppd'))
                config={'variant':variant,'top':t['top'],'ports':t['ports'],
                        'output':str(dest),'result':str(dest/'extraction.json'),'references':refs,
                        'expected_models':models,'expected_witnesses':witnesses}
                write_json(dest/'config.json',config)
                switches={'input':t[variant],'topcell':t['top'],'report':str(dest/'extract.lvsdb'),
                    'log':str(dest/'deck.log'),'target_netlist':str(dest/'extracted.cir'),
                    'cp0d_config':str(dest/'config.json'),'run_mode':'deep','net_only':'true','no_simplify':'true',
                    'top_lvl_pins':'true','disable_tap_extraction':'false','ignore_top_ports_mismatch':'false',
                    'implicit_nets':'','combine_devices':'false','purge':'false','purge_nets':'false',
                    'no_series_res':'true','no_parallel_res':'false'}
                write_json(dest/'switches.json',switches);argv=[tool]
                for k,v in switches.items():argv+=['-v',k+'='+v]
                argv.append(run/'deck/cp0c-io-signal.lvs')
                print('EXTRACTION: '+key+'/'+variant,flush=True)
                times[key+'/'+variant]=bounded(argv,dest/'console.log',120,env,dest)
                require('CP0C_IO_SIGNAL_EXTRACTION_COMPLETE '+variant in (dest/'console.log').read_text(),
                        'Native extraction completion marker missing')
                extraction=json.loads((dest/'extraction.json').read_text())
                require(not extraction['extraction_errors'] and extraction['net_joins']==0,'Extraction audit failed')
        print('STAGE: audit parallel reductions; strict comparisons and fault controls',flush=True)
        times['compare']=bounded([sys.executable,worker,'compare',run/'input.json',run/'comparisons'],run/'logs/compare.log',60)
        final=json.loads((run/'comparisons/conclusion.json').read_text());final['worker_seconds']=times
        final['physical_jumper_controls_pass']=all(t['geometry']['physical_jumper_controls_pass'] for t in prepared.values())
        write_json(run/'reports/conclusion.json',final)
        (run/'status.txt').write_text(final['status']+'\nCP0C_PENDING\n')
        code=0 if final['status']=='IO_SIGNAL_DIAGNOSTIC_MATCH_VERIFIED' else 2
    except (Exception,KeyboardInterrupt) as exc:
        msg=f'{type(exc).__name__}: {exc}'
        if run:
            (run/'failure.txt').write_text(msg+'\n')
            (run/'status.txt').write_text('IO_SIGNAL_STAGE_FAILED\nCP0C_PENDING\n')
        print('STOPPED: '+msg,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed=[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
                write_json(run/'reports/preservation.json',{'unchanged':not changed,'changed':changed})
                if changed:
                    code=1
                    (run/'status.txt').write_text('IO_SIGNAL_PRESERVATION_FAILED\nCP0C_PENDING\n')
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                (run/'exit-status.txt').write_text(str(code)+'\n');(run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                print((run/'status.txt').read_text(),flush=True)
                print(f'Upload: {archive(run)}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return code

if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
