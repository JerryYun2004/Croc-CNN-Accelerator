import json,re,shutil,traceback
from pathlib import Path
from common import command,put,require,sha
from environment import environment
from physical import preserve,route_audit,complete,warning_inventory
from timing import constraints,timing
from electrical import inventory
from spef import audit as spef_audit
from annotation import audit as annotation_audit
ROOT=Path(__file__).resolve().parents[1]

def run_stage(d,name,tcl,openroad,env,timeout=1800):
    print('STAGE: '+d.parent.name+'/'+d.name+' '+name,flush=True)
    result=command([openroad,'-no_init','-exit',ROOT/'flow'/tcl],d/(name+'.log'),cwd=d,env=env,timeout=timeout)
    return result

def execute_variant(repo,evidence,out,openroad,v,env=None):
    env=environment(repo) if env is None else dict(env)
    env['CP3G_FLOW']=str(ROOT/'flow')
    d=out/v;d.mkdir();source=evidence/v;rt={}
    for n in ['scripts','src']:shutil.copytree(source/n,d/n)
    for n in ['global.def','global.guide','original.sdc']:
        shutil.copyfile(source/n,d/('input.def' if n=='global.def' else 'input.guide' if n=='global.guide' else n))
    runenv=dict(env,CP3G_INPUT_DEF=str(d/'input.def'),CP3G_INPUT_GUIDE=str(d/'input.guide'))
    rt['route']=run_stage(d,'route','route.tcl',openroad,runenv,14400);put(d/'runtime.json',rt)
    # All following tools read the emitted detailed DEF or DB in fresh processes.
    runenv.update(CP3G_INPUT_DEF=str(d/'routed.def'),CP3G_INPUT_DB=str(d/'routed.odb'),CP3G_SPEF=str(d/'routed.spef'))
    for name,tcl in [('power','power.tcl'),('independent-drc','drc.tcl'),('antenna','antenna.tcl'),('extraction','extract.tcl'),('timing','timing.tcl')]:
        rt[name]=run_stage(d,name,tcl,openroad,runenv,3600);put(d/'runtime.json',rt)
    fresh=d/'independent';fresh.mkdir()
    for n in ['scripts','src']:shutil.copytree(source/n,fresh/n)
    # Re-extract the emitted DEF; never use saved parasitics or the first SPEF.
    rt['independent-extraction']=run_stage(fresh,'extraction','extract.tcl',openroad,runenv,3600)
    rt['independent-timing']=run_stage(fresh,'timing','timing.tcl',openroad,dict(runenv,CP3G_SPEF=str(fresh/'routed.spef')),3600)
    put(d/'runtime.json',rt)
    return inspect_variant(repo,evidence,d,v)

