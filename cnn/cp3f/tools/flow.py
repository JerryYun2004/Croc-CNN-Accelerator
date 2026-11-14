import json,re,shutil
from pathlib import Path
from common import command,put,require,sha
from environment import environment
from power import records,norm
from logic import audit,library
from checks import pdn,constraints,timing,routing,warnings,independent
from electrical import inventory,progress
from plans import critical_drivers
from clock import capture_targets,clock_audit
ROOT=Path(__file__).resolve().parents[1]
MAX_CYCLES=2

def admissible(times,electrical):
    return times['worst_min_ns']>=0.1 and times['tns_min_ns']>=-1e-6 and all(s['internal']==0 for c in electrical.values() for s in c['summary'].values())

def setup_met(times):
    return times['worst_max_ns']>=0 and times['tns_max_ns']>=-1e-6

def rank(item):
    t=item['timing'];return (t['worst_max_ns'],t['tns_max_ns'])

def audit_selection(d):
    selection=json.loads((d/'selection.json').read_text());checked=[]
    for item in selection['candidates']:
        name=item['name'];require(name=='initial' or re.fullmatch(r'(buffer|setup)[1-2]|greedy|(skew|hold)[1-8]',name),'Unknown candidate name')
        require(name not in [x['name'] for x in checked],'Duplicate candidate')
        if name!='initial':require(item['parent'] in [x['name'] for x in checked],'Missing candidate parent')
        c=d/'candidates'/name;t=timing(c)['final'];e=inventory(c/'global.def',c/'reports')
        require(item['timing']==t and item['eligible']==admissible(t,e),'Candidate metrics/eligibility changed: '+name)
        require(item['setup_gate_met']==setup_met(t),'Candidate setup flag changed')
        require(item['internal_electrical']=={k:{kind:r['internal'] for kind,r in v['summary'].items()} for k,v in e.items()},'Candidate electrical counts changed')
        log=(c/'run.log').read_text();require('CP3F_STAGE_COMPLETE' in log and '[ERROR' not in log,'Candidate process incomplete')
        if name.startswith('skew'):
            plan=json.loads((c/'capture-plan.json').read_text())
            require('CP3F_CAPTURE_CLOCKS '+str(len(plan['targets']))+'\n' in log,'Capture-clock plan incomplete')
        if name.startswith('buffer'):
            plan=json.loads((c/'repair-plan.json').read_text())
            require('CP3F_TARGETED_DRIVERS '+str(len(plan['drivers']))+'\n' in log,'Critical-driver plan incomplete')
        if name.startswith(('setup','hold')) or name=='greedy':
            log=(d/'optimizations'/name/'run.log').read_text()
            require('CP3F_OPTIMIZER_COMPLETE' in log and '[ERROR' not in log,'Optimizer process incomplete')
        checked.append(item)
    valid=[i for i in checked if i['eligible']];require(valid,'No admissible candidate')
    require(max(valid,key=rank)['name']==selection['selected'],'Candidate selection mismatch')
    require(sha(d/'global.guide')==sha(d/'candidates'/selection['selected']/'global.guide'),'Selected guides differ')
    require(timing(d)['final']==next(i['timing'] for i in valid if i['name']==selection['selected']),'Selected metrics differ')
    return selection

