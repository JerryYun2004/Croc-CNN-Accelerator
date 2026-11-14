import re
from base_support import require
from model import compare_report, erc_rows

CASES=[
    dict(name='dynamic_1pf_fast',load=1,slew=.02,oe='dynamic'),
    dict(name='dynamic_3pf_fast',load=3,slew=.02,oe='dynamic'),
    dict(name='dynamic_3pf_nominal',load=3,slew=.33,oe='dynamic'),
    dict(name='dynamic_3pf_slow',load=3,slew=1.26,oe='dynamic'),
    dict(name='dynamic_7pf_nominal',load=7,slew=.33,oe='dynamic'),
    dict(name='transmit_3pf',load=3,slew=.33,oe=1),
    dict(name='receive_3pf',load=3,slew=.33,oe=0),
    dict(name='control_excess_load',load=15,slew=.33,oe=1),
    dict(name='control_excess_input_slew',load=3,slew=4,oe=1),
]

def collect(folder,case,models):
    reports=folder/'reports'; arcs=[]; erc=[]; paths=[]; pad=[]
    units=(reports/'units.rpt').read_text()
    require(re.search(r'\btime\s+1ns\b',units) and re.search(r'\bcapacitance\s+1pF\b',units),'Unexpected native units')
    for corner in ('tt','ff'):
        erc += [dict(corner=corner,**r) for r in erc_rows((reports/f'{corner}-erc.rpt').read_text())]
        pad += [dict(corner=corner,**r) for r in erc_rows((reports/f'{corner}-pad-checks.rpt').read_text())]
        for kind in ('data','enable','receive'):
            text=(reports/'paths'/f'{kind}-{corner}.rpt').read_text()
            paths.append(dict(corner=corner,kind=kind,count=text.count('Startpoint:')))
        if case['oe']=='dynamic':
            for kind,kinds in [('data',['data']),('enable',['enable','disable'])]:
                data=compare_report(models[corner],(reports/'arcs'/f'{corner}-{kind}.rpt').read_text(),kinds)
                for r in data:
                    require(abs(r['input_slew_ns']-case['slew'])<1e-4,'Fixture input slew differs from requested I/O 10–90% value')
                    require(case['load']<=r['total_cap_pf']<case['load']+.5,'Unexpected fixture capacitance')
                    require(r['within_characterized_domain'],'Dynamic sample is outside characterized table domain')
                arcs += [dict(corner=corner,**r) for r in data]
    sdc=(folder/'out/runtime.sdc').read_text()
    require(not re.search(r'^set_(?:disable_timing|false_path|max_transition|max_capacitance)\b',sdc,re.M),'Fixture contains a timing exception or altered limit')
    return dict(arcs=arcs,erc=erc,pad_checks=pad,paths=paths,
                check_setup=(reports/'check-setup.rpt').read_text())

def gate(results,models):
    """Keep reproduction gates separate from chip or electrical closure."""
    def violation(name,kind,pins,corner='tt'):
        return any(r['corner']==corner and r['check']==kind and r['pin'] in pins and r['violated']
                   for r in results[name]['erc'])
    def count(name,kind,corner='tt'):
        return next(r['count'] for r in results[name]['paths'] if r['corner']==corner and r['kind']==kind)
    nominal=results['dynamic_3pf_nominal']['arcs']
    disable=[r for r in nominal if r['corner']=='tt' and r['arc']=='disable']
    normal=[r for r in nominal if r['corner']=='tt' and r['arc'] in ('data','enable')]
    limit=models['tt']['pad_max_transition_ns']
    tests={
        'dynamic_disable_both_edges_above_limit':len(disable)==2 and all(r['slew_ns']>limit for r in disable),
        'nominal_data_and_enable_below_limit':len(normal)==4 and all(r['slew_ns']<limit for r in normal),
        'dynamic_pad_slew_violation_detected':violation('dynamic_3pf_nominal','max slew',{'u_pad/pad'}),
        'transmit_has_no_erc_violations':not results['transmit_3pf']['erc'],
        'receive_has_no_erc_violations':not results['receive_3pf']['erc'],
        'excess_load_detected':violation('control_excess_load','max capacitance',{'u_pad/pad'}),
        'excess_input_slew_detected':violation('control_excess_input_slew','max slew',{'u_pad/c2p','data_i'}),
    }
    # Fixed modes remove output-disable transitions; no implication for turnaround.
    for corner in ('tt','ff'):
        tests['transmit_paths_'+corner]=count('transmit_3pf','data',corner)>0
        tests['receive_paths_'+corner]=count('receive_3pf','receive',corner)>0 and count('receive_3pf','data',corner)==0
        tests['dynamic_data_paths_'+corner]=count('dynamic_3pf_nominal','data',corner)>0
    pad=[r for r in results['dynamic_3pf_nominal']['pad_checks'] if r['corner']=='tt' and r['pin']=='u_pad/pad' and r['check']=='max slew']
    tests['reported_slew_matches_disable_arc']=len(pad)==1 and abs(pad[0]['actual']-max(r['slew_ns'] for r in disable))<1e-4
    return dict(verified=all(tests.values()),tests=tests)
