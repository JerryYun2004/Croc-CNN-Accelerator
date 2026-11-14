"""Strict power-pad checks and independently mutated negative controls."""
from cp0c_io_power_geometry import *

def faults_for(key):
    f=['vss_tap_area','vss_tap_perimeter','iovss_tap_area','iovss_tap_perimeter',
       'tap_polarity','wrong_supply','port_swap','substrate_port_open']
    for port in TARGETS[key]['device_ports']:f.append('port_open_'+port)
    for pol in (('n',) if key.endswith('vdd') else ('n','p')):
        f += ['esd_'+pol+'_'+v for v in ('area','perimeter','m','missing','reverse')]
    if key.endswith('vdd'):
        for dev in ('clamp_n','inverter_n','inverter_p','capacitor_n'):
            f += [dev+'_'+v for v in ('width','length','bulk_open','gate_open','missing')]
        f+=['res_width','res_length','res_spacing','res_bends','res_multiplier','res_sub_open',
            'res_sub_swap','res_chain_open','res_segment_short']
        f+=['res_missing_'+str(i) for i in range(26)]
    return tuple(f)

def apply_fault(nl,key,fault):
    c=nl.top_circuit();pins=named_pins(c);ds=list(c.each_device())
    def one(model,select=lambda d:True):
        found=[d for d in ds if d.device_class().name.lower()==model and select(d)]
        require(len(found)==1,'Ambiguous fault target '+model);return found[0]
    def opened(d,t):d.connect_terminal(t,c.create_net('FAULT_OPEN'))
    if fault.startswith(('vss_tap_','iovss_tap_')) or fault in ('tap_polarity','wrong_supply'):
        port='iovss' if fault.startswith('iovss') else 'vss'
        d=one('ptap1',lambda d:d.net_for_terminal('TIE').expanded_name()==pins[port].expanded_name())
        if fault.endswith('area'):d.set_parameter('A',d.parameter('A')+.1)
        elif fault.endswith('perimeter'):d.set_parameter('P',d.parameter('P')+.1)
        elif fault=='wrong_supply':d.connect_terminal('TIE',pins['iovss'])
        else:
            a,b=d.net_for_terminal('TIE'),d.net_for_terminal('WELL');d.connect_terminal('TIE',b);d.connect_terminal('WELL',a)
    elif fault.startswith(('clamp_','inverter_','capacitor_')):
        family,pol,op=fault.split('_',2)
        length={'clamp':.6,'inverter':.5,'capacitor':9.5}[family]
        d=one('sg13_hv_'+pol+'mos',lambda d:abs(d.parameter('L')-length)<1e-8)
        if op in ('width','length'):
            p='W' if op=='width' else 'L';d.set_parameter(p,d.parameter(p)+.1)
        elif op.endswith('_open'):opened(d,'B' if op=='bulk_open' else 'G')
        elif op=='missing':c.remove_device(d)
        else:raise RuntimeError('Unknown MOS control')
    elif fault.startswith('esd_'):
        _,pol,op=fault.split('_');d=one('dantenna' if pol=='n' else 'dpantenna')
        if op in ('area','perimeter','m'):
            p={'area':'A','perimeter':'P','m':'m'}[op];d.set_parameter(p,d.parameter(p)*1.2+1)
        elif op=='missing':c.remove_device(d)
        elif op=='reverse':
            a,b=d.net_for_terminal('A'),d.net_for_terminal('C');d.connect_terminal('A',b);d.connect_terminal('C',a)
        else:raise RuntimeError('Unknown ESD control')
    elif fault.startswith('res_'):
        resistors=sorted([d for d in ds if d.device_class().name.lower()=='rppd'],key=lambda d:d.expanded_name())
        require(len(resistors)==26,'Resistor chain count differs')
        d=resistors[int(fault.removeprefix('res_missing_'))] if fault.startswith('res_missing_') else resistors[0]
        if fault.startswith('res_missing_'):c.remove_device(d)
        elif fault=='res_sub_open':opened(d,'rppd_sub')
        elif fault=='res_sub_swap':
            a,b=d.net_for_terminal('rppd_1'),d.net_for_terminal('rppd_sub')
            d.connect_terminal('rppd_1',b);d.connect_terminal('rppd_sub',a)
        elif fault=='res_chain_open':opened(d,'rppd_1')
        elif fault=='res_segment_short':d.connect_terminal('rppd_1',d.net_for_terminal('rppd_2'))
        else:
            p={'res_width':'w','res_length':'l','res_spacing':'ps','res_bends':'b','res_multiplier':'m'}[fault]
            d.set_parameter(p,d.parameter(p)+.1)
    elif fault=='port_swap':
        pids={p.name().lower():p.id() for p in c.each_pin()}
        a='vdd' if key=='vdd' else 'iovdd';b='iovss'
        c.connect_pin(pids[a],pins[b]);c.connect_pin(pids[b],pins[a])
    elif fault.startswith('port_open_') or fault=='substrate_port_open':
        name='sub!' if fault=='substrate_port_open' else fault.removeprefix('port_open_')
        p=next(p for p in c.each_pin() if p.name().lower()==name)
        c.connect_pin(p.id(),c.create_net('FAULT_PORT_OPEN'))
    else:raise RuntimeError('Unknown fault '+fault)

