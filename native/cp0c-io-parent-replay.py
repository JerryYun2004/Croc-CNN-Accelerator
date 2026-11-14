#!/usr/bin/env python3
"""Replay the completed v1.2 native I/O-parent databases without extraction."""
import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from cp0c_io_parent_support import db, require, sha, write_json, FAULTS

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('cp0p_runner',HERE/'cp0c-io-parent.py')
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

def validate_source(source,pin,checked):
    """Use the exact uploaded extraction evidence, including its original failure."""
    for name,digest in pin['source_files'].items():
        rel=Path(name)
        require(not rel.is_absolute() and '..' not in rel.parts,'Unsafe pin path')
        checked(source/rel,digest)
    def read(name):return json.loads((source/name).read_text())
    require((source/'exit-status.txt').read_text().strip()=='2','Expected the completed v1.2 comparison failure')
    require(read('reports/preservation.json')=={'unchanged':True,'changed':[]},'Source preservation failed')
    old=read('reports/conclusion.json')
    require(old['status']=='IO_PARENT_REVIEW_REQUIRED' and old['outcomes']=={'translated':False},
            'Source comparison result differs')
    provenance=read('provenance.json')
    require(provenance['version']=='1.2-net-api' and provenance['klayout']=='0.30.9',
            'Source tool/package version differs')
    require(provenance['original_io_load_pf']==15 and not provenance['source_geometry_changed'],
            'Source design provenance differs')
    for variant,count in (('original',5),('marker',6)):
        row=read('probes/'+variant+'/extraction.json')
        require(not row['extraction_errors'] and row['net_joins']==0 and
                row['implicit_connections']==[] and row['blackboxes']==[], 'Extraction prerequisites failed')
        audit=row['tap_reduction_audit']
        require(audit['status']=='ONLY_CHILD_PARALLEL_TAPS_REDUCED' and
                audit['raw_taps']==3 and audit['reduced_taps']==2 and
                audit['non_taps_unchanged'] and audit['pins_unchanged'] and
                audit['nets_unchanged'] and audit['net_joins']==0,'Tap reduction audit differs')
        witnesses=row['substrate_witnesses']
        require(len(witnesses)==count and {w['net'] for w in witnesses}=={row['substrate_net_key']},
                'Substrate witnesses differ')
        require('CP0C_IO_PARENT_EXTRACTION_COMPLETE '+variant in
                (source/'probes'/variant/'console.log').read_text(),'Extraction completion missing')
    return provenance

def validate_result(final,positive):
    expected={'translated':True,**{case:False for case in ('unmarked','untranslated','unpropagated',*FAULTS)}}
    require(final['status']=='IO_PARENT_DIAGNOSTIC_MATCH_VERIFIED' and final['outcomes']==expected and
            final['negative_controls_pass'] and final['negative_controls_meaningful'] and
            final['fault_controls']==14 and final['context_controls']==3 and final['source_graphs_unchanged'],
            'Replay comparison/control gate failed')
    require(positive['raw_match'] and positive['strict_match'] and positive['circuits']==['Match'] and
            positive['pair_counts']=={'devices':{'Match':9},'pins':{'Match':7},'nets':{'Match':9}},
            'Positive coverage incomplete')
    require(final['cp0c']=='PENDING' and not final['full_pad_lvs'] and not final['full_chip_lvs'],
            'Replay scope differs')

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('io_parent_run',type=Path);args=ap.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Use the existing native Apple Silicon environment')
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    import klayout.dbcore as dbcore
    desc=subprocess.check_output(['file','-b','-L',dbcore.__file__],text=True).strip()
    require('Mach-O' in desc and 'arm64' in desc,'Python KLayout module is not native arm64')
    pin=json.loads((HERE/'cp0c-io-parent-replay-pins.json').read_text())
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results';source=args.io_parent_run.resolve()
    require(source.parent==results and source.name==pin['source_run'],'Use the completed October 21 21:54 I/O-parent run')
    lock=results/'cp0c.lock'
    try:lock.mkdir()
    except FileExistsError:
        owner=(lock/'owner.txt').read_text() if (lock/'owner.txt').is_file() else '(missing owner record)'
        raise RuntimeError(f'Another CP0C stage may be active; inspect {lock}/owner.txt\n{owner}') from None
    run=None;preserve={};code=1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=io-parent-replay\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-io-parent-replay-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for name in ('helpers','reports','logs','comparisons'):(run/name).mkdir()
        print(f'Evidence: {run}\nVersion: {runner.VERSION}\nComparison-only replay; no extraction; 60-second worker limit.',flush=True)
        def checked(path,expected=None):
            p=Path(path);require(p.is_file(),'Missing input: '+str(p));digest=sha(p)
            require(expected is None or digest==expected,'Input checksum differs: '+str(p))
            preserve[str(p.resolve())]=digest;return p
        for name in runner.FILES:shutil.copy2(checked(HERE/name),run/'helpers'/name)
        checked(dbcore.__file__)
        previous=validate_source(source,pin,checked)
        write_json(run/'reports/source-validation.json',{'status':'PINNED_NATIVE_EXTRACTION_VERIFIED',
            'source_run':source.name,'files_verified':len(pin['source_files']),
            'original_extraction_complete':True,'marker_extraction_complete':True,
            'tap_reduction_verified':True,'substrate_witnesses_verified':True})
        write_json(run/'provenance.json',{'version':runner.VERSION,'source_run':str(source),
            'source_version':previous['version'],'mode':'comparison-only replay',
            'klayout':db.__version__,'python':sys.version,'native_python_module':desc,
            'original_io_load_pf':15,'source_geometry_changed':False,'new_extraction':False,
            'class_lookup':'exact names from active devices against mutable class enumeration',
            'parameter_policy_changed':False,'net_joins':0,'implicit_connections':[],
            'blackboxes':[],'full_chip_lvs':False,'candidate_promoted':False,'cp0c':'PENDING'})
        write_json(run/'input.json',{'probes':str(source/'probes')})
        print('STAGE: replay strict parent comparisons and all 17 negative controls',flush=True)
        seconds=runner.bounded([sys.executable,run/'helpers/cp0c-io-parent-worker.py',
            'compare',run/'input.json',run/'comparisons'],run/'logs/compare.log',60)
        final=json.loads((run/'comparisons/conclusion.json').read_text())
        positive=json.loads((run/'comparisons/translated.json').read_text())
        if final['status']=='IO_PARENT_DIAGNOSTIC_MATCH_VERIFIED':
            validate_result(final,positive);code=0
        else:code=2
        final.update(worker_seconds={'compare':seconds},mode='comparison-only replay',source_run=source.name)
        write_json(run/'reports/conclusion.json',final)
        (run/'status.txt').write_text(final['status']+'\nCP0C_PENDING\n')
    except (Exception,KeyboardInterrupt) as exc:
        code=1;msg=f'{type(exc).__name__}: {exc}'
        if run:
            (run/'failure.txt').write_text(msg+'\n')
            (run/'status.txt').write_text('IO_PARENT_REPLAY_FAILED\nCP0C_PENDING\n')
        print('STOPPED: '+msg,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed=[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
                write_json(run/'reports/preservation.json',{'unchanged':not changed,'changed':changed})
                if changed:
                    code=1;(run/'status.txt').write_text('IO_PARENT_REPLAY_PRESERVATION_FAILED\nCP0C_PENDING\n')
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                (run/'exit-status.txt').write_text(str(code)+'\n')
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                print((run/'status.txt').read_text(),flush=True)
                print(f'Upload: {runner.archive(run)}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return code

if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
