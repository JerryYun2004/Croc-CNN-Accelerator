#!/usr/bin/env python3
"""Run two bounded LevelDown extractions with the existing native Mac tools."""
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
from cp0c_io_parent_support import db,require,sha,write_json,TOP

VERSION='1.3-class-lookup'
FILES=('cp0c-io-parent.py','cp0c-io-parent.sh','cp0c-io-parent-worker.py','cp0c-io-parent.lvs',
       'cp0c_io_parent_support.py','cp0c-io-parent-pins.json','cp0c-io-parent-audit.rb',
       'cp0c-io-parent-replay.py','cp0c-io-parent-replay-pins.json')

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

def validate_context(ctx,pin,checked):
    """Validate native results semantically; do not invent native report hashes."""
    for name,h in pin['io_context_helpers'].items():checked(ctx/'helpers'/name,h)
    require(checked(ctx/'exit-status.txt').read_text().strip()=='0','I/O context runner failed')
    conclusion=json.loads(checked(ctx/'reports/conclusion.json').read_text())
    require(conclusion['status']=='IO_CONTEXT_DIAGNOSTICS_COMPLETE' and conclusion['fault_controls_pass'],
            'I/O context prerequisites did not pass')
    preservation=json.loads(checked(ctx/'reports/preservation.json').read_text())
    require(preservation=={'unchanged':True,'changed':[]},'Previous input preservation failed')
    sec=json.loads(checked(ctx/'secondary/secondary.json').read_text())
    require(sec['status']=='IO_SECONDARY_DIAGNOSTIC_MATCH_VERIFIED' and sec['fault_control_count']==12,
            'Protection-cell coverage differs')
    cases=('translated','baseline','spacing_only','tap_only','res_width','res_length','res_bends',
        'res_spacing','tap_area','tap_perimeter','missing_resistor','missing_tap','tap_polarity',
        'substrate_open','port_swap','diode_area')
    expected={n:n=='translated' for n in cases}
    require(sec['outcomes']==expected and sec['negative_controls_pass'],'Protection-cell controls incomplete')
    for name,want in expected.items():
        row=json.loads(checked(ctx/'secondary'/(name+'.json')).read_text())
        require(row['raw_match']==want and row['strict_match']==want,'Native control differs: '+name)
        if want:require(row['pairs']=={'devices':{'Match':4},'pins':{'Match':4},'nets':{'Match':5}},'Positive coverage differs')
    h=json.loads(checked(ctx/'reports/hierarchy.json').read_text());expected_h=pin['expected_hierarchy']
    require(h['io_masters']==29 and h['counts']==expected_h['counts'],'I/O hierarchy inventory differs')
    paths=h['substrate_context']['paths'];require(len(paths)==785,'Substrate trace is incomplete')
    for path in paths:
        require(path and path[-1]['parent']=='croc_chip' and path[-1]['parent_ports']==['VSS'],
                'Unexpected substrate endpoint')
        for a,b in zip(path,path[1:]):
            require(a['parent']==b['cell'] and a['parent_cluster']==b['local_cluster'],
                    'Discontinuous substrate trace')
    return json.loads(checked(ctx/'provenance.json').read_text())

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('io_context_run',type=Path);args=ap.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Use the existing native Apple Silicon environment')
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    import klayout.dbcore as dbcore
    module_desc=subprocess.check_output(['file','-b','-L',dbcore.__file__],text=True).strip()
    require('Mach-O' in module_desc and 'arm64' in module_desc,'Python KLayout module is not native arm64')
    here=Path(__file__).resolve().parent;pin=json.loads((here/'cp0c-io-parent-pins.json').read_text())
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results';ctx=args.io_context_run.resolve()
    require(ctx.parent==results and ctx.name==pin['io_context_run'],'Use the completed October 21 14:46 I/O-context run')
    lock=results/'cp0c.lock'
    try:lock.mkdir()
    except FileExistsError:
        owner=(lock/'owner.txt').read_text() if (lock/'owner.txt').is_file() else '(missing owner record)'
        raise RuntimeError(f'Another CP0C stage may be active; inspect {lock}/owner.txt\n{owner}') from None
    run=None;preserve={};code=1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=io-parent\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-io-parent-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for n in ('helpers','reports','logs','deck','input','probes','comparisons'):(run/n).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nTwo isolated extractions, one at a time; two threads; 120s per extraction.',flush=True)
        require(shutil.disk_usage(run).free>=1024**3,'At least 1 GiB free disk required')
        def checked(path,expected=None):
            p=Path(path);require(p.is_file(),'Missing input: '+str(p));digest=sha(p)
            require(expected is None or expected==digest,'Input checksum differs: '+str(p))
            preserve[str(p.resolve())]=digest;return p
        for name in FILES:shutil.copy2(checked(here/name),run/'helpers'/name)
        checked(dbcore.__file__)
        cp=validate_context(ctx,pin,checked)
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
        shutil.copy2(here/'cp0c-io-parent.lvs',run/'deck/cp0c-io-parent.lvs')
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
        write_json(run/'provenance.json',{'version':VERSION,'io_context_run':str(ctx),'source_run':str(source),
            'io_gds_sha256':pin['io_gds'],'klayout':db.__version__,'tool':str(tool),'tool_description':desc,
            'python':sys.version,'native_python_module':module_desc,'original_io_load_pf':15,
            'source_geometry_changed':False,'new_extraction':True,'extraction_top':TOP,
            'copy_geometry_change':'One PolyRes marker in SecondaryProtection only',
            'harness_boundary_port':'sub!','net_joins':0,'implicit_connections':[],'blackboxes':[],
            'full_chip_lvs':False,'candidate_promoted':False,'cp0c':'PENDING'})
        worker=run/'helpers/cp0c-io-parent-worker.py';times={}
        print('STAGE: prepare parent and reference fixtures',flush=True)
        times['prepare']=bounded([sys.executable,worker,'prepare',run/'input.json',run/'input'],run/'logs/prepare.log',60)
        prepared=json.loads((run/'input/prepared.json').read_text())
        for variant in ('original','marker'):
            dest=run/'probes'/variant;dest.mkdir()
            refs=prepared['references'] if variant=='marker' else {'translated':prepared['references']['translated']}
            config={'variant':variant,'output':str(dest),'result':str(dest/'extraction.json'),'references':refs,
                    'audit_helper':str(run/'helpers/cp0c-io-parent-audit.rb')}
            write_json(dest/'config.json',config)
            switches={'input':prepared[variant],'topcell':TOP,'report':str(dest/'extract.lvsdb'),
                'log':str(dest/'deck.log'),'target_netlist':str(dest/'extracted.cir'),
                'cp0p_config':str(dest/'config.json'),'run_mode':'deep','net_only':'true','no_simplify':'true',
                'top_lvl_pins':'true','disable_tap_extraction':'false','ignore_top_ports_mismatch':'false',
                'implicit_nets':'','combine_devices':'false','purge':'false','purge_nets':'false',
                'no_series_res':'true','no_parallel_res':'false'}
            write_json(dest/'switches.json',switches);argv=[tool]
            for k,v in switches.items():argv+=['-v',k+'='+v]
            argv.append(run/'deck/cp0c-io-parent.lvs')
            print('EXTRACTION: '+variant,flush=True)
            times[variant]=bounded(argv,dest/'console.log',120,env,dest)
            require('CP0C_IO_PARENT_EXTRACTION_COMPLETE '+variant in (dest/'console.log').read_text(),
                    'Native extraction completion marker missing')
            extraction=json.loads((dest/'extraction.json').read_text())
            require(not extraction['extraction_errors'] and extraction['net_joins']==0,'Extraction audit failed')
            require(extraction['tap_reduction_audit']['status']=='ONLY_CHILD_PARALLEL_TAPS_REDUCED',
                    'Tap reduction audit failed')
        print('STAGE: strict parent comparisons and fault controls',flush=True)
        times['compare']=bounded([sys.executable,worker,'compare',run/'input.json',run/'comparisons'],run/'logs/compare.log',60)
        final=json.loads((run/'comparisons/conclusion.json').read_text());final['worker_seconds']=times
        write_json(run/'reports/conclusion.json',final)
        (run/'status.txt').write_text(final['status']+'\nCP0C_PENDING\n');print((run/'status.txt').read_text(),flush=True)
        code=0 if final['status']=='IO_PARENT_DIAGNOSTIC_MATCH_VERIFIED' else 2
    except (Exception,KeyboardInterrupt) as exc:
        msg=f'{type(exc).__name__}: {exc}'
        if run:(run/'failure.txt').write_text(msg+'\n')
        print('STOPPED: '+msg,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed=[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
                write_json(run/'reports/preservation.json',{'unchanged':not changed,'changed':changed})
                if changed:code=1
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                (run/'exit-status.txt').write_text(str(code)+'\n');(run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                print(f'Upload: {archive(run)}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return code

if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