def audit_extraction(path,target,variant,out):
    x=json.loads(Path(path).read_text())
    require(x['milestone']=='complete' and x['net_joins']==0 and not x['extraction_errors']
            and x['implicit_connections']==[] and x['blackboxes']==[] and x['tap_extraction_enabled'],
            'Extraction not complete/strict')
    require(x['reference_pass_through_projection']=={'port':target['pass_through'],'devices_removed':0,
        'layout_nets_added':0,'verification':'all four rails independently checked in physical metal gate'},
        'Unexpected pass-through projection')
    raw=dict(target['raw_models'])
    if variant=='unmarked':raw.pop('rppd')
    actual=dict(Counter(d['model'].lower() for d in x['before_combine']['devices']))
    require(actual==raw,'Raw device inventory differs: '+str(actual))
    if target['pad'].endswith('Vdd'):
        for model,length,width,count in [('sg13_hv_nmos',.6,4.4,172),('sg13_hv_nmos',.5,9.,12),
                                        ('sg13_hv_nmos',9.5,9.,14),('sg13_hv_pmos',.5,7.,50)]:
            matches=[d for d in x['before_combine']['devices'] if d['model'].lower()==model
                     and abs(d['parameters']['L']-length)<1e-8]
            require(len(matches)==count and all(abs(d['parameters']['W']-width)<1e-8 for d in matches),
                    'Raw power MOS geometry differs')
        if variant!='unmarked':
            rs=[d for d in x['before_combine']['devices'] if d['model'].lower()=='rppd']
            require(len(rs)==26 and all(d['parameters']=={'w':1.,'l':20.,'ps':0.,'b':0.,'m':1.} for d in rs),
                    'Raw resistor dimensions differ')
    witnesses=x['substrate_witnesses']
    models=dict(target['models'])
    if variant=='unmarked':models.pop('rppd')
    expected=sum(v for k,v in models.items() if k in ('sg13_hv_nmos','ptap1','dantenna','rppd'))
    require(len(witnesses)==expected and {w['net'] for w in witnesses}=={x['substrate_net_key']},
            'Substrate witness coverage differs')
    result={'layout':reduction_audit(x['before_combine'],x['after_combine']),
            'references':{r['name']:reduction_audit(r['before_combine'],r['after_combine']) for r in x['references']}}
    write_json(out,result);return result

def read_db(path):
    state=db.LayoutVsSchematic();state.read(str(path))
    require(not list(state.each_error()),'Saved extraction has errors')
    a,b=state.netlist().dup(),state.reference.dup()
    require(len(a.top_circuits())==len(b.top_circuits())==1,'Saved top count differs')
    return a,b

def resistor_chain(nl,key):
    c=nl.top_circuit();pins=named_pins(c);ds=list(c.each_device())
    rs=[d for d in ds if d.device_class().name.lower()=='rppd']
    require(len(rs)==26,'Expected every resistor segment')
    caps=[d for d in ds if d.device_class().name.lower()=='sg13_hv_nmos' and abs(d.parameter('L')-9.5)<1e-8]
    require(len(caps)==1,'Expected one RC capacitor MOS')
    start=pins[key].expanded_name();end=caps[0].net_for_terminal('G').expanded_name()
    adjacent=defaultdict(list)
    for i,d in enumerate(rs):
        terms={t.name.lower():t.id() for t in d.device_class().terminal_definitions()}
        a,b=[d.net_for_terminal(terms[t]).expanded_name() for t in ('rppd_1','rppd_2')]
        require(a!=b,'Shorted resistor segment')
        require(d.net_for_terminal(terms['rppd_sub']).expanded_name()==pins['sub!'].expanded_name(),'Resistor bulk is open')
        adjacent[a].append((b,i));adjacent[b].append((a,i))
    require(len(adjacent)==27 and {n for n,edges in adjacent.items() if len(edges)==1}=={start,end}
            and all(len(edges) in (1,2) for edges in adjacent.values()),'Resistor chain endpoints/branching differ')
    seen={start};todo=[start]
    while todo:
        for n,_ in adjacent[todo.pop()]:
            if n not in seen:seen.add(n);todo.append(n)
    require(seen==set(adjacent),'Resistor chain disconnected')
    return {'status':'RESISTOR_CHAIN_VERIFIED','segments':26,'nodes':27,'start':start,'end':end,
            'all_substrates_connected':True,'series_reduction':False}