def execute_variant(repo,evidence,out,openroad,v,env=None):
    env=environment(repo) if env is None else dict(env);env['CP3F_FLOW']=str(ROOT/'flow')
    d=out/v;d.mkdir();source=evidence/v
    for n in ['scripts','src']:shutil.copytree(source/n,d/n)
    shutil.copyfile(source/'original.sdc',d/'original.sdc')
    candidates=d/'candidates';candidates.mkdir();(d/'optimizations').mkdir();rt={};items=[]
    lib=library(repo/'ihp13/pdk/lib/sg13g2_stdcell_typ_1p20V_25C.lib')
    def stage(name,tcl,input_path,parent=None,kind='DB',drivers=None,targets=None):
        s=candidates/name;s.mkdir()
        for n in ['scripts','src']:shutil.copytree(d/n,s/n)
        (s/'drivers.txt').write_text(''.join(x['instance']+'\n' for x in (drivers or [])))
        put(s/'repair-plan.json',dict(drivers=drivers or []))
        if targets is not None:
            put(s/'capture-plan.json',dict(prefix='cp3f_'+name+'_',targets=targets))
            (s/'targets.txt').write_text(''.join(x['instance']+'\n' for x in targets))
        runenv=dict(env,CP3F_INPUT=str(input_path),CP3F_INPUT_KIND=kind,CP3F_PREFIX='cp3f_'+name+'_')
        print('STAGE: '+v+' '+name,flush=True)
        rt[name]=command([openroad,'-no_init','-exit',ROOT/'flow'/tcl],s/'run.log',cwd=s,env=runenv,timeout=1800)
        put(d/'runtime.json',rt)
        require('CP3F_STAGE_COMPLETE' in (s/'run.log').read_text(),'Stage did not finish: '+name)
        t=timing(s)['final'];e=inventory(s/'global.def',s/'reports')
        item=dict(name=name,parent=parent,timing=t,eligible=admissible(t,e),setup_gate_met=setup_met(t),internal_electrical={c:{k:x['internal'] for k,x in a['summary'].items()} for c,a in e.items()})
        put(s/'electrical.json',e);put(s/'stage-summary.json',item);items.append(item)
        print('CANDIDATE: '+v+' '+json.dumps(item),flush=True)
        return s,item
    initial,_=stage('initial','import.tcl',source/'global.def',kind='DEF')
    for n in ['imported.def','imported.sdc']:shutil.copyfile(initial/n,d/n)
    def best():
        valid=[i for i in items if i['eligible']];require(valid,'No candidate retains internal electrical and 0.1 ns hold gates');return max(valid,key=rank)
    def optimize(name,tcl,current):
        opt=d/'optimizations'/name;opt.mkdir()
        for n in ['scripts','src']:shutil.copytree(d/n,opt/n)
        runenv=dict(env,CP3F_INPUT=str(candidates/current['name']/'global.odb'),CP3F_INPUT_KIND='DB')
        put(opt/'input.json',dict(parent=current['name']))
        print('STAGE: '+v+' '+name+' optimization',flush=True)
        rt[name+'-optimization']=command([openroad,'-no_init','-exit',ROOT/'flow'/tcl],opt/'run.log',cwd=opt,env=runenv,timeout=3600)
        put(d/'runtime.json',rt)
        require('CP3F_OPTIMIZER_COMPLETE' in (opt/'run.log').read_text(),'Optimizer incomplete')
        return stage(name,'route.tcl',opt/'optimized.odb',parent=current['name'])
    for cycle in range(1,MAX_CYCLES+1):
        current=best()
        if setup_met(current['timing']):break
        drivers=critical_drivers(candidates/current['name']/'reports',lib)
        if drivers:
            stage('buffer'+str(cycle),'repair.tcl',candidates/current['name']/'global.odb',parent=current['name'],drivers=drivers)
        current=best()
        if setup_met(current['timing']):break
        optimize('setup'+str(cycle),'optimize.tcl',current)
    current=best()
    if not setup_met(current['timing']):optimize('greedy','greedy.tcl',current)
    current=best()
    for cycle in range(1,9):
        if setup_met(current['timing']):break
        targets=capture_targets(candidates/current['name']/'reports')
        if not targets:break
        candidate,item=stage('skew'+str(cycle),'skew.tcl',candidates/current['name']/'global.odb',parent=current['name'],targets=targets)
        if item['timing']['worst_min_ns']<0.1:
            candidate,item=optimize('hold'+str(cycle),'hold.tcl',item)
        if not item['eligible']:break
        if rank(item)>rank(current) or item['timing']['tns_max_ns']>current['timing']['tns_max_ns']:
            current=item
        else:break
    chosen=best();selected=candidates/chosen['name']
    put(d/'selection.json',dict(selected=chosen['name'],policy='Require zero internal electrical violations and minimum slack >= 0.1 ns; select greatest maximum slack, then maximum TNS. CP3F acceptance separately requires nonnegative maximum slack and zero maximum TNS.',candidates=items))
    for n in ['global.def','global.odb','global.guide','propagated.sdc','final.v']:shutil.copyfile(selected/n,d/n)
    shutil.copytree(selected/'reports',d/'reports');shutil.copyfile(selected/'run.log',d/'cleanup.log');shutil.copyfile(initial/'run.log',d/'import.log')
    for name,tcl,log in [('power','power.tcl','power.log'),('verification','verify.tcl','independent-route.log')]:
        print('STAGE: '+v+' '+name,flush=True)
        rt[name]=command([openroad,'-no_init','-exit',ROOT/'flow'/tcl],d/log,cwd=d,env=env,timeout=1200)
        put(d/'runtime.json',rt)
    return rt