def inspect_variant(repo,evidence,d,v):
    print('STAGE: '+v+' independent evidence audits',flush=True)
    source=evidence/v;inputs=0
    for parent in [d,d/'independent']:
        for folder in ['scripts','src']:
            for p in (source/folder).rglob('*'):
                if p.is_file():
                    require(sha(parent/p.relative_to(source))==sha(p),'Copied flow input changed');inputs+=1
    for old,new in [('global.def','input.def'),('global.guide','input.guide'),('original.sdc','original.sdc')]:require(sha(source/old)==sha(d/new),'Accepted CP3F input changed: '+old)
    original=preserve(source/'global.def',d/'imported.def')
    design=preserve(source/'global.def',d/'routed.def');put(d/'preservation-audit.json',design)
    sdc=constraints(d/'original.sdc',d/'propagated.sdc');constraints(d/'original.sdc',d/'imported.sdc');constraints(d/'original.sdc',d/'independent/propagated.sdc');put(d/'constraints-audit.json',sdc)
    route=route_audit(d);put(d/'routing-audit.json',route)
    annotations=[]
    print('AUDIT: '+v+' physical design, constraints, routing and power checked; checking annotation and SPEF',flush=True)
    for folder in [d,d/'independent']:
        elog=complete(folder/'extraction.log','CP3G_LEF_RC_EXTRACTION_COMPLETE');tlog=complete(folder/'timing.log','CP3G_SPEF_TIMING_COMPLETE');annotations.append({corner:annotation_audit(folder/('annotation-'+corner+'.rpt'),d/'routed.def') for corner in ['tt','ff']})
        require(not re.search(r'\[WARNING (?:RCX|ODB|STA)-',elog+tlog),'Extraction or annotation warning requires review')
    require(annotations[0]==annotations[1],'Fresh-process annotation differs');put(d/'annotation-audit.json',annotations[0])
    extracted=spef_audit(d/'routed.spef',d/'routed.def');fresh=spef_audit(d/'independent/routed.spef',d/'routed.def')
    require(extracted==fresh,'Fresh-process extraction differs');put(d/'extraction-audit.json',extracted)
    print('AUDIT: '+v+' required internal SPEF coverage and connectivity verified; checking timing and electrical reports',flush=True)
    times=timing(d);independent=timing(d/'independent')
    require(all(abs(times['final'][k]-independent['final'][k])<=1e-6 for k in times['final']),'Fresh-process SPEF timing differs')
    verify=dict(status='FRESH_DEF_EXTRACTION_AND_SPEF_TIMING_VERIFIED',canonical_spef_sha256=extracted['canonical_sha256'],timing_agrees_to_1e_6_ns=True)
    put(d/'independent-audit.json',verify)
    electrical=inventory(d/'routed.def',d/'reports');put(d/'electrical.json',electrical)
    native_old=json.loads((source/'summary.json').read_text())['timing']['final'];new=times['final']
    gates=dict(setup_nonnegative=new['worst_max_ns']>=0 and new['tns_max_ns']>=-1e-6,hold_nonnegative=new['worst_min_ns']>=0 and new['tns_min_ns']>=-1e-6,hold_margin_0_1_ns_met=new['worst_min_ns']>=0.1,internal_electrical_zero=all(x['internal']==0 for c in electrical.values() for x in c['summary'].values()))
    warnings=warning_inventory(d);put(d/'warnings.json',warnings)
    complete(d/'antenna.log','CP3G_ANTENNA_CHARACTERIZATION_COMPLETE')
    antenna_text=(d/'antenna.log').read_text()
    antenna_counts={kind:re.findall(r'Found (\d+) '+kind+r' violations\.',antenna_text) for kind in ['net','pin']}
    require(all(len(x)==1 for x in antenna_counts.values()),'Antenna violation counts missing')
    antenna=dict(scope='LEF antenna characterization; IO abstract antenna data incomplete, no antenna repair applied',net_violations=int(antenna_counts['net'][0]),pin_violations=int(antenna_counts['pin'][0]),full_antenna_signoff=False,report_sha256=sha(d/'antenna.rpt'))
    result=dict(status='CP3G_ROUTE_CHECKS_PASS_REVIEW_REQUIRED' if route['route_gate_met'] else 'CP3G_ROUTE_GATE_UNMET',route_gate_met=route['route_gate_met'],copied_inputs_verified=inputs,design_preservation=design,constraints=sdc,routing=route,extraction=extracted,annotation=annotations[0],independent_verification=verify,timing=times,timing_gates=gates,cp3f_native_global_route_timing=native_old,timing_change_ns={k:new[k]-native_old[k] for k in new},electrical_summary={c:r['summary'] for c,r in electrical.items()},antenna=antenna,warning_counts=warnings['by_code'],calibrated_extraction=False,timing_closed=False,electrical_closed=False,cp0c_electrical='PENDING',full_foundry_drc_verified=False,full_lvs_verified=False)
    put(d/'summary.json',result);print('RESULT: '+v+' '+result['status']+' '+json.dumps(new),flush=True);return result

def execute(repo,evidence,out,openroad,env=None):
    results={};errors={}
    for v in ['baseline','cnn']:
        try:results[v]=execute_variant(repo,evidence,out,openroad,v,env)
        except Exception as e:
            errors[v]=str(e);put(out/v/'failure.json',dict(error=str(e),traceback=traceback.format_exc()));print('VARIANT STOPPED: '+v+' '+str(e),flush=True)
    put(out/'comparison.json',dict(variants=results,errors=errors))
    return results,errors
