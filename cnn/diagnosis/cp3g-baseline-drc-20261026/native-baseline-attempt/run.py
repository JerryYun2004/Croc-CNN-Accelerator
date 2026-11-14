#!/usr/bin/env python3
"""One manual native route attempt and one fresh DRC; no extraction or timing."""
import hashlib,json,os,platform,re,resource,shutil,signal,subprocess,sys,tempfile,time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from environment import environment
KIT=Path(__file__).resolve().parent
REPO=KIT.parents[3]
EVIDENCE=REPO/'cnn/results/cp3f-20261025-151432-r_46flt3'
TOOL=Path.home()/'tools/croc-openroad-c463e998615210dc322c999fc9a9f56885dbd664/install/bin/openroad'

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for data in iter(lambda:f.read(1048576),b''):h.update(data)
    return h.hexdigest()

def put(p,value):
    temporary=p.with_suffix(p.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(p)

def stop(p):
    if p.poll() is None:
        os.killpg(p.pid,signal.SIGTERM)
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);p.wait()

def main():
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise RuntimeError('Requires existing native arm64 Mac')
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    if subprocess.check_output(['git','branch','--show-current'],cwd=REPO,text=True).strip()!='cnn/bringup':
        raise RuntimeError('Expected cnn/bringup')
    stamp=datetime.now(ZoneInfo('America/Vancouver')).strftime('%Y%m%d-%H%M%S')
    out=Path(tempfile.mkdtemp(prefix='cp3g-native-baseline-'+stamp+'-',dir=REPO/'cnn/results'))
    print('Candidate: '+str(out),flush=True)
    state=dict(status='PRECHECK',accepted=False,route_iteration_limit=20,route_timeout_seconds=3600,
               drc_timeout_seconds=300,termination_grace_seconds=5,route_threads=8,drc_threads=2,
               hold_margin='UNRESOLVED',extraction='NOT_RUN',timing='NOT_RUN',cp3g_acceptance='PENDING',
               signoff='PENDING',cp0c_electrical='PENDING',stages={})
    pins=json.loads((KIT/'input-pins.json').read_text());code=1
    def save():put(out/'status.json',state)
    def preserved():return all(Path(p).is_file() and sha(p)==h for p,h in pins.items())
    def stage(name,tcl,env,limit,marker):
        state['status']=name.upper()+'_RUNNING';save()
        start=time.monotonic();result=dict(completed=False,timed_out=False)
        with (out/(name+'.log')).open('xb',buffering=0) as log:
            p=subprocess.Popen([str(TOOL),'-no_init','-exit',str(out/'flow'/tcl)],cwd=out,env=env,
                               stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:result['exit_code']=p.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                result['timed_out']=True;stop(p);result['exit_code']=p.returncode
            except BaseException:
                stop(p);result['exit_code']=p.returncode
                result['interrupted']=True
                result['seconds']=round(time.monotonic()-start,3)
                state['stages'][name]=result;save();raise
        result['seconds']=round(time.monotonic()-start,3)
        result['completed']=(not result['timed_out'] and result['exit_code']==0 and
                             marker in (out/(name+'.log')).read_text())
        state['stages'][name]=result;save()
        return result['completed']
    save()
    try:
        if not preserved():raise RuntimeError('Pinned tool, PDK, or accepted input differs; no flow launched')
        shutil.copytree(KIT,out/'preparation',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(KIT/'flow',out/'flow')
        for folder in ('scripts','src'):shutil.copytree(EVIDENCE/'baseline'/folder,out/folder)
        for src,dst in [('global.def','input.def'),('global.guide','input.guide'),('original.sdc','original.sdc')]:
            shutil.copy2(EVIDENCE/'baseline'/src,out/dst)
        # Direct log-file output; no in-memory buffering, polling, retry, or sweep.
        env=environment(REPO);env.update(CP3G_FLOW=str(out/'flow'),CP3G_INPUT_DEF=str(out/'input.def'),
                                        CP3G_INPUT_GUIDE=str(out/'input.guide'))
        if not stage('route','route.tcl',env,3600,'CP3G_DETAIL_ROUTE_COMPLETE'):
            state['status']='ROUTE_INCOMPLETE';return 1
        required=['routed.def','routed.odb','routed.v','propagated.sdc','route-drc.rpt']
        if not all((out/n).is_file() for n in required):
            state['status']='ROUTE_INCOMPLETE';raise RuntimeError('Missing routed outputs; DRC not launched')
        log=(out/'route.log').read_text();counts=re.findall(r'^CP3G_ROUTER_DRC (\d+)\s*$',log,re.M)
        if len(counts)!=1:raise RuntimeError('Missing/unrecognized router DRC count; fresh check not launched')
        state['router_drc_count']=int(counts[0]);state['route_outputs']={n:sha(out/n) for n in required};save()
        env['CP3G_INPUT_DEF']=str(out/'routed.def')
        if not stage('drc','drc.tcl',env,300,'CP3G_INDEPENDENT_DRC_COMPLETE'):
            state['status']='DRC_INCOMPLETE';return 1
        report=out/'independent-drc.rpt'
        if not report.is_file():raise RuntimeError('Missing fresh DRC report')
        drc_log=(out/'drc.log').read_text()
        counts=re.findall(r'^CP3G_INDEPENDENT_DRC_COUNT (\d+)\s*$',drc_log,re.M)
        actual=len(re.findall(r'^violation type:',report.read_text(),re.M))
        if len(counts)!=1 or int(counts[0])!=actual:raise RuntimeError('DRC count/report disagreement')
        state['fresh_drc_count']=actual
        state['status']='ROUTE_AND_FRESH_DRC_ZERO' if actual==0 and state['router_drc_count']==0 else 'DRC_FAIL'
        code=0 if state['status']=='ROUTE_AND_FRESH_DRC_ZERO' else 2
        return code
    except KeyboardInterrupt:
        state['status']='INTERRUPTED_INCOMPLETE';return 130
    except Exception as e:
        state['error']=str(e)
        if not state['status'].endswith('INCOMPLETE'):state['status']='FAILED_INCOMPLETE'
        return 1
    finally:
        state['protected_inputs_unchanged']=preserved()
        if not state['protected_inputs_unchanged']:state['status']='INPUT_PRESERVATION_FAILED'
        save()
        files=[p for p in sorted(out.rglob('*')) if p.is_file() and '__pycache__' not in p.parts]
        (out/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(out))+'\n' for p in files))
        print('Status: '+state['status'],flush=True);print('Evidence: '+str(out),flush=True)
        if not state['protected_inputs_unchanged']:return 1

if __name__=='__main__':sys.exit(main())
