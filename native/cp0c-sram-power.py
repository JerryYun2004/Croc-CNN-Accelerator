#!/usr/bin/env python3
"""Create and verify a separate SRAM supply-via ECO candidate; never promote it."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import traceback

from cp0c_sram_power_support import (DEF_SHA,ODB_SHA,SDC_SHA,GDS_SHA,SRAM,
    require,sha,write_json,plan,emit_plan,audit_def_delta)

VERSION='1.1-exact-instance-names'
OPENROAD_PIN='c463e998615210dc322c999fc9a9f56885dbd664'

def read_json(path):return json.loads(Path(path).read_text())

def command(argv,cwd,env,log):
    print('+ '+shlex.join(map(str,argv)),flush=True)
    with Path(log).open('w') as f:
        p=subprocess.Popen(list(map(str,argv)),cwd=cwd,env=env,text=True,
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        try:
            for line in p.stdout:
                print(line,end='',flush=True);f.write(line);f.flush()
            rc=p.wait()
        except BaseException:
            p.terminate()
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:p.kill();p.wait()
            raise
    require(rc==0,f'Command failed with exit {rc}; see {log}')

def archive(run):
    target=run.with_suffix('.tar.gz')
    with tarfile.open(target,'w:gz') as tar:
        for p in sorted(run.rglob('*')):
            if not p.is_file():continue
            rel=p.relative_to(run)
            if rel.parts[0] in ('input','out','deck'):continue
            if '__pycache__' in rel.parts:continue
            if p.suffix in ('.lvsdb','.cir','.lyrdb'):continue
            tar.add(p,arcname=str(Path(run.name)/rel),recursive=False)
    return target

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('lvs_run',type=Path);args=ap.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Native Apple Silicon macOS is required')
    import klayout.db as db
    require(db.__version__=='0.30.9','Use the completed KLayout 0.30.9 venv')
    root=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results=root/'native/results';source=args.lvs_run.resolve()
    require(source.parent==results and source.name.startswith('cp0c-lvs-'),'Use the original completed LVS run in native/results')
    here=Path(__file__).resolve().parent
    pins=read_json(here/'cp0c-sram-power-pins.json')
    lock=results/'cp0c.lock'
    try:lock.mkdir()
    except FileExistsError:raise RuntimeError(f'Another CP0C command may be running; inspect {lock}/owner.txt') from None
    run=None;preserve={};ok=False
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=sram-power\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-sram-power-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        print(f'Evidence: {run}\nVersion: {VERSION}\nOne physical-verification process at a time; two threads.',flush=True)
        for name in ('helpers','reports','logs','input','out','deck','full'):(run/name).mkdir()
        for p in here.glob('cp0c*sram*'):
            if p.is_file():shutil.copy2(p,run/'helpers'/p.name)
        helper=run/'helpers';worker=helper/'cp0c_sram_power_support.py'
        def check(p,h):
            p=Path(p);require(p.is_file(),f'Missing input: {p}')
            require(sha(p)==h,f'Checksum differs: {p}')
            preserve[str(p.resolve())]=h;return p
        def copy_checked(src,dst,h):
            check(src,h);dst=Path(dst);dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(src,dst);require(sha(dst)==h,f'Copy differs: {dst}');return dst
        provenance=read_json(source/'provenance.json')
        require(provenance.get('version')=='1.2-legacy-cdl','Use the reviewed completed LVS 1.2 run')
        require(provenance.get('gds_sha256')==GDS_SHA and provenance.get('original_io_load_pf')==15,'LVS provenance differs')
        gds=Path(provenance['gds_run']);internal=Path(provenance['internal_run'])
        require(gds.parent==results and internal.parent==results,'Source runs must be in this repository')
        original=internal/'eco/out'
        source_def=check(original/'croc_internal.def',DEF_SHA)
        source_odb=check(original/'croc_internal.odb',ODB_SHA)
        copy_checked(original/'croc_internal.sdc',run/'input/croc_internal.sdc',SDC_SHA)
        copy_checked(original/'croc_internal_lvs.v',run/'input/croc_internal_lvs.v',pins['reference_verilog'])
        original_gds=check(gds/'out/croc.gds.gz',GDS_SHA)
        seal=check(gds/'out/seal_ring.gds.gz',pins['seal'])
        reference=copy_checked(source/'input/reference.cdl',run/'input/reference.cdl',pins['reference_cdl'])
        for group,base in (('gds_helpers',gds),('lvs_helpers',source)):
            for name,h in pins[group].items():copy_checked(base/'helpers'/name,helper/name,h)
        for rel,h in pins['drc_deck'].items():copy_checked(gds/'deck/drc'/rel,run/'deck/drc'/rel,h)
        copy_checked(gds/'deck/tech_mod.json',run/'deck/tech_mod.json',pins['tech_mod'])
        for rel,h in pins['lvs_deck'].items():copy_checked(source/'deck'/rel,run/'deck/lvs'/rel,h)
        assembly=read_json(gds/'reports/assembly-inputs.json')
        require(Path(assembly['defpath'])==source_def,'Assembly provenance does not use the reviewed DEF')
        require(len(assembly['lefs'])==11 and len(assembly['gdss'])==4,'Assembly source inventory differs')
        require({Path(p).name for p in assembly['gdss']}==set(pins['gdss']),'GDS libraries differ')
        for p in assembly['lefs']:check(p,pins['lefs'][Path(p).name])
        for p in assembly['gdss']:check(p,pins['gdss'][Path(p).name])
        check(assembly['layer_map'],pins['layer_map'])
        lef=next(Path(p) for p in assembly['lefs'] if Path(p).name==SRAM+'.lef')
        sram_gds=next(Path(p) for p in assembly['gdss'] if Path(p).name==SRAM+'.gds')
        toolbase=Path.home()/'tools/croc-klayout-0.30.9'
        tool=check(toolbase/'install/strmrun',pins['strmrun'])
        openroad=check(Path.home()/f'tools/croc-openroad-{OPENROAD_PIN}/install/bin/openroad',pins['openroad'])
        # Audit the completed native runtime without rebuilding or changing it.
        for line in (toolbase/'runtime.sha256').read_text().splitlines():
            h,name=line.split('  ',1);p=Path(name)
            check(p if p.is_absolute() else toolbase/p,h)
        sys.path.insert(0,str(helper))
        from cp0c_lvs_runtime import klayout_environment,native_binary
        from cp0c_gds_support import summarize_rdb
        write_json(run/'reports/tools.json',{'openroad':native_binary(openroad),'strmrun':native_binary(tool),'python':sys.executable,'klayout':db.__version__})
        env=dict(os.environ);env['OMP_NUM_THREADS']='2'
        kenv=klayout_environment(tool,env)
        p=plan(source_def,lef);emit_plan(p,run/'reports')
        write_json(run/'provenance.json',{'version':VERSION,'original_lvs':str(source),'original_gds':str(gds),
            'original_internal':str(internal),'openroad_pin':OPENROAD_PIN,'klayout':'0.30.9',
            'original_io_load_pf':15,'sdc_sha256':SDC_SHA,'source_def_sha256':DEF_SHA,
            'source_odb_sha256':ODB_SHA,'source_gds_sha256':GDS_SHA,
            'reference_cdl_sha256':pins['reference_cdl'],'arrays':288,'cuts':2592,
            'lvs_model_changes':[],'circuit_blackboxes':[],'implicit_supply_connections':[],
            'source_runs_modified':False,'cp0c_status':'PENDING'})
        before=run/'input/before.def';after=run/'out/croc_sram_power.def'
        odb=run/'out/croc_sram_power.odb'
        eenv=dict(env,CP0P_ODB=str(source_odb),CP0P_BEFORE_DEF=str(before),
                  CP0P_PLAN=str(run/'reports/placements.tsv'),CP0P_AFTER_DEF=str(after),CP0P_AFTER_ODB=str(odb))
        command([openroad,'-exit',helper/'cp0c-sram-power.tcl'],run,eenv,run/'logs/openroad.log')
        require('CP0C_SRAM_POWER_ECO_COMPLETE arrays=288 cuts=2592' in (run/'logs/openroad.log').read_text(),'Native via insertion did not complete')
        write_json(run/'reports/def-delta.json',audit_def_delta(before,after,p))
        print('DEF_DELTA_VERIFIED: cells, signals and previous power wires unchanged.',flush=True)
        assembly.update(defpath=str(after),out=str(run/'out/croc.gds.gz'),report=str(run/'reports/assembly.json'))
        write_json(run/'reports/assembly-inputs.json',assembly)
        command([sys.executable,helper/'cp0c-gds-worker.py','assemble',run/'reports/assembly-inputs.json'],run,env,run/'logs/assembly.log')
        command([sys.executable,worker,'gds-audit',original_gds,run/'out/croc.gds.gz',run/'reports/plan.json',run/'reports/gds-delta.json'],run,env,run/'logs/gds-delta.log')
        print('GDS_DELTA_VERIFIED: exactly the planned vias; metal enclosures contained in existing conductors.',flush=True)
        merge={'chip_path':str(run/'out/croc.gds.gz'),'seal_path':str(seal),'out':str(run/'out/croc.sealed.gds.gz'),
               'report':str(run/'reports/seal.json'),'sram_reference':str(sram_gds)}
        write_json(run/'reports/merge-inputs.json',merge)
        command([sys.executable,helper/'cp0c-gds-worker.py','merge',run/'reports/merge-inputs.json'],run,env,run/'logs/seal.log')
        drc=dict(pins['drc_settings'],input=str(run/'out/croc.sealed.gds.gz'),report=str(run/'reports/interconnect.lyrdb'),
                 log=str(run/'logs/drc-deck.log'),drc_json_default=str(run/'deck/drc/rule_decks/sg13cmos5l_tech_default.json'),drc_json=str(run/'deck/tech_mod.json'))
        write_json(run/'reports/drc-switches.json',drc)
        argv=[tool]
        for k,v in drc.items():argv+=['-v',f'{k}={v}']
        command(argv+[run/'deck/drc/ihp-sg13cmos5l.drc'],run,kenv,run/'logs/drc-console.log')
        drc_result=summarize_rdb(run/'reports/interconnect.lyrdb',run/'reports/drc-counts.tsv',run/'reports/drc-samples.json')
        write_json(run/'reports/drc-summary.json',drc_result)
        require(drc_result['markers']==0,'Interconnect DRC found violations; candidate not accepted')
        print('INTERCONNECT_DRC_PASS',flush=True)
        full=run/'full'
        lvs=dict(pins['lvs_settings'],input=str(run/'out/croc.gds.gz'),schematic=str(reference),
                 report=str(full/'layout.lvsdb'),log=str(full/'deck.log'),target_netlist=str(full/'extracted.cir'),cp0_verdict=str(full/'verdict.json'))
        write_json(full/'switches.json',lvs)
        argv=[tool]
        for k,v in lvs.items():argv+=['-v',f'{k}={v}']
        command(argv+[run/'deck/lvs/cp0c.lvs'],full,kenv,full/'console.log')
        require('CP0C_LVS_DECK_COMPLETE' in (full/'console.log').read_text(),'LVS comparison did not complete')
        command([sys.executable,helper/'cp0c_lvs_support.py','audit',full/'layout.lvsdb',full/'verdict.json',full/'audit.json','croc_chip'],full,env,full/'audit.log')
        command([sys.executable,worker,'supply-audit',full/'layout.lvsdb',run/'reports/sram-supplies.json'],run,env,run/'logs/sram-supplies.log')
        audit=read_json(full/'audit.json')
        write_json(run/'reports/result.json',{'sram_power_repair':'SRAM_PG_REPAIR_VERIFIED',
            'interconnect_drc_markers':0,'full_lvs':audit['status'],'circuit_status_counts':audit['circuit_status_counts'],
            'cp0c':'PENDING','candidate_promoted':False,'sta_rerun':False,
            'limitations':['Remaining LVS/model compatibility review','Original 15 pF I/O electrical violations',
                           'Remaining FEOL/density and other signoff checks','No IR-drop or EM signoff']})
        (run/'status.txt').write_text('SRAM_PG_REPAIR_VERIFIED\n'+audit['status']+'\nCP0C_PENDING\n')
        ok=True
    except BaseException as error:
        if run:
            (run/'failure.txt').write_text(traceback.format_exc())
            (run/'status.txt').write_text(f'STOPPED: {type(error).__name__}: {error}\nCP0C_PENDING\n')
        print(f'STOPPED: {type(error).__name__}: {error}',flush=True)
    finally:
        if run:
            (run/'source.sha256').write_text(''.join(f'{h}  {name}\n' for name,h in sorted(preserve.items())))
            changed=[name for name,h in preserve.items() if not Path(name).is_file() or sha(name)!=h]
            write_json(run/'reports/preservation.json',{'unchanged':not changed,'changed_files':changed})
            if changed:
                ok=False;(run/'status.txt').write_text('STOPPED: source input changed during execution\nCP0C_PENDING\n')
            (run/'output.sha256').write_text(''.join(f'{sha(p)}  {p.relative_to(run)}\n' for folder in ('out','full')
                for p in sorted((run/folder).glob('*')) if p.is_file()))
            packed=archive(run)
            print((run/'status.txt').read_text(),end='',flush=True)
            print(f'Upload: {packed}\nFull candidate and database: {run}/out and {run}/full',flush=True)
        (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return 0 if ok else 1

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:print(f'ERROR: {error}',file=sys.stderr);raise SystemExit(1)
