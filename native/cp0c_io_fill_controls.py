"""Compare the four separate physical tap boundaries; reject injected faults."""
from cp0c_io_fill_geometry import *

def faults():
    return [p+'__'+op for p in TAP_PORTS for op in
        ('area','perimeter','missing','reverse','tie_open','bulk_open','wrong_tie')]+[
        'port_open__'+p for p in PORTS]+['port_swap','merge_iovss_ties']

def mutate(nl,fault):
    c=nl.top_circuit();pins=named_pins(c)
    if fault.startswith('port_open__'):
        name=fault.split('__')[1];p=next(p for p in c.each_pin() if p.name().lower()==name)
        c.connect_pin(p.id(),c.create_net('FAULT_OPEN'))
    elif fault=='port_swap':
        ids={p.name().lower():p.id() for p in c.each_pin()}
        c.connect_pin(ids['m1_t2'],pins['m1_t6']);c.connect_pin(ids['m1_t6'],pins['m1_t2'])
    elif fault=='merge_iovss_ties':
        for d in c.each_device():
            if d.net_for_terminal('TIE').expanded_name() in [pins[p].expanded_name() for p in TAP_PORTS[:3]]:
                d.connect_terminal('TIE',pins['m1_t2'])
    else:
        name,op=fault.split('__')
        ds=[d for d in c.each_device() if d.net_for_terminal('TIE').expanded_name()==pins[name].expanded_name()]
        require(len(ds)==1,'Ambiguous tap fault target');d=ds[0]
        if op in ('area','perimeter'):
            p='A' if op=='area' else 'P';d.set_parameter(p,d.parameter(p)+.1)
        elif op=='missing':c.remove_device(d)
        elif op=='reverse':
            a,b=d.net_for_terminal('TIE'),d.net_for_terminal('WELL');d.connect_terminal('TIE',b);d.connect_terminal('WELL',a)
        elif op.endswith('_open'):d.connect_terminal('TIE' if op=='tie_open' else 'WELL',c.create_net('FAULT_OPEN'))
        elif op=='wrong_tie':d.connect_terminal('TIE',pins[TAP_PORTS[(TAP_PORTS.index(name)+1)%4]])
        else:raise RuntimeError('Unknown fault')

def read_db(path):
    state=db.LayoutVsSchematic();state.read(str(path))
    require(not list(state.each_error()),'Saved extraction has errors')
    a,b=state.netlist().dup(),state.reference.dup()
    require(len(a.top_circuits())==len(b.top_circuits())==1,'Expected one top circuit')
    return a,b

def audit_extraction(x,geometry):
    require(x['milestone']=='complete' and x['net_joins']==0 and x['extraction_errors']==[] and
            x['implicit_connections']==[] and x['blackboxes']==[] and x['tap_extraction_enabled'],
            'Extraction completion/strictness differs')
    require(x['before_combine']==x['after_combine'],'Device extraction changed during audit')
    before=x['before_combine'];require(len(before['nets'])==5 and len(before['pins'])==4,'Raw boundary count differs')
    require(set(tuple(p) for p in before['pins'])=={(p,p) for p in TAP_PORTS},'Raw ports differ')
    devices=before['devices'];require(len(devices)==4 and all(d['model']=='ptap1' for d in devices),'Raw device count differs')
    require({d['terminals']['TIE'] for d in devices}==set(TAP_PORTS),'Tap tie ports differ')
    for d in devices:
        require(set(d['parameters'])=={'A','P'} and set(d['terminals'])=={'TIE','WELL'},'Tap model changed')
        tap=geometry['tap_geometry'][d['terminals']['TIE']]
        require(all(abs(d['parameters'][p]-tap[p])<=1e-8 for p in ('A','P')),'Extraction disagrees with measured tap geometry')
    wells={d['terminals']['WELL'] for d in devices}
    require(wells=={x['substrate_net_key']} and not wells&set(TAP_PORTS),'Substrate boundary differs')
    require(len(x['substrate_witnesses'])==4 and
            {w['net'] for w in x['substrate_witnesses']}==wells,'Substrate witness coverage differs')
    require(len(x['references'])==1 and x['references'][0]['name']=='boundary','Reference set differs')
    ref=x['references'][0];require(ref['before_combine']==ref['after_combine'],'Reference devices were combined')
    require(geometry['source_masks_unchanged'] and geometry['new_mask_shapes']==0 and geometry['net_joins']==0,
            'Source masks changed')
    mc=geometry['physical_controls']
    track_names={f'{PREFIX[a]}_t{i}' for a,count in LAYERS.items() for i in range(count)}
    require(set(mc['opened']['components'])==track_names and
            set(geometry['physical_connectivity']['components'])==track_names and
            all(v==1 for v in geometry['physical_connectivity']['components'].values()),
            'Physical track coverage differs')
    require(mc['status']=='TRACK_CONTROLS_VERIFIED' and mc['open_tracks_rejected']==27 and
            mc['short_controls_rejected']==23 and len(mc['adjacent_short_controls'])==23 and
            all(r['rejected'] for r in mc['adjacent_short_controls']) and
            all(n==2 for n in mc['opened']['components'].values()),'Physical controls incomplete')
    return {'status':'RAW_TAPS_AND_TRACKS_VERIFIED','devices':4,'boundary_pins':5,'nets':5,
            'tap_geometry_match':True,'net_joins':0,'device_reductions':0,
            'track_open_assertions':27,'physical_short_controls':23}

