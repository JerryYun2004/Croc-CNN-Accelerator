#!/usr/bin/env python3
"""Export the reviewed internal-closure candidate and run CMOS5L interconnect DRC."""
import argparse
import csv
import datetime
import json
import os
from pathlib import Path
import platform
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile

from cp0c_gds_support import require,sha256,parse_def,assemble,merge_seal,summarize_rdb
from cp0c_gds_runtime import klayout_environment

CROC='29b2e748e72c592a5a895c2b11fa85bc9080f67a'
PDK5='43ac6216df29c043f97d462706f98a91fa70e3ea'
PDK7='c1d6f49ea902a88e328dda022805da8887ea26e3'
CANDIDATE={
 'croc_internal.def':'ed9bb4299f13bb8e71efb1092f43f5f718693c8ae4be7e588630362774249d31',
 'croc_internal.odb':'67bba20d204c54a94dc4b1bcf573279ff05b899c39bfd220dd9f8e7a7fc9d9a3',
 'croc_internal.sdc':'f0216b26bf10e67bee572a0c8058413f6f0cbfb2c9d167368fb17bc92efe202c',
 'croc_internal.v':'5b2b7e1e2f10096d6b690f343fa3cad7fa0c7fcc05213e33cb59811482f68f40',
 'croc_internal_lvs.v':'4cb924d046da4001d311607caaf6614d3c773d8d177e21046aeb9c3458d3f93d'}
GDS_NAMES=('sg13g2_stdcell.gds','RM_IHPSG13_1P_512x32_c2_bm_bist.gds',
           'sg13cmos5l_io.gds','bondpad5l_70x70.gds')

