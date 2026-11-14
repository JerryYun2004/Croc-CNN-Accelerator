#!/usr/bin/env python3
"""Bounded child: cell comparisons or read-only I/O hierarchy audit."""
import json
from pathlib import Path
import sys
from cp0c_io_context_support import (db,CELL,FAULTS,require,write_json,load,isolate,graph,
    geometry,translate,apply_fault,compare,aliases_from_cdl,hierarchy_audit)

def main():
    mode=sys.argv[1]; cfg=json.loads(Path(sys.argv[2]).read_text()); out=Path(sys.argv[3])
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    if mode=='hierarchy':
        report=hierarchy_audit(cfg['full_database']); write_json(out/'hierarchy.json',report)
        print(f"IO_HIERARCHY_AUDITED: {report['io_masters']} masters; {report['counts']}",flush=True)
        return
    require(mode=='secondary','Unknown worker mode')
    measured=geometry(cfg['marker_gds']); write_json(out/'geometry.json',measured)
    write_json(out/'port-alias-evidence.json',aliases_from_cdl(cfg['io_cdl']))
    s=load(cfg['marker_database']); original=load(cfg['original_database'])
    oc=original.netlist().top_circuit()
    require(sum(1 for _ in oc.each_device())==3 and 'core,pad' in [p.name() for p in oc.each_pin()],
            'Original unmarked-cell prerequisite changed')
    a=isolate(s.netlist(),CELL); b=isolate(s.reference,CELL.upper())
    original_a,original_b=graph(a),graph(b)
    require(graph(isolate(original.reference,CELL.upper()))==original_b,
            'Original and marker-only probes used different reference graphs')
    write_json(out/'source-layout-graph.json',original_a); write_json(out/'source-reference-graph.json',original_b)
    results={}
    for variant in ('translated','baseline','spacing_only','tap_only',*FAULTS):
        aa,bb=a.dup(),b.dup()
        edits=translate(bb,measured,variant if variant in ('baseline','spacing_only','tap_only') else 'translated')
        before=graph(aa)
        if variant in FAULTS: apply_fault(aa,variant)
        after=graph(aa)
        require((before!=after)==(variant in FAULTS),'Fault control did not modify exactly its disposable graph')
        result=compare(aa,bb); result.update(variant=variant,reference_edits=edits,
            layout_graph=after,reference_graph=graph(bb),fault=variant if variant in FAULTS else None)
        if variant!='translated':
            require(not result['raw_match'],'Native comparer unexpectedly accepted '+variant)
        write_json(out/(variant+'.json'),result);results[variant]=result['strict_match']
        print(f"  {variant}: strict_match={result['strict_match']}",flush=True)
        if variant=='translated' and not result['strict_match']:
            write_json(out/'secondary.json',{'status':'IO_SECONDARY_REVIEW_REQUIRED',
                'outcomes':results,'negative_controls_meaningful':False,'cp0c':'PENDING'})
            return
    require(graph(a)==original_a and graph(b)==original_b,'Source graphs were modified')
    expected={'translated':True,**{n:False for n in ('baseline','spacing_only','tap_only',*FAULTS)}}
    require(results==expected,'Compatibility or post-match fault controls failed')
    write_json(out/'secondary.json',{'status':'IO_SECONDARY_DIAGNOSTIC_MATCH_VERIFIED',
        'outcomes':results,'negative_controls_meaningful':True,'negative_controls_pass':True,
        'fault_control_count':len(FAULTS),'original_missing_marker_reconfirmed':True,
        'source_graphs_unchanged':True,'cp0c':'PENDING'})
    print('IO_SECONDARY_DIAGNOSTIC_MATCH_VERIFIED',flush=True)

if __name__=='__main__': main()
