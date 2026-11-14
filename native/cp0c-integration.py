#!/usr/bin/env python3
"""Fresh full-chip extraction and annotated LVS integration on native macOS."""
import argparse,json,os,platform,shutil,signal,subprocess,sys,tarfile,tempfile,time
from datetime import datetime
from pathlib import Path
from cp0c_integration_common import db,require,sha,write_json

VERSION='1.0-full-chip-integration'
FILES=('cp0c-integration.py','cp0c-integration.sh','cp0c-integration-worker.py','cp0c-integration.lvs',
 'cp0c_integration_prepare.py','cp0c_integration_compare.py','cp0c_integration_common.py',
 'cp0c_integration_context.py','cp0c_integration_sram.py','cp0c-integration-pins.json','cp0c-integration-io.json',
 'cp0c-integration-reference.lvs')
FAULTS=('io_wrong_supply','gpio_swap','sram_supply_open','missing_io_resistor')

def bounded(argv,log,timeout,env=None,cwd=None):
    start=time.monotonic();peak=0
    with Path(log).open('w') as f:
        p=subprocess.Popen(list(map(str,argv)),stdout=f,stderr=subprocess.STDOUT,env=env or dict(os.environ,OMP_NUM_THREADS='2'),
            cwd=cwd,start_new_session=True)
        try:
            while True:
                remain=timeout-(time.monotonic()-start)
                if remain<=0:raise TimeoutError(f'Worker exceeded {timeout}s; see {log}')
                try:rc=p.wait(timeout=min(10,remain));break
                except subprocess.TimeoutExpired:
                    rss=subprocess.run(['ps','-o','rss=','-p',str(p.pid)],capture_output=True,text=True,timeout=5)
                    if rss.returncode==0 and rss.stdout.strip():
                        peak=max(peak,int(rss.stdout.strip()));require(peak<10*1024**2,'Worker exceeded the 10 GiB RSS limit')
                    print(f'  Running ({int(time.monotonic()-start)}s, sampled peak {peak/1024:.0f} MiB); log: {log}',flush=True)
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
            raise
    require(rc==0,f'Worker exited {rc}; see {log}')
    return {'seconds':round(time.monotonic()-start,3),'sampled_peak_rss_kib':peak,'limit_seconds':timeout}

def archive(run):
    path=run.with_suffix('.tar.gz')
    with tarfile.open(path,'w:gz') as t:
        for p in sorted(run.rglob('*')):
            r=p.relative_to(run)
            if not p.is_file() or r.parts[0]=='deck' or '__pycache__' in r.parts:continue
            if p.name.endswith(('.gds.gz','.lvsdb','.cir','.cdl')):continue
            t.add(p,arcname=str(Path(run.name)/r),recursive=False)
    return path

