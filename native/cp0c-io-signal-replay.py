#!/usr/bin/env python3
"""Replay the completed native output/GPIO extractions; no new extraction."""
import argparse
from datetime import datetime
import importlib.util,json,os,platform,shutil,subprocess,sys,tempfile
from pathlib import Path
from cp0c_io_signal_support import db,require,sha,write_json,TARGETS,faults_for
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('signal_runner',HERE/'cp0c-io-signal.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

def validate_source(source,pin,checked):
    for name,h in pin['source_files'].items():
        rel=Path(name);require(not rel.is_absolute() and '..' not in rel.parts,'Unsafe source pin path')
        checked(source/rel,h)
    def read(rel):return json.loads((source/rel).read_text())
    require((source/'exit-status.txt').read_text().strip()=='2','Expected completed v1.1 review result')
    require(read('reports/preservation.json')=={'unchanged':True,'changed':[]},'Source preservation failed')
    final=read('reports/conclusion.json')
    require(final['status']=='IO_SIGNAL_REVIEW_REQUIRED' and final['targets']=={} and
            final['physical_jumper_controls_pass'] and final['failures']=={
                k:'RuntimeError: Unexpected physical device inventory before reduction' for k in TARGETS},
            'Source review result differs')
    previous=read('provenance.json')
    require(previous['version']==pin['source_version'] and previous['klayout']=='0.30.9' and
            previous['original_io_load_pf']==15 and not previous['source_geometry_changed'] and
            not previous['full_chip_lvs'] and not previous['candidate_promoted'],'Source provenance differs')
    for key,t in TARGETS.items():
        for variant in (('candidate','unmarked') if key=='gpio' else ('candidate',)):
            rel=Path('probes')/key/variant;row=read(rel/'extraction.json')
            require(row['milestone']=='complete' and not row['extraction_errors'] and row['net_joins']==0 and
                    row['implicit_connections']==[] and row['blackboxes']==[] and row['tap_extraction_enabled'],
                    'Extraction prerequisites differ: '+str(rel))
            require('CP0C_IO_SIGNAL_EXTRACTION_COMPLETE '+variant in (source/rel/'console.log').read_text(),
                    'Extraction completion marker missing')
            counts=dict(t['models'])
            if variant=='unmarked':counts.pop('rppd')
            witnesses=sum(v for k,v in counts.items() if k in ('sg13_lv_nmos','sg13_hv_nmos','ptap1','dantenna','rppd'))
            require(len(row['substrate_witnesses'])==witnesses and
                    {w['net'] for w in row['substrate_witnesses']}=={row['substrate_net_key']},
                    'Substrate witness coverage differs')
    return previous

def validate_result(final,comparisons):
    require(final['status']=='IO_SIGNAL_DIAGNOSTIC_MATCH_VERIFIED' and not final['failures'] and
            set(final['targets'])==set(TARGETS),'Signal replay gate failed')
    for key,t in TARGETS.items():
        r=final['targets'][key]
        contexts=['untranslated','unpropagated']+(['unmarked'] if key=='gpio' else [])
        expected={'translated':True,**{v:False for v in contexts+list(faults_for(key))}}
        require(r['status']=='IO_SIGNAL_PAD_DIAGNOSTIC_MATCH_VERIFIED' and r['outcomes']==expected and
                r['negative_controls_pass'] and r['fault_controls']==len(faults_for(key)) and
                r['context_controls']==len(contexts) and r['source_graphs_unchanged'],'Control coverage differs: '+key)
        p=json.loads((comparisons/key/'translated.json').read_text())
        nets=15 if key=='output' else 24
        require(p['raw_match'] and p['strict_match'] and p['circuits']==['Match'] and p['pair_counts']=={
            'devices':{'Match':t['devices']},'pins':{'Match':len(t['ports'])+1},'nets':{'Match':nets}},
            'Positive comparison coverage differs: '+key)
        for case in expected:
            x=json.loads((comparisons/key/(case+'.json')).read_text())
            require(x['raw_match']==x['strict_match']==(case=='translated'),'Raw control outcome differs: '+key+'/'+case)
    require(final['cp0c']=='PENDING' and not final['full_chip_lvs'] and not final['candidate_promoted'],
            'Replay scope differs')

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('signal_run',type=Path);args=ap.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Use the existing native Apple Silicon environment')
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    import klayout.dbcore as dbcore
    desc=subprocess.check_output(['file','-b','-L',dbcore.__file__],text=True).strip()
    require('Mach-O' in desc and 'arm64' in desc,'Python KLayout module must be native arm64')
    pin=json.loads((HERE/'cp0c-io-signal-replay-pins.json').read_text())
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results';source=args.signal_run.resolve()
    require(source.parent==results and source.name==pin['source_run'],'Use the completed October 22 09:00 signal run')
    lock=results/'cp0c.lock'
    try:lock.mkdir()
    except FileExistsError:
        owner=(lock/'owner.txt').read_text() if (lock/'owner.txt').is_file() else '(missing owner record)'
        raise RuntimeError(f'Another CP0C stage may be active; inspect {lock}/owner.txt\n{owner}') from None
    run=None;preserve={};code=1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=io-signal-replay\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-io-signal-replay-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for n in ('helpers','reports','logs','comparisons'):(run/n).mkdir()
        print(f'Evidence: {run}\nVersion: {runner.VERSION}\nComparison-only replay; no extraction; 60-second worker limit.',flush=True)
        require(shutil.disk_usage(run).free>=256*1024**2,'At least 256 MiB free disk required')
        def checked(path,expected=None):
            p=Path(path);require(p.is_file(),'Missing input: '+str(p));h=sha(p)
            require(expected is None or h==expected,'Input checksum differs: '+str(p));preserve[str(p.resolve())]=h;return p
        for name in runner.FILES:shutil.copy2(checked(HERE/name),run/'helpers'/name)
        checked(dbcore.__file__,pin['native_python_module_sha256'])
        previous=validate_source(source,pin,checked)
        write_json(run/'reports/source-validation.json',{'status':'PINNED_NATIVE_EXTRACTIONS_VERIFIED',
            'source_run':source.name,'files_verified':len(pin['source_files']),
            'completed_extractions':['output/candidate','gpio/candidate','gpio/unmarked']})
        write_json(run/'provenance.json',{'version':runner.VERSION,'source_run':str(source),'source_version':previous['version'],
            'mode':'comparison-only replay','klayout':db.__version__,'python':sys.version,'native_python_module':desc,
            'raw_inventory_fix':'Count individual driver fingers rather than partly combined devices',
            'original_io_load_pf':15,'source_geometry_changed':False,'new_extraction':False,'parameter_policy_changed':False,
            'net_joins':0,'implicit_connections':[],'blackboxes':[],'full_chip_lvs':False,'candidate_promoted':False,'cp0c':'PENDING'})
        write_json(run/'input.json',{'probes':str(source/'probes')})
        print('STAGE: replay strict reduction audits, both positive matches and all 62 negative controls',flush=True)
        seconds=runner.bounded([sys.executable,run/'helpers/cp0c-io-signal-worker.py','compare',
            run/'input.json',run/'comparisons'],run/'logs/compare.log',60)
        final=json.loads((run/'comparisons/conclusion.json').read_text())
        if final['status']=='IO_SIGNAL_DIAGNOSTIC_MATCH_VERIFIED':validate_result(final,run/'comparisons');code=0
        else:code=2
        final.update(mode='comparison-only replay',source_run=source.name,worker_seconds={'compare':seconds},
                     physical_jumper_controls_pass=True)
        write_json(run/'reports/conclusion.json',final);(run/'status.txt').write_text(final['status']+'\nCP0C_PENDING\n')
    except (Exception,KeyboardInterrupt) as exc:
        code=1;msg=f'{type(exc).__name__}: {exc}'
        if run:
            (run/'failure.txt').write_text(msg+'\n');(run/'status.txt').write_text('IO_SIGNAL_REPLAY_FAILED\nCP0C_PENDING\n')
        print('STOPPED: '+msg,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed=[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
                write_json(run/'reports/preservation.json',{'unchanged':not changed,'changed':changed})
                if changed:code=1;(run/'status.txt').write_text('IO_SIGNAL_REPLAY_PRESERVATION_FAILED\nCP0C_PENDING\n')
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                (run/'exit-status.txt').write_text(str(code)+'\n');(run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                print((run/'status.txt').read_text(),flush=True);print(f'Upload: {runner.archive(run)}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return code
if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
