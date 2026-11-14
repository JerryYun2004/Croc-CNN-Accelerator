#!/usr/bin/env python3
"""Prepare both signal-pad fixtures or audit and compare their native extractions."""
import json,sys
from pathlib import Path
from collections import Counter
from cp0c_io_signal_support import *

def read(path):
    state=db.LayoutVsSchematic();state.read(str(path))
    require(not list(state.each_error()),'Saved extraction contains errors')
    a,b=state.netlist().dup(),state.reference.dup()
    require(len(a.top_circuits())==len(b.top_circuits())==1,'Saved top count differs')
    return a,b

def audit(path,target,variant,out):
    x=json.loads(Path(path).read_text())
    require(x['milestone']=='complete' and x['net_joins']==0 and not x['extraction_errors']
            and x['implicit_connections']==[] and x['blackboxes']==[],'Extraction not complete/strict')
    raw=dict(target['raw_models'])
    if variant=='unmarked':raw.pop('rppd')
    actual=dict(Counter(d['model'].lower() for d in x['before_combine']['devices']))
    require(actual==raw,'Unexpected physical device inventory before reduction: expected '+str(raw)+'; got '+str(actual))
    # Individually extracted driver fingers, independently counted in the pinned GDS.
    gpio=target['pad'].endswith('InOut30mA')
    for model,count,width in (('sg13_hv_nmos',15 if gpio else 8,4.4),
                              ('sg13_hv_pmos',30 if gpio else 16,6.66)):
        fingers=[d for d in x['before_combine']['devices'] if d['model'].lower()==model and
                 abs(d['parameters']['L']-.6)<1e-8]
        require(len(fingers)==count and all(abs(d['parameters']['W']-width)<1e-8 for d in fingers),
                'Raw driver finger geometry differs: '+model)

    result={'layout':reduction_audit(x['before_combine'],x['after_combine']),
            'references':{r['name']:reduction_audit(r['before_combine'],r['after_combine']) for r in x['references']}}
    write_json(out,result);return result

def compare_target(key,probes,out):
    t=TARGETS[key];out.mkdir(exist_ok=True)
    variants=('candidate','unmarked') if key=='gpio' else ('candidate',)
    for v in variants:audit(probes/v/'extraction.json',t,v,out/(v+'-reduction.json'))
    baseline_a,baseline_b=read(probes/'candidate/translated.lvsdb');original=[graph(baseline_a),graph(baseline_b)]
    for nl in (baseline_a,baseline_b):
        require(not list(nl.top_circuit().each_subcircuit()),'Netlist not fully flattened')
        require(dict(Counter(d.device_class().name.lower() for d in nl.top_circuit().each_device()))==t['models'],
                'Positive model inventory differs')
    contexts=['untranslated','unpropagated']+(['unmarked'] if key=='gpio' else [])
    faults=faults_for(key);cases=['translated',*contexts,*faults];outcomes={};raw={}
    for v in cases:
        if v=='unmarked':a,b=read(probes/'unmarked/translated.lvsdb')
        elif v in contexts:a,b=read(probes/'candidate'/(v+'.lvsdb'))
        else:a,b=baseline_a.dup(),baseline_b.dup()
        before=graph(a)
        if v in faults:apply_fault(a,v)
        after=graph(a);require((before!=after)==(v in faults),'Fault mutation invalid')
        r=compare(a,b,t['ports']+['sub!'],t['devices']);r.update(variant=v,layout=after,reference=graph(b))
        write_json(out/(v+'.json'),r);outcomes[v]=r['strict_match'];raw[v]=r['raw_match']
        print(f"  {key}/{v}: strict_match={r['strict_match']}",flush=True)
        if v=='translated' and not r['strict_match']:
            final={'status':'IO_SIGNAL_PAD_REVIEW_REQUIRED','target':key,'outcomes':outcomes,
                   'negative_controls_meaningful':False,'cp0c':'PENDING'}
            write_json(out/'conclusion.json',final);return final
        if v!='translated':require(not r['raw_match'],'Comparer accepted negative control '+key+'/'+v)
    require(original==[graph(baseline_a),graph(baseline_b)],'Source graph mutation')
    expected={v:v=='translated' for v in cases};require(raw==outcomes==expected,'Comparison gate failed')
    final={'status':'IO_SIGNAL_PAD_DIAGNOSTIC_MATCH_VERIFIED','target':key,'outcomes':outcomes,
        'fault_controls':len(faults),'context_controls':len(contexts),'negative_controls_pass':True,
        'source_graphs_unchanged':True,'full_chip_lvs':False,'cp0c':'PENDING'}
    write_json(out/'conclusion.json',final);return final

def main():
    mode=sys.argv[1];cfg=json.loads(Path(sys.argv[2]).read_text());out=Path(sys.argv[3]);out.mkdir(exist_ok=True)
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    if mode=='prepare':
        write_json(out/'prepared.json',prepare(cfg['io_gds'],cfg['io_reader_cdl'],out));print('IO_SIGNAL_FIXTURES_PREPARED');return
    require(mode=='compare','Unknown worker stage')
    # Retain both results even if a target needs review; never count negative failures as a pass without its positive.
    results={};failures={}
    for key in TARGETS:
        try:results[key]=compare_target(key,Path(cfg['probes'])/key,out/key)
        except Exception as exc:
            failures[key]=f'{type(exc).__name__}: {exc}'
            write_json(out/(key+'-failure.json'),{'error':failures[key]})
            print('REVIEW '+key+': '+failures[key],flush=True)
    passed=not failures and all(r['status']=='IO_SIGNAL_PAD_DIAGNOSTIC_MATCH_VERIFIED' for r in results.values())
    final={'status':'IO_SIGNAL_DIAGNOSTIC_MATCH_VERIFIED' if passed else 'IO_SIGNAL_REVIEW_REQUIRED',
        'targets':results,'failures':failures,'full_chip_lvs':False,'candidate_promoted':False,'cp0c':'PENDING'}
    write_json(out/'conclusion.json',final);print(final['status'],flush=True)

if __name__=='__main__':main()