def validate_fill(ctx,pin,checked):
    for name,h in pin['fill_files'].items():
        p=Path(name);require(not p.is_absolute() and '..' not in p.parts,'Unsafe prerequisite path');checked(ctx/p,h)
    def read(name):return json.loads((ctx/name).read_text())
    require((ctx/'exit-status.txt').read_text().strip()=='0','Filler/corner stage did not pass')
    require(read('reports/preservation.json')=={'unchanged':True,'changed':[]},'Prerequisite preservation failed')
    final=read('reports/conclusion.json')
    require(final['status']=='IO_FILL_DIAGNOSTIC_MATCH_VERIFIED' and not final['failures'] and
        not final['full_chip_lvs'] and not final['candidate_promoted'],'Prerequisite conclusion differs')
    require(set(final['targets'])=={'filler1000','filler400','filler200','corner'},'Incomplete filler/corner coverage')
    for r in final['targets'].values():
        outcomes=r['outcomes'];require(len(outcomes)==36 and all(v is (k=='boundary') for k,v in outcomes.items()) and
            r['negative_controls']==35 and r['source_graphs_unchanged'],'Filler/corner controls differ')
    return read('provenance.json')

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('fill_run',type=Path);args=ap.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Use the existing native Apple Silicon environment')
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    import klayout.dbcore as dbcore
    module_desc=subprocess.check_output(['file','-b','-L',dbcore.__file__],text=True).strip()
    require('Mach-O' in module_desc and 'arm64' in module_desc,'KLayout Python module must be native arm64')
    here=Path(__file__).resolve().parent;pin=json.loads((here/'cp0c-integration-pins.json').read_text())
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results';ctx=args.fill_run.resolve()
    require(ctx.parent==results and ctx.name==pin['fill_run'],'Use the completed October 22 13:40 filler/corner run')
    lock=results/'cp0c.lock'
    try:lock.mkdir()
    except FileExistsError:
        owner=(lock/'owner.txt').read_text() if (lock/'owner.txt').is_file() else '(no owner record)'
        raise RuntimeError(f'Another CP0C stage may be active; inspect {lock}/owner.txt\n{owner}') from None
    run=None;preserve={};code=1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=integration\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-integration-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for n in ('helpers','reports','logs','deck','input','full','comparisons'):(run/n).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nOne process at a time; two extraction threads; 10 GiB sampled RSS limit.',flush=True)
        require(shutil.disk_usage(run).free>=5*1024**3,'At least 5 GiB free disk required')
        def checked(path,expected=None):
            p=Path(path);require(p.is_file(),'Missing input: '+str(p));h=sha(p)
            require(expected is None or expected==h,'Input checksum differs: '+str(p));preserve[str(p.resolve())]=h;return p
        for name in FILES:shutil.copy2(checked(here/name),run/'helpers'/name)
        checked(here/'cp0c-integration-io.json',pin['io_annotations'])
        checked(dbcore.__file__,pin['native_python_module_sha256'])
        prior=validate_fill(ctx,pin,checked);source=Path(prior['source_run']).resolve()
        require(source.parent==results and source.name==pin['source_run'],'Source lineage differs')
        sp=json.loads(checked(source/'provenance.json').read_text())
        require(sp['original_io_load_pf']==prior['original_io_load_pf']==15,'Original I/O constraints differ')
        original=Path(sp['original_lvs']).resolve();gdsrun=Path(sp['original_gds']).resolve()
        require(original.parent==results and gdsrun.parent==results,'Input paths leave repository')
        database=checked(source/'full/layout.lvsdb',pin['source_database'])
        gds=checked(source/'out/croc.gds.gz',pin['source_gds'])
        reference=checked(original/'input/reference.cdl',pin['reference_cdl'])
        checked(gdsrun/'input/sg13cmos5l_io.gds',pin['io_gds'])
        sram_gds=checked(gdsrun/'input/RM_IHPSG13_1P_512x32_c2_bm_bist.gds',pin['sram_gds'])
        for rel,h in pin['lvs_deck'].items():
            src=checked(source/'deck/lvs'/rel,h);dst=run/'deck'/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
        shutil.copy2(here/'cp0c-integration.lvs',run/'deck/cp0c-integration.lvs')
        shutil.copy2(here/'cp0c-integration-reference.lvs',run/'deck/cp0c-integration-reference.lvs')
        base=Path.home()/'tools/croc-klayout-0.30.9';tool=checked(base/'install/strmrun',pin['strmrun'])
        require(os.access(tool,os.X_OK),'strmrun is not executable')
        desc=subprocess.check_output(['file','-b','-L',str(tool)],text=True).strip()
        require('Mach-O' in desc and 'arm64' in desc,'strmrun must be native arm64')
        manifest=checked(base/'runtime.sha256').read_text().splitlines();require(manifest,'Empty runtime manifest')
        for line in manifest:
            digest,name=line.split('  ',1);p=Path(name);checked(p if p.is_absolute() else base/p,digest)
        env=dict(os.environ,DYLD_LIBRARY_PATH=str(tool.parent),OMP_NUM_THREADS='2')
        cfg={'pins':str(run/'helpers/cp0c-integration-pins.json'),'annotations':str(run/'helpers/cp0c-integration-io.json'),
            'database':str(database),'gds':str(gds),'sram_gds':str(sram_gds),'reference':str(reference),'full':str(run/'full')}
        write_json(run/'input.json',cfg)
        write_json(run/'provenance.json',{'version':VERSION,'fill_run':str(ctx),'source_run':str(source),'klayout':db.__version__,
            'tool':str(tool),'tool_description':desc,'native_python_module':module_desc,'original_io_load_pf':15,
            'source_geometry_changed':False,'extraction_copy_changes':'27 PolyRes bodies on layer 128/0 in two IO masters only',
            'reference_scope':'Previously tested IO geometry annotations, explicit substrate boundary and SRAM resistor models/widths',
            'new_full_chip_extraction_requested':True,'implicit_connections':[],'blackboxes':[],
            'candidate_promoted':False,'cp0c':'PENDING'})
        worker=run/'helpers/cp0c-integration-worker.py';times={}
        print('STAGE: audit saved chip boundaries; prepare full-chip diagnostic inputs',flush=True)
        times['prepare']=bounded([sys.executable,worker,'prepare',run/'input.json',run/'input'],run/'logs/prepare.log',300)
        require('CP0C_INTEGRATION_PREPARED' in (run/'logs/prepare.log').read_text(),'Preparation completion marker missing')
        print('PREFLIGHT: parse the complete annotated reference with the pinned native reader',flush=True)
        parser=[tool,'-v','cp0i_reference='+str(run/'input/reference.cdl'),
            '-v','cp0i_ledger='+str(run/'input/reference-annotations.json'),
            '-v','cp0i_report='+str(run/'reports/reference-parser.json'),run/'deck/cp0c-integration-reference.lvs']
        times['reference_parser']=bounded(parser,run/'logs/reference-parser.log',60,env,run/'input')
        require('CP0C_INTEGRATION_REFERENCE_READY' in (run/'logs/reference-parser.log').read_text(),'Reference parser completion marker missing')
        extract={'top':'croc_chip','reference':str(run/'input/reference.cdl'),'output':str(run/'full')}
        write_json(run/'full/config.json',extract)
        sw={'input':str(run/'input/diagnostic.gds.gz'),'topcell':'croc_chip','report':str(run/'full/extract.lvsdb'),
            'log':str(run/'full/deck.log'),'target_netlist':str(run/'full/extracted.cir'),
            'cp0i_config':str(run/'full/config.json'),'run_mode':'deep','net_only':'true','no_simplify':'true','top_lvl_pins':'false',
            'disable_tap_extraction':'false','ignore_top_ports_mismatch':'false','implicit_nets':'',
            'combine_devices':'false','purge':'false','purge_nets':'false','no_series_res':'true','no_parallel_res':'false'}
        write_json(run/'full/switches.json',sw);argv=[tool]
        for k,v in sw.items():argv+=['-v',k+'='+v]
        argv.append(run/'deck/cp0c-integration.lvs')
        print('EXTRACTION: complete croc_chip; maximum 20 minutes',flush=True)
        times['extract']=bounded(argv,run/'full/console.log',1200,env,run/'full')
        require('CP0C_INTEGRATION_EXTRACTION_COMPLETE' in (run/'full/console.log').read_text(),'Extraction completion marker missing')
        print('COMPARE: all standard-cell masters, both SRAM banks, integrated IO and chip wiring',flush=True)
        times['baseline']=bounded([sys.executable,worker,'baseline',run/'input.json',run/'comparisons/baseline'],run/'logs/baseline.log',600)
        baseline=json.loads((run/'comparisons/baseline/result.json').read_text());controls={}
        if baseline['strict_match']:
            for mode in FAULTS:
                print('FAULT CONTROL: '+mode,flush=True)
                times[mode]=bounded([sys.executable,worker,mode,run/'input.json',run/'comparisons'/mode],run/'logs'/(mode+'.log'),600)
                result=json.loads((run/'comparisons'/mode/'result.json').read_text())
                controls[mode]=not result['raw_match'] and not result['strict_match']
        passed=baseline['strict_match'] and len(controls)==len(FAULTS) and all(controls.values())
        status='FULL_CHIP_DIAGNOSTIC_MATCH_VERIFIED' if passed else 'FULL_CHIP_INTEGRATION_REVIEW_REQUIRED'
        write_json(run/'reports/conclusion.json',{'status':status,'baseline_strict_match':baseline['strict_match'],
            'fault_controls':controls,'fault_controls_complete':len(controls)==len(FAULTS),
            'new_full_chip_extraction':True,'full_chip_comparison':True,'workers':times,
            'reference_scope':'Annotated diagnostic reference, not vendor-CDL signoff','candidate_promoted':False,'cp0c':'PENDING'})
        (run/'status.txt').write_text(status+'\nCP0C_PENDING\n');code=0 if passed else 2
    except (Exception,KeyboardInterrupt) as exc:
        message=f'{type(exc).__name__}: {exc}'
        if run:
            (run/'failure.txt').write_text(message+'\n');(run/'status.txt').write_text('FULL_CHIP_INTEGRATION_STAGE_FAILED\nCP0C_PENDING\n')
        print('STOPPED: '+message,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed=[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
                write_json(run/'reports/preservation.json',{'unchanged':not changed,'changed':changed})
                if changed:code=1;(run/'status.txt').write_text('INTEGRATION_PRESERVATION_FAILED\nCP0C_PENDING\n')
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                (run/'exit-status.txt').write_text(str(code)+'\n');(run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                print((run/'status.txt').read_text(),flush=True)
                print(f'Upload: {archive(run)}\nLarge extraction files retained in: {run}/full',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return code

if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
