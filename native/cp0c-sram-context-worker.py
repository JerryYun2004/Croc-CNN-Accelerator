#!/usr/bin/env python3
"""Bounded child: audit the array context or replay one macro comparison."""
from collections import Counter
import json
from pathlib import Path
import sys
import time
import klayout.db as db
from cp0c_sram_context_support import (SRAM, ARRAY_R, require, write_json, isolate,
    summary, trace_context, verify_marker_masters, annotate_reference, comparator,
    reference_array_bulk_audit, pins, save_graph, read_graph, graph_digest, sha, structural_hints, dangling_gate_hints)

def load(path):
    state=db.LayoutVsSchematic();state.read(str(path))
    require(not list(state.each_error()),'Source extraction has errors')
    return state

def flatten_pair(state):
    a=isolate(state.netlist(),SRAM);b=isolate(state.reference,SRAM.upper())
    before=[summary(a),summary(b)]
    a.flatten();b.flatten()
    after=[summary(a),summary(b)]
    for row in after:
        require(row['device_count']==143422 and row['pins']==191 and row['subcircuits']==0,
                'Unexpected flattened macro inventory')
    return a,b,{'before':before,'after':after}

def apply_fault(nl,variant):
    if variant in ('baseline','translated'):return None
    c=nl.top_circuit()
    targets=sorted((d for d in c.each_device() if ARRAY_R.fullmatch(d.expanded_name())
                    and d.expanded_name().endswith('.0')),key=lambda d:d.expanded_name())
    require(targets,'No array R0 fault target');d=targets[0]
    row={'device':d.expanded_name(),'variant':variant}
    if variant=='wrong_width':
        row.update(old=d.parameter('W'),new=.21);d.set_parameter('W',.21)
    elif variant=='open':
        old=d.net_for_terminal('A');new=c.create_net('CP0C_CONTEXT_OPEN_CONTROL')
        d.connect_terminal('A',new);row.update(old=old.expanded_name(),new=new.expanded_name())
    elif variant=='wrong_layer':
        dc=next(x for x in nl.each_device_class() if x.name=='res_metal3')
        nd=c.create_device(dc,d.name)
        for t in ('A','B'):nd.connect_terminal(t,d.net_for_terminal(t))
        for p in d.device_class().parameter_definitions():nd.set_parameter(p.name,d.parameter(p.id()))
        row.update(old=d.device_class().name,new=dc.name);c.remove_device(d)
    else:raise RuntimeError('Unknown comparison variant')
    return row