def compare_target(key,probes,geometry,out):
    out.mkdir(parents=True,exist_ok=True)
    x=json.loads((probes/'candidate/extraction.json').read_text())
    write_json(out/'extraction-audit.json',audit_extraction(x,geometry))
    base_a,base_b=read_db(probes/'candidate/boundary.lvsdb');original=[graph(base_a),graph(base_b)]
    for nl in (base_a,base_b):
        require(not list(nl.top_circuit().each_subcircuit()),'Unexpected subcircuit')
        require(Counter(d.device_class().name.lower() for d in nl.top_circuit().each_device())=={'ptap1':4},'Unexpected device inventory')
    outcomes={}
    for case in ['boundary',*faults()]:
        a,b=base_a.dup(),base_b.dup();before=graph(a)
        if case!='boundary':mutate(a,case)
        after=graph(a);require((before!=after)==(case!='boundary'),'Fault did not change the graph')
        r=compare(a,b,PORTS,4);r.pop('resistor_symmetry',None)
        r.update(variant=case,layout=after,reference=graph(b));write_json(out/(case+'.json'),r)
        outcomes[case]=r['strict_match']
        require(r['raw_match']==r['strict_match']==(case=='boundary'),'Unexpected comparison result: '+key+'/'+case)
    require(original==[graph(base_a),graph(base_b)],'Source graph was mutated')
    final={'status':'IO_FILL_CELL_DIAGNOSTIC_MATCH_VERIFIED','target':key,'outcomes':outcomes,
        'negative_controls':len(faults()),'source_graphs_unchanged':True,'full_chip_lvs':False,'cp0c':'PENDING'}
    write_json(out/'conclusion.json',final);return final

def validate_result(final,comparisons):
    require(final['status']=='IO_FILL_DIAGNOSTIC_MATCH_VERIFIED' and final['failures']=={} and
            set(final['targets'])==set(TARGETS),'Incomplete filler/corner result')
    expected={'boundary':True,**{f:False for f in faults()}}
    for key,t in final['targets'].items():
        require(t['status']=='IO_FILL_CELL_DIAGNOSTIC_MATCH_VERIFIED' and t['outcomes']==expected and
                t['negative_controls']==35 and t['source_graphs_unchanged'],'Filler/corner control coverage differs')
        for case,value in expected.items():
            r=json.loads((comparisons/key/(case+'.json')).read_text())
            require(r['raw_match']==r['strict_match']==value,'Individual filler/corner result differs')
            if value:require(r['circuits']==['Match'] and r['pair_counts']=={
                'devices':{'Match':4},'pins':{'Match':5},'nets':{'Match':5}},'Positive pair coverage differs')
    require(not final['full_chip_lvs'] and not final['candidate_promoted'] and final['cp0c']=='PENDING','Scope changed')
