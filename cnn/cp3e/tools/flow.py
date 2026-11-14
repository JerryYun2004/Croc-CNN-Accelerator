import shutil,json,re
from pathlib import Path
from common import command,put,require,sha
from environment import environment
from power import records,norm
from logic import audit,library
from checks import pdn,constraints,timing,routing,warnings,independent
from electrical import inventory,progress
from plans import local_targets,critical_drivers
ROOT=Path(__file__).resolve().parents[1]

def admissible(times,electrical):
    return times['worst_min_ns']>=0.1 and times['tns_min_ns']>=-1e-6 and all(s['internal']==0 for c in electrical.values() for s in c['summary'].values())

def rank(item):
    t=item['timing'];return (t['worst_max_ns'],t['tns_max_ns'])

def audit_selection(d):
    selection=json.loads((d/'selection.json').read_text());checked=[]
    for item in selection['candidates']:
        name=item['name'];require(name in ['initial','optimized']+[f'round{x}' for x in range(1,8)],'Unknown candidate name')
        require(name not in [x['name'] for x in checked],'Duplicate candidate')
        c=d/'candidates'/name;t=timing(c)['final'];e=inventory(c/'global.def',c/'reports')
        require(item['timing']==t and item['eligible']==admissible(t,e),'Candidate metrics/eligibility changed: '+name)
        require(item['internal_electrical']=={k:{kind:r['internal'] for kind,r in v['summary'].items()} for k,v in e.items()},'Candidate electrical counts changed')
        log=(c/'run.log').read_text();require('CP3E_STAGE_COMPLETE' in log and '[ERROR' not in log,'Candidate process incomplete')
        if name.startswith('round'):
            plan=json.loads((c/'repair-plan.json').read_text())
            require('CP3E_TARGETED_DRIVERS '+str(len(plan['drivers']))+'\n' in log,'Critical-driver plan incomplete')
        checked.append(item)
    valid=[i for i in checked if i['eligible']];require(valid,'No admissible candidate')
    require(max(valid,key=rank)['name']==selection['selected'],'Candidate selection mismatch')
    require(sha(d/'global.guide')==sha(d/'candidates'/selection['selected']/'global.guide'),'Selected guides differ')
    require(timing(d)['final']==next(i['timing'] for i in valid if i['name']==selection['selected']),'Selected metrics differ')
    return selection

def execute_variant(repo,evidence,out,openroad,v,env=None):
    env=environment(repo) if env is None else dict(env);env['CP3E_FLOW']=str(ROOT/'flow')
    d=out/v;d.mkdir();source=evidence/v
    for n in ['scripts','src']:shutil.copytree(source/n,d/n)
    shutil.copyfile(source/'original.sdc',d/'original.sdc')
    candidates=d/'candidates';candidates.mkdir();rt={};items=[]
    lib=library(repo/'ihp13/pdk/lib/sg13g2_stdcell_typ_1p20V_25C.lib')
    def stage(name,tcl,input_path,kind='DB',local=None,drivers=None):
        s=candidates/name;s.mkdir()
        for n in ['scripts','src']:shutil.copytree(d/n,s/n)
        (s/'local-targets.tsv').write_text(''.join(x['type']+'\t'+x['pin']+'\n' for x in (local or [])))
        (s/'drivers.txt').write_text(''.join(x['instance']+'\n' for x in (drivers or [])))
        put(s/'repair-plan.json',dict(local=local or [],drivers=drivers or []))
        runenv=dict(env,CP3E_INPUT=str(input_path),CP3E_INPUT_KIND=kind,CP3E_PREFIX='cp3e_'+name.replace('-','_')+'_')
        print('STAGE: '+v+' '+name,flush=True)
        rt[name]=command([openroad,'-no_init','-exit',ROOT/'flow'/tcl],s/'run.log',cwd=s,env=runenv,timeout=1800)
        put(d/'runtime.json',rt)
        require('CP3E_STAGE_COMPLETE' in (s/'run.log').read_text(),'Stage did not finish: '+name)
        t=timing(s)['final'];e=inventory(s/'global.def',s/'reports')
        item=dict(name=name,timing=t,eligible=admissible(t,e),internal_electrical={c:{k:x['internal'] for k,x in a['summary'].items()} for c,a in e.items()})
        put(s/'electrical.json',e);put(s/'stage-summary.json',item);items.append(item)
        return s,item
    initial,_=stage('initial','import.tcl',source/'global.def','DEF')
    for n in ['imported.def','imported.sdc']:shutil.copyfile(initial/n,d/n)
    targets=local_targets(source);put(d/'local-repair-plan.json',targets)
    drivers=critical_drivers(source/'reports',lib,minimum_cap=0.018)
    current,item=stage('round1','repair.tcl',initial/'global.odb',local=targets,drivers=drivers)
    for step in range(2,8):
        if item['eligible'] and item['timing']['worst_max_ns']>=0.08:break
        drivers=critical_drivers(current/'reports',lib)
        if not drivers:break
        current,item=stage('round'+str(step),'repair.tcl',current/'global.odb',drivers=drivers)
    valid=[i for i in items if i['eligible']];require(valid,'No candidate clears internal electrical and 0.1 ns hold gates')
    best=max(valid,key=rank)
    if best['timing']['worst_max_ns']<0.08:
        opt=d/'optimization';opt.mkdir()
        for n in ['scripts','src']:shutil.copytree(d/n,opt/n)
        runenv=dict(env,CP3E_INPUT=str(candidates/best['name']/'global.odb'),CP3E_INPUT_KIND='DB')
        print('STAGE: '+v+' setup optimization',flush=True)
        rt['optimization']=command([openroad,'-no_init','-exit',ROOT/'flow/optimize.tcl'],opt/'run.log',cwd=opt,env=runenv,timeout=3600)
        require('CP3E_OPTIMIZER_COMPLETE' in (opt/'run.log').read_text(),'Optimizer incomplete')
        stage('optimized','route.tcl',opt/'optimized.odb')
        shutil.copyfile(opt/'run.log',d/'optimization.log')
    else:(d/'optimization.log').write_text('CP3E_OPTIMIZER_SKIPPED: setup margin reached\n')
    valid=[i for i in items if i['eligible']];best=max(valid,key=rank);selected=candidates/best['name']
    put(d/'selection.json',dict(selected=best['name'],policy='Require zero internal electrical violations and minimum slack >= 0.1 ns; select greatest maximum slack, then maximum TNS.',candidates=items))
    for n in ['global.def','global.odb','global.guide','propagated.sdc','final.v']:shutil.copyfile(selected/n,d/n)
    shutil.copytree(selected/'reports',d/'reports');shutil.copyfile(selected/'run.log',d/'cleanup.log');shutil.copyfile(initial/'run.log',d/'import.log')
    for name,tcl,log in [('power','power.tcl','power.log'),('verification','verify.tcl','independent-route.log')]:
        print('STAGE: '+v+' '+name,flush=True)
        rt[name]=command([openroad,'-no_init','-exit',ROOT/'flow'/tcl],d/log,cwd=d,env=env,timeout=1200)
        put(d/'runtime.json',rt)
    return rt

