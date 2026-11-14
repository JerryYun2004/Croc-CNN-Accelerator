import shutil,json,re
from pathlib import Path
from common import command,put,require,sha
from environment import environment
from power import records,norm
from logic import audit,library
from checks import pdn,constraints,timing,routing,warnings,independent
from electrical import inventory,progress
ROOT=Path(__file__).resolve().parents[1]

def plan_sram_inputs(d):
    report=inventory(d/'refined.def',d/'reports',prefix='refined')
    targets=sorted({r['pin'] for c in report.values() for r in c['violations'] if r['domain']=='sram' and r['type']=='max slew'})
    (d/'sram-input-targets.txt').write_text('\n'.join(targets)+'\n')
    put(d/'sram-input-plan.json',dict(count=len(targets),targets=targets,criterion='Union of TT/FF reported SRAM input maximum-slew violations'))

def inspect(repo,evidence,out):
    lib=repo/'ihp13/pdk/lib/sg13g2_stdcell_typ_1p20V_25C.lib';results={}
    for v in ['baseline','cnn']:
        d=out/v;before=evidence/v/'global.def'
        a,b=before.read_text(),(d/'imported.def').read_text()
        for sec in ['COMPONENTS','PINS']:
            require({n:norm(x) for n,x in records(a,sec).items()}=={n:norm(x) for n,x in records(b,sec).items()},'Import changed '+sec)
        logical=audit(before,d/'global.def',lib);put(d/'logic-audit.json',logical)
        power=pdn(before,d/'global.def',lib);put(d/'power-audit.json',power)
        require(sha(d/'original.sdc')==sha(evidence/v/'original.sdc'),'Reference constraints changed')
        sdc=constraints(d/'original.sdc',d/'propagated.sdc');put(d/'constraints-audit.json',sdc)
        route=routing(d);times=timing(d);fresh=independent(d,route,times);put(d/'independent-audit.json',fresh)
        old=json.loads((evidence/v/'summary.json').read_text())['timing']['final']
        ea=inventory(before,evidence/v/'reports');eb=inventory(d/'global.def',d/'reports')
        put(d/'electrical-before.json',ea);put(d/'electrical-after.json',eb)
        improvement=progress(old,times['final'],ea,eb);put(d/'progress-audit.json',improvement)
        warn=warnings(d);put(d/'warnings.json',warn)
        for name,marker in [('tree.log','CP3D_PILOT_COMPLETE'),('setup.log','CP3D_OPTIMIZE_COMPLETE'),('implementation.log','CP3D_REFINEMENT_COMPLETE')]:
            text=(d/name).read_text();require(marker in text and '[ERROR' not in text,'Incomplete stage: '+name)
        text=(d/'implementation.log').read_text()
        require('CP3D_CLOCK_BRANCH_BUFFERS 8\n' in text,'Unexpected clock branch repair count')
        require('CP3D_SRAM_OUTPUT_BUFFERS '+('64' if v=='baseline' else '128')+'\n' in text,'Unexpected SRAM output repair count')
        plan=json.loads((d/'sram-input-plan.json').read_text())
        require('CP3D_SRAM_INPUT_BUFFERS '+str(plan['count'])+'\n' in (d/'cleanup.log').read_text(),'SRAM input repair plan not completed')
        result=dict(status='CP3D_CHECKS_PASS_CLOSURE_PENDING',logic=logical,power=power,constraints=sdc,routing=route,timing=times,reference_timing=old,improvement=improvement,independent_verification=fresh,warning_counts=warn['by_code'],cp0c_electrical='PENDING',detailed_routing_complete=False,timing_closed=False,electrical_closed=False)
        put(d/'summary.json',result);results[v]=result
    put(out/'comparison.json',results);return results

def execute_variant(repo,evidence,out,openroad,v,env=None):
        env=environment(repo) if env is None else dict(env)
        env['CP3D_FLOW']=str(ROOT/'flow')
        d=out/v;d.mkdir();source=evidence/v
        for n in ['scripts','src']:shutil.copytree(source/n,d/n)
        shutil.copyfile(source/'original.sdc',d/'original.sdc')
        env['CP3D_INPUT']=str(source/'global.def');rt={}
        for stage,tcl,log,timeout in [('tree','tree.tcl','tree.log',1800),('setup','setup.tcl','setup.log',3600),('implementation','implement.tcl','implementation.log',7200),('cleanup','cleanup.tcl','cleanup.log',1800),('power','power.tcl','power.log',1200),('verification','verify.tcl','independent-route.log',1200)]:
            if stage=='cleanup':plan_sram_inputs(d)
            print('STAGE: '+v+' '+stage,flush=True)
            rt[stage]=command([openroad,'-no_init','-exit',ROOT/'flow'/tcl],d/log,cwd=d,env=env,timeout=timeout)
            put(d/'runtime.json',rt)
        return rt

def execute(repo,evidence,out,openroad):
    runtime={}
    for v in ['baseline','cnn']:
        runtime[v]=execute_variant(repo,evidence,out,openroad,v)
        put(out/'runtime.json',runtime)
    return inspect(repo,evidence,out)
