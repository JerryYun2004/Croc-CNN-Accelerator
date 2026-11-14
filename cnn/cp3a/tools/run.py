#!/usr/bin/env python3
import argparse,json,os,platform,shutil,subprocess,sys,tarfile,tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from common import command,git_state,manifest,put,require,sha
from preflight import sources,accepted
from flow import execute
ROOT=Path(__file__).resolve().parents[1]

def native_tools(pins,out):
    paths=dict(bender=shutil.which('bender'),
        yosys=str(Path.home()/'tools/croc-oss-cad-suite-2025-12-30/oss-cad-suite/bin/yosys'),
        openroad=str(Path.home()/'tools/croc-openroad-c463e998615210dc322c999fc9a9f56885dbd664/install/bin/openroad'))
    data={}
    for n,path in paths.items():
        require(path and Path(path).is_file(),'Missing accepted native tool: '+n)
        require(sha(path)==pins['tools'][n]['sha256'],'Native tool differs: '+n)
        arg=dict(bender='--version',yosys='-V',openroad='-version')[n]
        version=subprocess.check_output([path,arg],text=True).strip()
        require(pins['tools'][n]['version_contains'] in version,'Tool version differs: '+n)
        data[n]=dict(path=path,launcher_sha256=sha(path),version=version)
    put(out/'tools.json',data)
    return paths

def main():
    p=argparse.ArgumentParser();p.add_argument('--cp2c',required=True,type=Path);a=p.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Run on the existing native arm64 Mac')
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    repo=ROOT.parents[1];evidence=a.cp2c.expanduser().resolve();pins=json.loads((ROOT/'pins.json').read_text())
    require(' ' not in str(repo),'Existing flow requires a repository path without spaces')
    before=git_state(repo);require(before['branch']=='cnn/bringup','Use cnn/bringup')
    resultroot=repo/'cnn/results';resultroot.mkdir(exist_ok=True)
    stamp=datetime.now(ZoneInfo('America/Vancouver')).strftime('%Y%m%d-%H%M%S')
    out=Path(tempfile.mkdtemp(prefix='cp3a-'+stamp+'-',dir=resultroot))
    print('CP3A evidence: '+str(out),flush=True);code=1;protected={}
    try:
        print('STAGE: verify accepted CP2C, frozen physical flow and PDK',flush=True)
        protected=sources(repo,ROOT,pins);protected.update(accepted(evidence,pins))
        put(out/'protected-inputs.json',protected)
        put(out/'prerequisite.json',dict(status='ACCEPTED_CP2C_VERIFIED',manifest_sha256=pins['cp2c_evidence_manifest']))
        shutil.copytree(ROOT,out/'source',ignore=shutil.ignore_patterns('__pycache__'))
        put(out/'host.json',dict(platform=platform.platform(),machine=platform.machine(),python=sys.version,timezone='America/Vancouver'))
        tools=native_tools(pins,out)
        execute(repo,out,**tools)
        preserved=all(Path(n).is_file() and sha(n)==h for n,h in protected.items())
        samegit=before==git_state(repo)
        put(out/'preservation.json',dict(inputs_unchanged=preserved,git_state_unchanged=samegit,unchanged=preserved and samegit))
        require(preserved and samegit,'Source or Git state changed during run')
        put(out/'conclusion.json',dict(status='CP3A_FIT_PASS',version=pins['version'],
            scope='Clean paired whole-chip synthesis and fixed-die legal placement feasibility',
            native_review_required=True,cp0c_electrical='PENDING',pdn_closed=False,routing_closed=False,
            timing_closed=False,electrical_closed=False,historical_layout_reproduced=False))
        (out/'status.txt').write_text('CP3A_FIT_PASS\nCP0C_ELECTRICAL_PENDING\n');code=0
    except Exception as e:
        (out/'status.txt').write_text('CP3A_FAILED\nCP0C_ELECTRICAL_PENDING\n')
        (out/'failure.txt').write_text(str(e)+'\n');print('STOPPED: '+str(e),flush=True)
    (out/'exit-status.txt').write_text(str(code)+'\n')
    # Keep inputs, mapped netlists, ODB/DEF, logs, reports and checks; omit large
    # reproducible synthesis intermediates and duplicated ABC databases.
    files=[x for x in sorted(out.rglob('*')) if x.is_file() and 'tmp' not in x.relative_to(out).parts
        and x.name not in ['lazy_man_synth_library.aig','netlist_debug.v'] and not x.name.startswith('core.')]
    (out/'SHA256SUMS').write_text(''.join(sha(x)+'  '+str(x.relative_to(out))+'\n' for x in files))
    archive=Path(str(out)+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tar:
        for x in files+[out/'SHA256SUMS']:tar.add(x,arcname=out.name+'/'+str(x.relative_to(out)))
    print((out/'status.txt').read_text(),end='');print('Upload: '+str(archive),flush=True)
    return code

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:print('ERROR: '+str(e),file=sys.stderr);sys.exit(1)