def inspect(repo,evidence,out,reference_mode="accepted_native_cp3d"):
    require(reference_mode in ["accepted_native_cp3d","same_tool_import"],"Invalid review reference mode")
    lib=repo/'ihp13/pdk/lib/sg13g2_stdcell_typ_1p20V_25C.lib';results={}
    for v in ['baseline','cnn']:
        d=out/v;before=evidence/v/'global.def';a,b=before.read_text(),(d/'imported.def').read_text()
        for sec in ['COMPONENTS','PINS']:
            require({n:norm(x) for n,x in records(a,sec).items()}=={n:norm(x) for n,x in records(b,sec).items()},'Import changed '+sec)
        logical=audit(before,d/'global.def',lib);put(d/'logic-audit.json',logical)
        def clock_nets(text):
            return {n:sorted(re.findall(r'\( (\S+) (\S+) \)',body)) for n,body in records(text,'NETS').items() if '+ USE CLOCK' in body}
        ac,bc=clock_nets(a),clock_nets((d/'global.def').read_text())
        require(ac and ac==bc,'Clock-net topology changed')
        clock=dict(status='CLOCK_NET_TOPOLOGY_PRESERVED',nets=len(ac));put(d/'clock-audit.json',clock)

        power=pdn(before,d/'global.def',lib);put(d/'power-audit.json',power)
        require(sha(d/'original.sdc')==sha(evidence/v/'original.sdc'),'Reference constraints changed')
        sdc=constraints(d/'original.sdc',d/'propagated.sdc');put(d/'constraints-audit.json',sdc)
        route=routing(d);times=timing(d);fresh=independent(d,route,times);put(d/'independent-audit.json',fresh)
        native_old=json.loads((evidence/v/'summary.json').read_text())['timing']['final']
        old=native_old if reference_mode=='accepted_native_cp3d' else timing(d/'candidates/initial')['final']
        ea=inventory(before,evidence/v/'reports') if reference_mode=='accepted_native_cp3d' else inventory(d/'candidates/initial/global.def',d/'candidates/initial/reports')
        eb=inventory(d/'global.def',d/'reports')
        put(d/'electrical-before.json',ea);put(d/'electrical-after.json',eb)
        improvement=progress(old,times['final'],ea,eb);put(d/'progress-audit.json',improvement)
        warn=warnings(d);put(d/'warnings.json',warn)
        selection=audit_selection(d)
        plan=json.loads((d/'local-repair-plan.json').read_text());log=(d/'candidates/round1/run.log').read_text()
        for kind,marker in [('hold','CP3E_LOCAL_HOLD_BUFFERS'),('sram','CP3E_SRAM_BUFFERS')]:
            require(marker+' '+str(sum(x['type']==kind for x in plan))+'\n' in log,'Local repair plan incomplete')
        result=dict(status='CP3E_CHECKS_PASS_SIGNOFF_PENDING' if reference_mode=='accepted_native_cp3d' else 'CP3E_LINUX_PREPARATORY_PASS_NATIVE_PENDING',logic=logical,clock=clock,power=power,constraints=sdc,routing=route,timing=times,reference_timing=old,reference_mode=reference_mode,accepted_native_cp3d_timing=native_old,improvement=improvement,independent_verification=fresh,warning_counts=warn['by_code'],selected_candidate=selection['selected'],cp0c_electrical='PENDING',detailed_routing_complete=False,timing_closed=False,electrical_closed=False)
        put(d/'summary.json',result);results[v]=result
    put(out/'comparison.json',results);return results

def execute(repo,evidence,out,openroad):
    runtime={}
    for v in ['baseline','cnn']:
        runtime[v]=execute_variant(repo,evidence,out,openroad,v);put(out/'runtime.json',runtime)
    return inspect(repo,evidence,out)
