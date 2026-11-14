import shutil,json
from pathlib import Path
from common import command,put,require
from environment import environment
from power import records,norm
from logic import audit
from checks import cts,pdn,constraints,timing,routing,warnings,independent
ROOT=Path(__file__).resolve().parents[1]

def inspect(repo,evidence,out):
    lib=repo/'ihp13/pdk/lib/sg13g2_stdcell_typ_1p20V_25C.lib';results={}
    for v in ['baseline','cnn']:
        d=out/v;before=evidence/v/'global.def'
        a,b=before.read_text(),(d/'imported.def').read_text()
        for sec in ['COMPONENTS','PINS']:
            require({n:norm(x) for n,x in records(a,sec).items()}=={n:norm(x) for n,x in records(b,sec).items()},'Import changed '+sec)
        logical=audit(before,d/'global.def',lib);put(d/'logic-audit.json',logical)
        power=pdn(before,d/'global.def',lib);put(d/'power-audit.json',power)
        sdc=constraints(d/'original.sdc',d/'propagated.sdc');put(d/'constraints-audit.json',sdc)
        route=routing(d);clock=cts(d);times=timing(d);fresh=independent(d,route,times);put(d/'independent-audit.json',fresh)
        warn=warnings(d);put(d/'warnings.json',warn)
        result=dict(status='CP3C_PHYSICAL_CHECKS_PASS_TIMING_PENDING',clock_tree=clock,logic=logical,power=power,
            constraints=sdc,routing=route,timing=times,independent_verification=fresh,warning_counts=warn['by_code'],
            cp0c_electrical='PENDING',detailed_routing_complete=False,timing_closed=False,electrical_closed=False)
        put(d/'summary.json',result);results[v]=result
    results['delta']=dict(guided_nets=results['cnn']['routing']['guided_nets']-results['baseline']['routing']['guided_nets'],
        global_wirelength_um=results['cnn']['routing']['global_wirelength_um']-results['baseline']['routing']['global_wirelength_um'],
        final_max_slack_ns=results['cnn']['timing']['final']['worst_max_ns']-results['baseline']['timing']['final']['worst_max_ns'])
    put(out/'comparison.json',results);return results

def execute(repo,evidence,out,openroad):
    env=environment(repo);env['CP3C_FLOW']=str(ROOT/'flow');runtime={}
    for variant in ['baseline','cnn']:
        d=out/variant;d.mkdir();source=evidence/variant
        for n in ['scripts','src']:shutil.copytree(source/n,d/n)
        env['CP3C_DEF']=str(source/'global.def')
        print('STAGE: '+variant+' clock-tree synthesis and initial timing',flush=True)
        rt={};rt['cts']=command([openroad,'-no_init','-exit',ROOT/'flow/cts.tcl'],d/'cts.log',cwd=d,env=env,timeout=1800)
        print('STAGE: '+variant+' fanout repair, bounded timing repair and global routing',flush=True)
        rt['implementation']=command([openroad,'-no_init','-exit',ROOT/'flow/implement.tcl'],d/'implementation.log',cwd=d,env=env,timeout=7200)
        print('STAGE: '+variant+' explicit cell supplies and four-network connectivity',flush=True)
        rt['power']=command([openroad,'-no_init','-exit',ROOT/'flow/power.tcl'],d/'power.log',cwd=d,env=env,timeout=1200)
        print('STAGE: '+variant+' independent final routing and parasitics verification',flush=True)
        rt['verification']=command([openroad,'-no_init','-exit',ROOT/'flow/verify.tcl'],d/'independent-route.log',cwd=d,env=env,timeout=1200)
        runtime[variant]=rt;put(out/'runtime.json',runtime)
    print('STAGE: logic, PDN, constraints, clock and route audits',flush=True)
    return inspect(repo,evidence,out)