def main():
    mode=sys.argv[1];cfg=json.loads(Path(sys.argv[2]).read_text());out=Path(sys.argv[3]);out.mkdir(exist_ok=True)
    require(db.__version__=='0.30.9','KLayout version differs')
    if mode=='prepare':
        state=load(cfg['database']);print('SOURCE_DATABASE_LOADED',flush=True)
        layout=db.Layout();layout.read(cfg['sram_gds']);require(layout.dbu==.001,'Unexpected GDS units')
        leaf=load(cfg['bitcell_database'])
        write_json(out/'marker-masters.json',verify_marker_masters(layout))
        trace_context(state,layout,leaf,out)
        print('SRAM_ARRAY_CONTEXT_VERIFIED',flush=True)
        a,b,inventory=flatten_pair(state);write_json(out/'macro-inventory.json',inventory)
        write_json(out/'reference-bulks.json',reference_array_bulk_audit(b))
        baseline=b.dup()
        write_json(out/'baseline-annotations.json',annotate_reference(baseline,False,out/'baseline-annotations.tsv'))
        write_json(out/'translation.json',annotate_reference(b,True,out/'translation.tsv'))
        # Assert interface/model pairing can be constructed before launching comparisons.
        comparator(a,b)
        graphs={}
        for name,nl in [('layout',a),('baseline-reference',baseline),('translated-reference',b)]:
            _,digest=save_graph(nl,out/(name+'.netlist'))
            graphs[name]={'graph_digest':digest,'file_sha256':sha(out/(name+'.netlist'))}
        write_json(out/'prepared.json',{'status':'SRAM_CONTEXT_PREPARED','graphs':graphs,'cp0c':'PENDING'})
        print('CP0C_SRAM_CONTEXT_PREPARED',flush=True)
        return
    variant=mode
    require(variant in ('baseline','translated','wrong_width','wrong_layer','open'),'Unknown worker mode')
    prepared=Path(cfg['prepared']);manifest=json.loads((prepared/'prepared.json').read_text())
    require(manifest['status']=='SRAM_CONTEXT_PREPARED','Preparation gate incomplete')
    def graph(name):
        path=prepared/(name+'.netlist');expected=manifest['graphs'][name]
        require(sha(path)==expected['file_sha256'],'Prepared graph file changed')
        nl=read_graph(path);require(graph_digest(nl)==expected['graph_digest'],'Prepared graph content changed')
        return nl
    a=graph('layout');b=graph('baseline-reference' if variant=='baseline' else 'translated-reference')
    inventory=json.loads((prepared/'macro-inventory.json').read_text())
    translation=json.loads((prepared/('baseline-annotations.json' if variant=='baseline' else 'translation.json')).read_text())
    print('REPLAY_GRAPHS_LOADED',flush=True)
    fault=apply_fault(b,variant)
    a,layout_digest=save_graph(a,out/'layout.netlist')
    print('LAYOUT_GRAPH_ROUNDTRIP_VERIFIED',flush=True)
    b,reference_digest=save_graph(b,out/'reference.netlist')
    print('REFERENCE_GRAPH_ROUNDTRIP_VERIFIED',flush=True)
    cmp=comparator(a,b)
    hints,refinement=structural_hints(a,b)
    write_json(out/'structural-hints.json',refinement)
    for an,bn in hints:cmp.same_nets(an,bn,True)
    print(f'STRICT_STRUCTURAL_NET_HINTS {len(hints)}',flush=True)
    dummy_hints,dummy_report=dangling_gate_hints(a,b)
    write_json(out/'dummy-gate-hints.json',dummy_report)
    for an,bn in dummy_hints:cmp.same_nets(an,bn,True)
    print(f'STRICT_DUMMY_GATE_HINTS {len(dummy_hints)}',flush=True)
    report={'variant':variant,'inventory':inventory,'translation':translation,'fault':fault,
            'strict_named_pin_constraints':191,'net_joins':0,'blackboxes':[],
            'new_extraction_performed':False,'full_chip_comparison':False,'cp0c':'PENDING',
            'saved_graph_digests':{'layout':layout_digest,'reference':reference_digest},
            'saved_graph_roundtrip':'CONNECTIVITY_AND_PARAMETERS_EXACT'}
    report['structural_refinement']=refinement
    report['dummy_gate_refinement']=dummy_report
    write_json(out/'configuration.json',report)
    print('MACRO_COMPARISON_STARTED '+variant,flush=True);start=time.monotonic()
    xref=db.NetlistCrossReference()
    raw=bool(cmp.compare(a,b,xref));elapsed=time.monotonic()-start
    print('MACRO_COMPARISON_RETURNED '+str(raw),flush=True)
    circuit_pairs=list(xref.each_circuit_pair())
    c=a.top_circuit();counts={};samples={}
    for name,fn in [('pins',xref.each_pin_pair),('nets',xref.each_net_pair),('devices',xref.each_device_pair)]:
        hist=Counter();bad=[]
        for pair in fn(c):
            status=str(pair.status());hist[status]+=1
            if pair.status()!=db.NetlistCrossReference.Match and len(bad)<20:
                aa,bb=pair.first(),pair.second()
                def label(x):
                    if x is None:return None
                    if hasattr(x,'expanded_name'):return x.expanded_name()
                    return x.name()
                bad.append({'layout':label(aa),'reference':label(bb),'status':status})
        counts[name]=dict(hist);samples[name]=bad
    exact=raw and len(circuit_pairs)==1 and circuit_pairs[0].status()==db.NetlistCrossReference.Match
    exact=exact and counts['pins']=={'Match':191}
    exact=exact and counts['devices']=={'Match':143422}
    exact=exact and set(counts['nets'])<={'Match','MatchWithWarning'} and sum(counts['nets'].values())==72373
    report.update(raw_match=raw,strict_match=bool(exact),compare_seconds=elapsed,
                  circuit_statuses=[str(p.status()) for p in circuit_pairs],
                  pair_counts=counts,mismatch_samples=samples,extraction_errors=[])
    write_json(out/'result.json',report)
    write_json(out/'graph-audit.json',{'status':'SAVED_MACRO_GRAPHS_AUDITED','strict_match':bool(exact),
                                     'layout_digest':layout_digest,'reference_digest':reference_digest})
    print('CP0C_SRAM_CONTEXT_WORKER_COMPLETE '+variant+' match='+str(exact),flush=True)

if __name__=='__main__': main()
