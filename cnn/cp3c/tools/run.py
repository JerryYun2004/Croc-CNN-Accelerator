#!/usr/bin/env python3
import argparse,json,os,platform,shutil,subprocess,sys,tarfile,tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from common import git_state,put,require,sha
from preflight import sources,accepted
from flow import execute
from selftest import controls
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--cp3b',required=True,type=Path);a=p.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Run on the existing native arm64 Mac')
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    repo=ROOT.parents[1];evidence=a.cp3b.expanduser().resolve();pins=json.loads((ROOT/'pins.json').read_text())
    require(' ' not in str(repo),'Existing flow requires a repository path without spaces')
    before=git_state(repo);require(before['branch']=='cnn/bringup','Use cnn/bringup')
    resultroot=repo/'cnn/results';require(not resultroot.is_symlink(),'Linked results directory refused');resultroot.mkdir(exist_ok=True)
    stamp=datetime.now(ZoneInfo('America/Vancouver')).strftime('%Y%m%d-%H%M%S')
    out=Path(tempfile.mkdtemp(prefix='cp3c-'+stamp+'-',dir=resultroot))
    print('CP3C evidence: '+str(out),flush=True);code=1;protected={}
    try:
        print('STAGE: verify accepted CP3B, native OpenROAD and protected inputs',flush=True)
        protected=sources(repo,ROOT,pins);protected.update(accepted(evidence,pins))
        put(out/'protected-inputs.json',protected)
        put(out/'prerequisite.json',dict(status='ACCEPTED_CP3B_VERIFIED',manifest_sha256=pins['cp3b_evidence_manifest']))
        shutil.copytree(ROOT,out/'source',ignore=shutil.ignore_patterns('__pycache__'))
        put(out/'host.json',dict(platform=platform.platform(),machine=platform.machine(),python=sys.version,timezone='America/Vancouver'))
        tool=Path.home()/'tools/croc-openroad-c463e998615210dc322c999fc9a9f56885dbd664/install/bin/openroad'
        require(tool.is_file() and sha(tool)==pins['tools']['openroad']['sha256'],'OpenROAD differs from accepted CP3B native executable')
        version=subprocess.check_output([str(tool),'-version'],text=True,timeout=60).strip()
        require(pins['tools']['openroad']['version_contains'] in version,'Native OpenROAD version differs')
        protected[str(tool)]=sha(tool);put(out/'protected-inputs.json',protected)
        put(out/'tools.json',dict(openroad=dict(path=str(tool),sha256=sha(tool),version=version)))
        execute(repo,evidence,out,str(tool))
        print('STAGE: corruption controls and preservation',flush=True)
        put(out/'corruption-controls.json',controls(out,repo,evidence))
        preserved=all(Path(n).is_file() and sha(n)==h for n,h in protected.items());samegit=before==git_state(repo)
        put(out/'preservation.json',dict(inputs_unchanged=preserved,git_state_unchanged=samegit,unchanged=preserved and samegit))
        require(preserved and samegit,'Source or Git state changed during run')
        put(out/'conclusion.json',dict(status='CP3C_CTS_GRT_PASS',version=pins['version'],
            scope='Paired CTS, bounded fanout/timing repair, structural logic preservation and zero-overflow global routing',
            native_review_required=True,cp0c_electrical='PENDING',power_connectivity_verified=True,
            bounded_logic_transform_verified=True,original_constraints_preserved=True,
            sram_abstract_supply_coverage_verified=True,global_routing_feasible=True,
            clock_tree_synthesized=True,detailed_routing_complete=False,full_lvs_verified=False,
            timing_closed=False,electrical_closed=False,ir_drop_verified=False,em_verified=False,
            historical_layout_reproduced=False))
        (out/'status.txt').write_text('CP3C_CTS_GRT_PASS\nTIMING_CLOSURE_PENDING\nNATIVE_REVIEW_REQUIRED\nCP0C_ELECTRICAL_PENDING\n');code=0
    except Exception as e:
        (out/'status.txt').write_text('CP3C_FAILED\nCP0C_ELECTRICAL_PENDING\n')
        (out/'failure.txt').write_text(str(e)+'\n');print('STOPPED: '+str(e),flush=True)
        put(out/'preservation.json',dict(checked=bool(protected),
            inputs_unchanged=bool(protected) and all(Path(n).is_file() and sha(n)==h for n,h in protected.items()),
            git_state_unchanged=before==git_state(repo)))
    (out/'exit-status.txt').write_text(str(code)+'\n')
    files=[x for x in sorted(out.rglob('*')) if x.is_file() and '__pycache__' not in x.parts and not x.name.startswith('core.')]
    (out/'SHA256SUMS').write_text(''.join(sha(x)+'  '+str(x.relative_to(out))+'\n' for x in files))
    archive=Path(str(out)+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tar:
        for x in files+[out/'SHA256SUMS']:tar.add(x,arcname=out.name+'/'+str(x.relative_to(out)))
    print((out/'status.txt').read_text(),end='');print('Upload: '+str(archive),flush=True)
    return code

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:print('ERROR: '+str(e),file=sys.stderr);sys.exit(1)
