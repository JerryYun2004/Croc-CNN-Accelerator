#!/usr/bin/env python3
"""Prepare parent fixtures or compare the freshly extracted native databases."""
import json
from pathlib import Path
import sys
from cp0c_io_parent_support import (db,MODELS,FAULTS,require,write_json,prepare,graph,compare,apply_fault)

def read(path):
    state=db.LayoutVsSchematic();state.read(str(path))
    require(not list(state.each_error()),'Saved extraction has errors')
    a,b=state.netlist().dup(),state.reference.dup()
    require(len(a.top_circuits())==len(b.top_circuits())==1,'Saved top count differs')
    return a,b

def main():
    mode=sys.argv[1];cfg=json.loads(Path(sys.argv[2]).read_text());out=Path(sys.argv[3])
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    if mode=='prepare':
        result=prepare(cfg['io_gds'],cfg['io_reader_cdl'],out)
        write_json(out/'prepared.json',result);print('IO_PARENT_FIXTURES_PREPARED',flush=True);return
    require(mode=='compare','Unknown worker stage')
    probes=Path(cfg['probes']);out.mkdir(exist_ok=True)
    # The marker-only parent extraction must be non-empty and contain all nine devices.
    baseline_a,baseline_b=read(probes/'marker/translated.lvsdb')
    source_graphs=[graph(baseline_a),graph(baseline_b)]
    from collections import Counter
    for nl in (baseline_a,baseline_b):
        require(dict(Counter(d.device_class().name.lower() for d in nl.top_circuit().each_device()))==MODELS,
                'Translated parent model inventory differs')
        require(not list(nl.top_circuit().each_subcircuit()),'Comparison requires a completely flattened parent')
    outcomes={};raw_outcomes={}
    cases=('translated','unmarked','untranslated','unpropagated',*FAULTS)
    for variant in cases:
        if variant=='unmarked':a,b=read(probes/'original/translated.lvsdb')
        elif variant in ('untranslated','unpropagated'):a,b=read(probes/'marker'/(variant+'.lvsdb'))
        else:a,b=baseline_a.dup(),baseline_b.dup()
        before=graph(a)
        if variant in FAULTS:apply_fault(a,variant)
        after=graph(a);require((before!=after)==(variant in FAULTS),'Invalid fault mutation')
        result=compare(a,b);result.update(variant=variant,layout=after,reference=graph(b),
            fault=variant if variant in FAULTS else None)
        write_json(out/(variant+'.json'),result)
        outcomes[variant]=result['strict_match'];raw_outcomes[variant]=result['raw_match']
        print(f"  {variant}: strict_match={result['strict_match']}",flush=True)
        if variant=='translated' and not result['strict_match']:
            write_json(out/'conclusion.json',{'status':'IO_PARENT_REVIEW_REQUIRED','outcomes':outcomes,
                'negative_controls_meaningful':False,'cp0c':'PENDING'})
            return
        if variant!='translated':require(not result['raw_match'],'Native comparer accepted failing control '+variant)
    require([graph(baseline_a),graph(baseline_b)]==source_graphs,'Source comparison graphs changed')
    expected={'translated':True,**{k:False for k in cases if k!='translated'}}
    require(outcomes==expected and raw_outcomes==expected,'Parent comparison/control gate failed')
    write_json(out/'conclusion.json',{'status':'IO_PARENT_DIAGNOSTIC_MATCH_VERIFIED',
        'outcomes':outcomes,'negative_controls_meaningful':True,'negative_controls_pass':True,
        'fault_controls':len(FAULTS),'context_controls':3,'source_graphs_unchanged':True,
        'full_pad_lvs':False,'full_chip_lvs':False,'cp0c':'PENDING'})
    print('IO_PARENT_DIAGNOSTIC_MATCH_VERIFIED',flush=True)

if __name__=='__main__':main()