def command(args,cwd=None,env=None,log=None):
    print('+ '+' '.join(str(x) for x in args),flush=True)
    with Path(log).open('w') if log else open(os.devnull,'w') as f:
        p=subprocess.Popen([str(x) for x in args],cwd=cwd,env=env,text=True,
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        try:
            for line in p.stdout:
                print(line,end='',flush=True);f.write(line);f.flush()
            return p.wait()
        except BaseException:
            p.terminate()
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:p.kill();p.wait()
            raise

def output(args,cwd=None): return subprocess.check_output(args,cwd=cwd,text=True).strip()

def check_manifest(path,base=None):
    entries=[]
    for line in Path(path).read_text().splitlines():
        h,name=line.split('  ',1);p=Path(name)
        if not p.is_absolute(): p=Path(base or Path(path).parent)/p
        require(sha256(p)==h,f'Checksum differs: {p}')
        entries.append((str(p.resolve()),h))
    require(entries,f'Empty manifest: {path}');return entries

def pinned_file(root,path):
    expected=subprocess.check_output(['git','show',f'{CROC}:{path}'],cwd=root)
    require((root/path).read_bytes()==expected,f'Pinned Croc file differs: {path}')
    return root/path

def audit_includes(deck,seen=None):
    seen=seen if seen is not None else set()
    p=Path(deck).resolve();require(p.is_file(),f'Missing rule include: {deck}')
    if p in seen:return seen
    seen.add(p)
    for rel in re.findall(r'^\s*#\s*%include\s+([^\s]+)',p.read_text(),re.M):
        # Resolve relative to the spelling of the containing path, as KLayout does.
        audit_includes(Path(deck).parent/rel,seen)
    return seen

def get_gds(root,manifest,inputs,logdir):
    rows=list(csv.DictReader(manifest.open()));result=[];provenance=[]
    for name in GDS_NAMES:
        choices=[r for r in rows if r['output']=='ihp13/pdk/gds/'+name]
        require(len(choices)==1,f'Manifest GDS selection differs: {name}')
        r=choices[0];dst=inputs/name;local=root/'technology/gds'/name
        require(not r['patch'],f'Unexpected patch on GDS: {name}')
        if local.is_file():
            require(sha256(local)==r['sha256'],f'Existing GDS hash differs: {local}')
            shutil.copy2(local,dst);origin=str(local)
        else:
            require(r['source_type']=='github',f'Required local GDS missing: {local}')
            require(re.fullmatch(r'[0-9a-f]{40}',r['revision']),'Unpinned GDS revision.')
            url=f'https://raw.githubusercontent.com/{r["source"]}/{r["revision"]}/{r["path"]}'
            require(command(['curl','--fail','--location','--retry','2','--silent','--show-error',
                             url,'--output',dst],log=logdir/(name+'.download.log'))==0,f'GDS download failed: {name}')
            origin=url
        require(sha256(dst)==r['sha256'],f'Downloaded GDS checksum differs: {name}')
        result.append(dst);provenance.append(dict(r,materialized=str(dst),origin=origin))
    return result,provenance

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('source',type=Path);a=ap.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Native Apple Silicon macOS required.')
    import klayout.db as db
    require(db.__version__=='0.30.9','Use the installed CP0C KLayout Python environment.')
    root=Path(output(['git','rev-parse','--show-toplevel'])).resolve()
    results=root/'native/results';source=a.source.resolve()
    require(source.parent==results and source.name.startswith('cp0c-internal-close-'),'Use the reviewed internal-close directory.')
    toolbase=Path.home()/'tools/croc-klayout-0.30.9';tool=toolbase/'install/strmrun'
    require((toolbase/'READY').is_file(),'Run bash native/cp0c-gds.sh install first.')
    lock=results/'cp0c.lock'
    try:lock.mkdir()
    except FileExistsError:raise RuntimeError(f'Another CP0C command may be running; inspect {lock}/owner.txt.') from None
    run=None;status=1;preserve={}
    try:
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=gds-drc\n')
        run=Path(tempfile.mkdtemp(prefix='cp0c-gds-drc-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for name in ('input','out','reports','logs','helpers','reference','deck'): (run/name).mkdir()
        print(f'Evidence: {run}\nVersion: 1.3-sram-placeholders\nOne DRC process, two threads.',flush=True)
        here=Path(__file__).resolve().parent
        for p in here.glob('cp0c*gds*'):
            if p.is_file():
                shutil.copy2(p,run/'helpers'/p.name)
                preserve[str(p)]=sha256(p)
        require(shutil.disk_usage(results).free>=6*1024**3,'At least 6 GiB free space required.')
        require((source/'exit-status.txt').read_text().strip()=='0','Internal-close run did not complete.')
        require((source/'preservation.txt').read_text().strip()=='All recorded source/tool/evidence hashes still match.',
                'Internal-close preservation check missing.')
        preserve.update(check_manifest(source/'source.sha256'))
        for n,h in CANDIDATE.items():
            p=source/'eco/out'/n;require(sha256(p)==h,f'Reviewed candidate differs: {n}');preserve[str(p)]=h
        check_manifest(source/'eco/output.sha256')
        preserve.update(check_manifest(toolbase/'runtime.sha256',toolbase))
        for n in ('summary.md','warnings.txt','preservation.txt','provenance.txt'):
            shutil.copy2(source/n,run/'reference'/n)
        for rel in ('replay/reports/final.tsv','replay/reports/audit.tsv','replay/reports/internal-findings.tsv'):
            shutil.copy2(source/rel,run/'reference'/Path(rel).name)
        for n in ('env.sh','scripts/setup_technology.sh','ihp13/pdk/pdk_manifest.csv','klayout/scripts/generate_seal_ring.py'):
            p=pinned_file(root,n);preserve[str(p)]=sha256(p)
        for repo,pin in ((root/'ihp13/sg13cmos5l',PDK5),(root/'ihp13/sg13g2',PDK7)):
            require(output(['git','rev-parse','HEAD'],repo)==pin,f'PDK source pin differs: {repo}')
            require(not output(['git','status','--porcelain','--untracked-files=no'],repo),f'PDK tracked files modified: {repo}')
            sub=output(['git','submodule','status','--recursive'],repo)
            require(not any(l.startswith(('-','+','U')) for l in sub.splitlines()),f'PDK nested submodule differs: {repo}')
        # Source Croc's environment with GNU utilities supplied by native/env.sh.
        env=os.environ.copy();env.update(CP0G_JSON=str(run/'reports/environment.json'),CP0G_PY=sys.executable)
        shell='''set -euo pipefail
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
"$CP0G_PY" - <<'PY'
import os,json
names=['CROC_ROOT','CROC_PDK','PDK_ROOT','KLAYOUT_PATH','PDK_LAYER_MAP_FILE','CROC_TECHNOLOGY_VIEW','PATH']
with open(os.environ['CP0G_JSON'],'w') as f:json.dump({n:os.environ[n] for n in names},f,indent=2)
PY
'''
        require(command(['/bin/bash','-c',shell],root,env,run/'logs/environment.log')==0,'Croc environment failed.')
        loaded=json.loads((run/'reports/environment.json').read_text());env.update(loaded)
        require(loaded['CROC_TECHNOLOGY_VIEW']=='public mirror','This package targets the reviewed public PDK mirror.')
        kpath=Path(loaded['KLAYOUT_PATH']);deck=kpath/'tech/drc/ihp-sg13cmos5l.drc'
        rulefiles=audit_includes(deck)
        jdefault=kpath/'tech/drc/rule_decks/sg13cmos5l_tech_default.json'
        jtech=kpath/'python/sg13cmos5l_pycell_lib/sg13cmos5l_tech_mod.json'
        for p in (*rulefiles,jdefault,jtech,Path(loaded['PDK_LAYER_MAP_FILE'])):
            require(p.is_file(),f'Missing PDK support: {p}');preserve[str(p.resolve())]=sha256(p)
        # Flatten symlinks into a read-only run snapshot; keep relative includes intact.
        shutil.copytree(deck.parent,run/'deck/drc',ignore=shutil.ignore_patterns('testing','__pycache__'))
        shutil.copy2(jtech,run/'deck/tech_mod.json')
        shutil.copy2(root/'klayout/scripts/generate_seal_ring.py',run/'helpers/generate_seal_ring.py')
        preserve[str(root/'native/env.sh')]=sha256(root/'native/env.sh')
        gdss,gds_manifest=get_gds(root,root/'ihp13/pdk/pdk_manifest.csv',run/'input',run/'logs')
        (run/'reports/gds-sources.json').write_text(json.dumps(gds_manifest,indent=2)+'\n')
        # Match the LEF set used by OpenROAD, including all SRAM abstracts.
        pdkr=Path(loaded['PDK_ROOT'])
        lefs=[pdkr/'lef/sg13cmos5l_tech.lef',pdkr/'lef/sg13g2_stdcell.lef',
              *sorted((pdkr/'lef').glob('RM_IHPSG13*.lef')),pdkr/'lef/sg13cmos5l_io.lef',pdkr/'lef/bondpad5l_70x70.lef']
        require(len(lefs)>=5,'Incomplete LEF list.')
        for p in lefs:
            h=sha256(p)
            old=preserve.get(str(p)) or preserve.get(str(p.resolve()))
            require(old is not None and old==h,f'LEF differs from the implementation evidence: {p}')
            preserve[str(p.resolve())]=h
        for p in (kpath/'python').rglob('*'):
            if p.is_file() and p.suffix in ('.py','.json','.lyp'):preserve[str(p.resolve())]=sha256(p)
        shared=root/'ihp13/sg13g2/ihp-sg13g2/libs.tech/klayout/python'
        cni=shared/'pycell4klayout-api/source/python/cni/dlo.py'
        require(cni.exists() or (cni.parent/'dlo/__init__.py').exists(),'Missing PyCell API submodule; run git submodule update --init --recursive.')
        for p in shared.rglob('*.py'):
            if p.is_file():preserve[str(p.resolve())]=sha256(p)
        preserve[str(kpath/'tech/sg13cmos5l.lyp')]=sha256(kpath/'tech/sg13cmos5l.lyp')
        (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
        (run/'provenance.json').write_text(json.dumps({'source':str(source),'croc_pin':CROC,'pdk5_pin':PDK5,'pdk7_pin':PDK7,
            'klayout_source':'6270877110ef808dd442fd2244164cec06a7b10e','klayout_python':db.__version__,
            'native_arch':platform.machine(),'scope':'BEOL + cont + contbar + offgrid + angle + pin + forbidden; precheck=false',
            'not_run':['remaining FEOL','recommended','density','maximal','GDS antenna','LVS','calibrated RC'],
            'io_constraints':'Original 15 pF retained; CP0C remains pending'},indent=2)+'\n')
        smoke=run/'reports/smoke'
        require(command([sys.executable,here/'cp0c-gds-smoke.py',tool,smoke],root,env,run/'logs/smoke.log')==0,'DRC engine smoke failed.')
        defp=source/'eco/out/croc_internal.def';meta=parse_def(defp)
        require(meta['die']==[0,0,1916,1916],f'Unexpected die dimensions: {meta["die"]}')
        print('Assembling physical GDS and checking every component master...',flush=True)
        assembly={'defpath':str(defp),'lefs':[str(p) for p in lefs],'gdss':[str(p) for p in gdss],
            'layer_map':loaded['PDK_LAYER_MAP_FILE'],'out':str(run/'out/croc.gds.gz'),'report':str(run/'reports/assembly.json')}
        (run/'reports/assembly-inputs.json').write_text(json.dumps(assembly,indent=2)+'\n')
        require(command([sys.executable,here/'cp0c-gds-worker.py','assemble',run/'reports/assembly-inputs.json'],
            root,env,run/'logs/assembly.log')==0,'GDS assembly or validation failed.')
        # Execute the pinned upstream seal generator in the wheel environment.
        seal_entry=run/'helpers/run_seal.py'
        seal_entry.write_text('import runpy,sys\nrunpy.run_path(sys.argv[1],init_globals={"width":"2000","height":"2000","output":sys.argv[2]})\n')
        require(command([sys.executable,seal_entry,run/'helpers/generate_seal_ring.py',run/'out/seal_ring.gds.gz'],
            root,env,run/'logs/seal.log')==0,'Pinned seal generation failed.')
        merge={'chip_path':str(run/'out/croc.gds.gz'),'seal_path':str(run/'out/seal_ring.gds.gz'),
            'out':str(run/'out/croc.sealed.gds.gz'),'report':str(run/'reports/sealed.json'),
            'sram_reference':str(run/'input/RM_IHPSG13_1P_512x32_c2_bm_bist.gds')}
        (run/'reports/merge-inputs.json').write_text(json.dumps(merge,indent=2)+'\n')
        require(command([sys.executable,here/'cp0c-gds-worker.py','merge',run/'reports/merge-inputs.json'],
            root,env,run/'logs/merge.log')==0,'Seal merge or validation failed.')
        (run/'export-status.txt').write_text('GDS_EXPORT_VALIDATED\n')
        snapshots={str(p):sha256(p) for p in (run/'out').iterdir() if p.is_file()}
        (run/'output.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(snapshots.items())))
        switches={'input':str(run/'out/croc.sealed.gds.gz'),'topcell':'croc_chip_sealed',
            'report':str(run/'reports/interconnect.lyrdb'),'log':str(run/'logs/deck.log'),
            'tables':'main cont contbar','threads':'2','run_mode':'deep','precheck_drc':'false',
            'no_feol':'true','no_beol':'false','no_recommended':'true','no_offgrid':'false',
            'no_angle':'false','no_pin':'false','no_forbidden':'false',
            'drc_json_default':str(run/'deck/drc/rule_decks/sg13cmos5l_tech_default.json'),
            'drc_json':str(run/'deck/tech_mod.json')}
        (run/'reports/drc-switches.json').write_text(json.dumps(switches,indent=2)+'\n')
        argv=[tool]
        for k,v in switches.items():argv.extend(['-v',f'{k}={v}'])
        argv.append(run/'deck/drc/ihp-sg13cmos5l.drc')
        print('Running enclosure-enabled interconnect DRC...',flush=True)
        drc_env=klayout_environment(tool,env)
        (run/'reports/drc-runtime.json').write_text(json.dumps({'executable':str(tool),
            'DYLD_LIBRARY_PATH':drc_env['DYLD_LIBRARY_PATH'],'scope':'strmrun child only'},indent=2)+'\n')
        rc=command(argv,run,drc_env,run/'logs/drc-console.log');(run/'drc-process-exit.txt').write_text(str(rc)+'\n')
        require(rc==0,'DRC execution failed; retained logs identify the cause.')
        drc=summarize_rdb(run/'reports/interconnect.lyrdb',run/'reports/drc-counts.tsv',run/'reports/marker-samples.json')
        (run/'reports/drc-summary.json').write_text(json.dumps(drc,indent=2)+'\n')
        outcome='INTERCONNECT_DRC_CLEAN' if drc['markers']==0 else 'INTERCONNECT_DRC_VIOLATIONS'
        (run/'drc-status.txt').write_text(outcome+'\n')
        warning_lines=[]
        for logfile in sorted((run/'logs').glob('*.log')):
            warning_lines.extend(f'{logfile.name}: {line}' for line in logfile.read_text(errors='replace').splitlines()
                if re.search(r'\b(warning|error|exception|fatal)\b',line,re.I))
        (run/'warnings.txt').write_text('\n'.join(warning_lines)+'\n')
        lines=['CP0C GDS EXPORT AND INTERCONNECT DRC COMPLETE','GDS_EXPORT_VALIDATED',outcome,'CP0C_PENDING','',
               f'Physical component instances: {meta["components"]}',f'Cell masters: {len(meta["masters"])}',
               'Die: 1916 x 1916 um; sealed boundary: 2000 x 2000 um (4 mm^2).',
               f'DRC markers: {drc["markers"]}; violating categories: {drc["violating_categories"]}.',
               f'Warning/error-labelled log lines retained for review: {len(warning_lines)} (see warnings.txt).',
               'Precheck disabled: via enclosure rules were enabled and required categories verified.',
               'Scope: BEOL, contact/contact-bar, offgrid, angle, pin and forbidden-layer checks.',
               'No density fill, remaining FEOL, recommended/maximal checks, GDS antenna or LVS in this step.',
               'A clean scoped DRC report is not complete foundry signoff or CP0C PASS.',
               'Original 15 pF I/O constraints, internal routed ODB, DEF, netlists and SDC preserved.',
               'Eight documented upstream SRAM empty placeholders retained; source hash and references verified.',
               'Timing still uses approximate LEF RC; the 575 annotation exceptions remain open.','']
        lines += [f'{cat}: {n} markers' for cat,n in drc['top_findings']]
        (run/'summary.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines),flush=True)
        for p,h in snapshots.items():require(sha256(p)==h,f'DRC changed exported GDS: {p}')
        status=0
    except (Exception,KeyboardInterrupt) as exc:
        if run:(run/'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        print(f'STOPPED: {type(exc).__name__}: {exc}',file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                try:
                    for p,h in preserve.items():require(sha256(p)==h,f'Source changed: {p}')
                    (run/'preservation.txt').write_text('All recorded candidate/tool/PDK hashes still match.\n')
                except Exception as exc:
                    status=1
                    with (run/'failure.txt').open('a') as f:f.write(str(exc)+'\n')
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                (run/'exit-status.txt').write_text(str(status)+'\n')
                # The GDS and full RDB stay on the Mac. Archive bounded marker evidence.
                archive=run.with_suffix('.tar.gz')
                with tarfile.open(archive,'w:gz') as tar:
                    for p in sorted(run.rglob('*')):
                        rel=p.relative_to(run)
                        if p.is_file() and rel.parts[0] not in ('input','out','deck') and p.suffix not in ('.lyrdb','.pyc'):
                            tar.add(p,arcname=str(rel),recursive=False)
                print(f'Upload: {archive}\nFull layout/report: {run}/out and {run}/reports/interconnect.lyrdb',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True);lock.rmdir()
    return status

if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