def compare_target(key,probes,out):
    t=TARGETS[key];out.mkdir(exist_ok=True)
    for v in t['variants']:audit_extraction(probes/v/'extraction.json',t,v,out/(v+'-reduction.json'))
    baseline_a,baseline_b=read_db(probes/'candidate/translated.lvsdb');original=[graph(baseline_a),graph(baseline_b)]
    for nl in (baseline_a,baseline_b):
        require(not list(nl.top_circuit().each_subcircuit()),'Netlist not flattened')
        require(dict(Counter(d.device_class().name.lower() for d in nl.top_circuit().each_device()))==t['models'],
                'Positive model inventory differs')
    if key.endswith('vdd'):
        write_json(out/'resistor-chain.json',{'layout':resistor_chain(baseline_a,key),
                                             'reference':resistor_chain(baseline_b,key)})
    faults=faults_for(key);cases=['translated',*t['contexts'],*faults];outcomes={};raw={}
    for v in cases:
        if v=='unmarked':a,b=read_db(probes/'unmarked/translated.lvsdb')
        elif v in t['contexts']:a,b=read_db(probes/'candidate'/(v+'.lvsdb'))
        else:a,b=baseline_a.dup(),baseline_b.dup()
        before=graph(a)
        if v in faults:apply_fault(a,key,v)
        after=graph(a);require((before!=after)==(v in faults),'Fault mutation invalid')
        r=compare(a,b,t['device_ports']+['sub!'],t['devices']);r.update(variant=v,layout=after,reference=graph(b))
        write_json(out/(v+'.json'),r);outcomes[v]=r['strict_match'];raw[v]=r['raw_match']
        print(f"  {key}/{v}: strict_match={r['strict_match']}",flush=True)
        if v=='translated' and not r['strict_match']:
            final={'status':'IO_POWER_PAD_REVIEW_REQUIRED','target':key,'outcomes':outcomes,
                   'negative_controls_meaningful':False,'cp0c':'PENDING'}
            write_json(out/'conclusion.json',final);return final
        if v!='translated':require(not r['raw_match'],'Comparer accepted negative control '+key+'/'+v)
    require(original==[graph(baseline_a),graph(baseline_b)],'Source graph mutation')
    if key.endswith('vdd'):
        swapped=baseline_a.dup()
        for d in swapped.top_circuit().each_device():
            if d.device_class().name.lower()=='rppd':
                a,b=d.net_for_terminal('rppd_1'),d.net_for_terminal('rppd_2')
                d.connect_terminal('rppd_1',b);d.connect_terminal('rppd_2',a)
        check=compare(swapped,baseline_b.dup(),t['device_ports']+['sub!'],t['devices'])
        require(check['strict_match'],'Symmetric resistor-end positive control failed')
        write_json(out/'resistor-end-symmetry.json',check)
    expected={v:v=='translated' for v in cases};require(raw==outcomes==expected,'Comparison gate failed')
    final={'status':'IO_POWER_PAD_DIAGNOSTIC_MATCH_VERIFIED','target':key,'outcomes':outcomes,
        'fault_controls':len(faults),'context_controls':len(t['contexts']),'negative_controls_pass':True,
        'source_graphs_unchanged':True,'full_chip_lvs':False,'cp0c':'PENDING'}
    write_json(out/'conclusion.json',final);return final

def validate_result(final,comparisons):
    require(final['status']=='IO_POWER_DEVICE_MATCH_VERIFIED' and not final['failures'] and
            set(final['targets'])==set(TARGETS),'Power device gate failed')
    for key,t in TARGETS.items():
        r=final['targets'][key];faults=faults_for(key)
        expected={'translated':True,**{v:False for v in [*t['contexts'],*faults]}}
        require(r['status']=='IO_POWER_PAD_DIAGNOSTIC_MATCH_VERIFIED' and r['outcomes']==expected and
                r['negative_controls_pass'] and r['source_graphs_unchanged'] and
                r['fault_controls']==len(faults) and r['context_controls']==len(t['contexts']),
                'Power control coverage differs: '+key)
        for case in expected:
            p=json.loads((comparisons/key/(case+'.json')).read_text())
            require(p['raw_match']==p['strict_match']==expected[case],'Individual power result differs')
            if case=='translated':
                require(p['circuits']==['Match'] and p['pair_counts']=={
                    'devices':{'Match':t['devices']},'pins':{'Match':4},
                    'nets':{'Match':31 if key.endswith('vdd') else 4}},'Positive match coverage differs')
    require(not final['full_chip_lvs'] and not final['candidate_promoted'] and final['cp0c']=='PENDING',
            'Scope changed')