def inspect(repo,evidence,out,reference_mode='accepted_native_cp3e'):
    require(reference_mode in ['accepted_native_cp3e','same_tool_import'],'Invalid review reference mode')
    libpath=repo/'ihp13/pdk/lib/sg13g2_stdcell_typ_1p20V_25C.lib';lib=library(libpath);results={}
    for v in ['baseline','cnn']:
        d=out/v;before=evidence/v/'global.def';a,b=before.read_text(),(d/'imported.def').read_text()
        for sec in ['COMPONENTS','PINS']:
            require({n:norm(x) for n,x in records(a,sec).items()}=={n:norm(x) for n,x in records(b,sec).items()},'Import changed '+sec)
        copied_inputs=0
        for parent in [d]+[p for base in ['candidates','optimizations'] for p in (d/base).iterdir() if p.is_dir()]:
            for folder in ['scripts','src']:
                for p in (evidence/v/folder).rglob('*'):
                    if p.is_file():
                        rel=p.relative_to(evidence/v);require(sha(parent/rel)==sha(p),'Copied flow input changed: '+str(parent/rel));copied_inputs+=1
        logical=audit(before,d/'global.def',libpath);put(d/'logic-audit.json',logical)
        selection=audit_selection(d)
        ancestry=[];chosen=selection['selected'];byname={i['name']:i for i in selection['candidates']}
        while chosen!='initial':
            if chosen.startswith('skew'):ancestry.append(json.loads((d/'candidates'/chosen/'capture-plan.json').read_text()))
            chosen=byname[chosen]['parent']
        clock=clock_audit(before,d/'global.def',list(reversed(ancestry)));put(d/'clock-audit.json',clock)
        power=pdn(before,d/'global.def',libpath);put(d/'power-audit.json',power)
        require(sha(d/'original.sdc')==sha(evidence/v/'original.sdc'),'Reference constraints changed')
        sdc=constraints(d/'original.sdc',d/'propagated.sdc');put(d/'constraints-audit.json',sdc)
        route=routing(d);times=timing(d);fresh=independent(d,route,times);put(d/'independent-audit.json',fresh)
        native_old=json.loads((evidence/v/'summary.json').read_text())['timing']['final']
        old=native_old if reference_mode=='accepted_native_cp3e' else timing(d/'candidates/initial')['final']
        ea=inventory(before,evidence/v/'reports') if reference_mode=='accepted_native_cp3e' else inventory(d/'candidates/initial/global.def',d/'candidates/initial/reports')
        eb=inventory(d/'global.def',d/'reports');put(d/'electrical-before.json',ea);put(d/'electrical-after.json',eb)
        closed=setup_met(times['final'])
        if closed:improvement=progress(old,times['final'],ea,eb)
        else:
            require(admissible(times['final'],eb),'Selected candidate lost hold/electrical gates')
            improvement=dict(status='CP3F_SETUP_GATE_UNMET',worst_max_improvement_ns=times['final']['worst_max_ns']-old['worst_max_ns'],max_tns_improvement_ns=times['final']['tns_max_ns']-old['tns_max_ns'],estimated_setup_nonnegative=False,hold_margin_0_1_ns_met=True,timing_closed=False,electrical_closed=False)
        put(d/'progress-audit.json',improvement)
        warn=warnings(d);put(d/'warnings.json',warn);selection=audit_selection(d)
        for item in selection['candidates']:
            if item['name'].startswith('skew'):
                expected=dict(prefix='cp3f_'+item['name']+'_',targets=capture_targets(d/'candidates'/item['parent']/'reports'))
                require(json.loads((d/'candidates'/item['name']/'capture-plan.json').read_text())==expected,'Derived capture-clock plan changed')
                require((d/'candidates'/item['name']/'targets.txt').read_text()==''.join(x['instance']+'\n' for x in expected['targets']),'Capture target input changed')
                clock_audit(d/'candidates'/item['parent']/'global.def',d/'candidates'/item['name']/'global.def',[expected])
            if item['name'].startswith('buffer'):
                plan=json.loads((d/'candidates'/item['name']/'repair-plan.json').read_text())
                expected=critical_drivers(d/'candidates'/item['parent']/'reports',lib)
                require(plan==dict(drivers=expected),'Derived repair plan changed')
                require((d/'candidates'/item['name']/'drivers.txt').read_text()==''.join(x['instance']+'\n' for x in expected),'Critical-driver input changed')
            if item['name'].startswith(('setup','hold')) or item['name']=='greedy':
                require(json.loads((d/'optimizations'/item['name']/'input.json').read_text())==dict(parent=item['parent']),'Optimization parent changed')
        result=dict(status=('CP3F_CHECKS_PASS_SIGNOFF_PENDING' if reference_mode=='accepted_native_cp3e' else 'CP3F_LINUX_PREPARATORY_PASS_NATIVE_PENDING') if closed else 'CP3F_PHYSICAL_CHECKS_PASS_SETUP_GATE_UNMET',copied_flow_inputs_verified=copied_inputs,logic=logical,clock=clock,power=power,constraints=sdc,routing=route,timing=times,reference_timing=old,reference_mode=reference_mode,accepted_native_cp3e_timing=native_old,improvement=improvement,independent_verification=fresh,warning_counts=warn['by_code'],selected_candidate=selection['selected'],cp0c_electrical='PENDING',global_route_setup_gate_met=closed,detailed_routing_complete=False,timing_closed=False,electrical_closed=False)
        put(d/'summary.json',result);results[v]=result
    put(out/'comparison.json',results)
    require(all(r['global_route_setup_gate_met'] for r in results.values()),'CP3F setup gate unmet; both variant audits and comparison.json were retained')
    return results

def execute(repo,evidence,out,openroad):
    runtime={}
    for v in ['baseline','cnn']:
        runtime[v]=execute_variant(repo,evidence,out,openroad,v);put(out/'runtime.json',runtime)
    return inspect(repo,evidence,out)
