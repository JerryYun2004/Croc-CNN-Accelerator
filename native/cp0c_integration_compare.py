"""Strict hierarchical comparison of the freshly extracted, annotated chip."""
from collections import Counter,defaultdict
from pathlib import Path
from types import SimpleNamespace
import json,time
from cp0c_integration_common import db,require,write_json,reduction_audit
from cp0c_integration_context import substrate_paths
from cp0c_integration_sram import SRAM,annotate_reference,structural_hints,dangling_gate_hints

def nn(n):return n.expanded_name() if n is not None else None
def ports(c):
    ps=list(c.each_pin());out={p.name().lower():c.net_for_pin(p.id()) for p in ps}
    require(len(ps)==len(out) and all(n is not None for n in out.values()),'Ambiguous/disconnected ports: '+c.name)
    return out

def flatten_sram(nl):
    c=next((c for c in nl.each_circuit() if c.name.lower()==SRAM.lower()),None)
    require(c is not None,'SRAM master is absent');todo=[s.circuit_ref() for s in c.each_subcircuit()];children={}
    while todo:
        q=todo.pop()
        if q.name in children:continue
        require(q.name.upper().startswith(('RM_IHPSG13','RSC_IHPSG13')),'Unexpected non-SRAM child '+q.name)
        children[q.name]=q;todo.extend(s.circuit_ref() for s in q.each_subcircuit())
    nl.flatten_circuits(list(children.values()))
    require(not list(c.each_subcircuit()) and len(list(c.each_device()))==143422 and
        len(list(c.each_pin()))==191 and len(list(c.each_net()))==72373,'Fresh SRAM flattened inventory differs')
    return c

def configure(a,b):
    cmp=db.NetlistComparer();cmp.dont_consider_net_names=True;cmp.max_depth=20;cmp.max_branch_complexity=256
    circuits=[{c.name.lower():c for c in n.each_circuit()} for n in (a,b)]
    require(set(circuits[0])==set(circuits[1]),'Prepared circuit sets differ; inspect inventories')
    for name in sorted(circuits[0]):
        ca,cb=[x[name] for x in circuits];cmp.same_circuits(ca,cb)
        pa,pb=ports(ca),ports(cb);require(set(pa)==set(pb),'Circuit interface differs: '+name)
        for key in pa:cmp.same_nets(pa[key],pb[key],True)
    classes=[]
    for nl in (a,b):
        mutable={cl.name:cl for cl in nl.each_device_class()};active=defaultdict(dict)
        for c in nl.each_circuit():
            for d in c.each_device():
                name=d.device_class().name;active[name.lower()][name]=mutable[name]
        for model,group in active.items():
            require(model in ('sg13_lv_nmos','sg13_lv_pmos','sg13_hv_nmos','sg13_hv_pmos',
                'dantenna','dpantenna','ptap1','rppd','res_metal1','res_metal2','res_metal3'),
                    'Unreviewed active device class '+model)
            for cl in group.values():
                params={p.name:p.id() for p in cl.parameter_definitions()}
                enabled=('L','W','rfmode') if model.startswith('sg13_') else (
                    ('L','W') if model.startswith('res_metal') else tuple(params))
                for p in params:cl.enable_parameter(p,p in enabled)
                eq=None
                for p in enabled:
                    require(p in params,'Missing primary parameter '+model+'/'+p)
                    absolute=1e-8 if (model.startswith('sg13_') and p in ('L','W')) or model=='ptap1' else 0
                    relative=.01 if model in ('dantenna','dpantenna') and p in ('A','P') else 0
                    part=db.EqualDeviceParameters(params[p],absolute,relative);eq=part if eq is None else eq+part
                cl.equal_parameters=eq
                if model=='ptap1':cl.clear_equivalent_terminal_ids()
                if model=='rppd':
                    t={t.name.lower():t.id() for t in cl.terminal_definitions()}
                    require(set(t)=={'rppd_1','rppd_2','rppd_sub'},'RPPD interface differs')
                    cl.clear_equivalent_terminal_ids();cl.equivalent_terminal_id(t['rppd_1'],t['rppd_2']);cl.strict=False
        classes.append(active)
    for model in set(classes[0])&set(classes[1]):
        for aa in classes[0][model].values():
            for bb in classes[1][model].values():cmp.same_device_classes(aa,bb)
    return cmp

def top_hints(ca,cb,max_rounds=24):
    """Match unique neighborhoods using ports, device geometry and child pin roles.

    Names of internal nets/instances do not participate. Every hint is strict;
    the KLayout comparator must still verify every circuit/device/connection.
    """
    intern={};graphs=[]
    def color(key):
        if key not in intern:intern[key]=len(intern)
        return intern[key]
    for c in (ca,cb):
        nets=list(c.each_net());idx={nn(n):i for i,n in enumerate(nets)}
        require(len(idx)==len(nets),'Ambiguous top net names')
        pin_names=defaultdict(list)
        for p in c.each_pin():pin_names[nn(c.net_for_pin(p.id()))].append(p.name().lower())
        colors=[color(('net',tuple(sorted(pin_names[nn(n)])))) for n in nets];adj=[[] for _ in nets]
        def node(key,edges):
            i=len(colors);colors.append(color(key));adj.append([])
            for label,n in edges:
                require(n is not None,'Disconnected terminal in top refinement')
                j=idx[nn(n)];adj[i].append((label,j));adj[j].append((label,i))
        for d in c.each_device():
            cl=d.device_class();model=cl.name.lower();mos=model.startswith('sg13_')
            pars=('L','W','rfmode') if mos else tuple(p.name for p in cl.parameter_definitions())
            params=tuple((p,round(d.parameter(p),7)) for p in pars)
            edges=[]
            for t in cl.terminal_definitions():
                label=t.name.lower()
                if mos and label in ('s','d'):label='sd'
                if model=='rppd' and label in ('rppd_1','rppd_2'):label='ends'
                edges.append((label,d.net_for_terminal(t.id())))
            node(('device',model,params),edges)
        for sc in c.each_subcircuit():
            child=sc.circuit_ref();ps=list(child.each_pin());names=[p.name().lower() for p in ps]
            require(len(names)==len(set(names)),'Ambiguous child interface in top refinement')
            node(('subcircuit',child.name.lower()),[(p.name().lower(),sc.net_for_pin(p.id())) for p in ps])
        graphs.append({'nets':nets,'colors':colors,'adj':adj})
    rounds=[]
    for iteration in range(max_rounds):
        old_count=len(set(graphs[0]['colors'])|set(graphs[1]['colors']));intern={};new=[]
        for g in graphs:
            cs=g['colors'];new.append([color((cs[i],tuple(sorted((e,cs[j]) for e,j in edges)))) for i,edges in enumerate(g['adj'])])
        equal=Counter(new[0])==Counter(new[1]);rounds.append({'round':iteration+1,'colors':len(intern),'histograms_equal':equal})
        for g,cs in zip(graphs,new):g['colors']=cs
        if len(intern)==old_count:break
    groups=[]
    for g in graphs:
        group=defaultdict(list)
        for n,k in zip(g['nets'],g['colors']):group[k].append(n)
        groups.append(group)
    hints=[(v[0],groups[1][k][0]) for k,v in groups[0].items() if len(v)==1 and len(groups[1].get(k,[]))==1]
    return hints,{'rounds':rounds,'strict_unique_hints':len(hints),'net_joins':0,'internal_names_used':False}

def audit_ambiguous_nets(x,c,r):
    """Verify the chosen mapping of structurally ambiguous internal nets.

    KLayout's net MatchWithWarning means an ambiguous choice, unlike its
    device warning (which may mean parameter mismatch). Never accept device
    warnings. Require a complete bijection and verify every incident device
    under the chosen net mapping; ambiguous nets cannot touch interface pins.
    """
    pairs=list(x.each_net_pair(c));mapping={};ambiguous=set();seen={};repeats=0
    for p in pairs:
        require(p.first() is not None and p.second() is not None,'One-sided net in ambiguity audit')
        aa,bb=nn(p.first()),nn(p.second())
        require(aa not in mapping or mapping[aa]==bb,'Conflicting net cross reference: '+aa)
        require(bb not in seen or seen[bb]==aa,'Conflicting reverse net cross reference: '+bb)
        repeats+=aa in mapping;mapping[aa]=bb;seen[bb]=aa
        require(str(p.status()) in ('Match','MatchWithWarning'),'Unmatched net in ambiguity audit')
        if str(p.status())=='MatchWithWarning':ambiguous.add(aa)
    require(set(mapping)=={nn(n) for n in c.each_net()} and set(seen)=={nn(n) for n in r.each_net()},
            'Incomplete net cross reference')
    counterpart={mapping[n] for n in ambiguous}
    for circuit,names in ((c,ambiguous),(r,counterpart)):
        for net in circuit.each_net():
            if nn(net) not in names:continue
            require(net.pin_count()==0,'Ambiguous net reaches a circuit port')
            require(net.subcircuit_pin_count()==0,'Ambiguous net reaches a child interface')
    checked=0;covered=set()
    for p in x.each_device_pair(c):
        aa,bb=p.first(),p.second()
        require(aa is not None and bb is not None and str(p.status())=='Match',
                'Non-exact device match in ambiguity audit')
        connections=[nn(aa.net_for_terminal(t.id())) for t in aa.device_class().terminal_definitions()]
        touched=ambiguous.intersection(connections)
        if not touched:continue
        model=aa.device_class().name.lower()
        require(model==bb.device_class().name.lower(),'Ambiguous-net device models differ')
        def ends(d,translate):
            out=defaultdict(list)
            for t in d.device_class().terminal_definitions():
                label=t.name.lower();net=nn(d.net_for_terminal(t.id()))
                require(net is not None,'Open device terminal in ambiguity audit')
                if model.startswith('sg13_') and label in ('s','d'):label='sd'
                if model=='rppd' and label in ('rppd_1','rppd_2'):label='ends'
                if model.startswith('res_metal') and label in ('a','b'):label='ends'
                out[label].append(mapping[net] if translate else net)
            return {k:sorted(v) for k,v in out.items()}
        require(ends(aa,True)==ends(bb,False),'Ambiguous-net device connections differ')
        checked+=1;covered.update(touched)
    require(covered==ambiguous,'Unconnected ambiguous net')
    return {'status':'AMBIGUOUS_INTERNAL_NET_MAPPING_VERIFIED','ambiguous_nets':len(ambiguous),
            'incident_devices_checked':checked,'net_pairs_bijective':True,'repeated_identical_pair_records':repeats,
            'port_or_child_interfaces_touched':False,'device_warnings_accepted':0,'net_joins':0}

def comparison_result(cmp,a,b,state=None):
    start=time.monotonic()
    if state is None:
        x=db.NetlistCrossReference();raw=bool(cmp.compare(a,b,x))
    else:
        raw=bool(state.compare(cmp));x=state.xref()
    rows=[]
    def label(v):
        if v is None:return None
        if hasattr(v,'expanded_name'):return v.expanded_name()
        return v.name() if callable(v.name) else v.name
    for pair in x.each_circuit_pair():
        c=pair.first();r=pair.second();row={'layout':label(c),'reference':label(r),'status':str(pair.status()),'pairs':{},'samples':{}}
        if c is not None:
            for kind,fn in [('pins',x.each_pin_pair),('nets',x.each_net_pair),('devices',x.each_device_pair),('subcircuits',x.each_subcircuit_pair)]:
                counts=Counter();bad=[]
                for p in fn(c):
                    status=str(p.status());counts[status]+=1
                    if status!='Match' and len(bad)<30:bad.append({'layout':label(p.first()),'reference':label(p.second()),'status':status})
                row['pairs'][kind]=dict(counts);row['samples'][kind]=bad
        rows.append(row)
    audits={};strict=raw and bool(rows)
    if raw:
        for pair in x.each_circuit_pair():
            c,r=pair.first(),pair.second()
            if c is not None and r is not None and any(str(p.status())=='MatchWithWarning' for p in x.each_net_pair(c)):
                try:audits[c.name]=audit_ambiguous_nets(x,c,r)
                except RuntimeError as exc:audits[c.name]={'status':'AMBIGUOUS_NET_REVIEW_REQUIRED','reason':str(exc)}
    for row in rows:
        strict &= row['status']=='Match'
        for kind,counts in row['pairs'].items():
            allowed={'Match'}
            expected={SRAM.lower():352,'croc_chip':8}.get(row['layout'].lower())
            if kind=='nets' and expected is not None and audits.get(row['layout'],{}).get('status')=='AMBIGUOUS_INTERNAL_NET_MAPPING_VERIFIED':
                allowed.add('MatchWithWarning')
            strict &= set(counts)<=allowed
            if 'MatchWithWarning' in counts:strict &= counts['MatchWithWarning']==expected
    return {'raw_match':raw,'strict_match':bool(strict),'circuits':rows,'ambiguous_net_audits':audits,
            'compare_seconds':round(time.monotonic()-start,3)},x

def apply_top_fault(nl,fault):
    c=nl.top_circuit();ps=ports(c)
    if fault=='io_wrong_supply':
        choices=[(d,t) for d in c.each_device() if d.device_class().name.lower() in ('dantenna','dpantenna')
                 for t in d.device_class().terminal_definitions()
                 if nn(d.net_for_terminal(t.id()))==nn(ps['vddio'])]
        require(choices,'No I/O supply fault target');d,t=choices[0];d.connect_terminal(t.id(),ps['vssio'])
    elif fault=='gpio_swap':
        pins={p.name().lower():p for p in c.each_pin()}
        c.connect_pin(pins['gpio0_io'].id(),ps['gpio1_io']);c.connect_pin(pins['gpio1_io'].id(),ps['gpio0_io'])
    elif fault=='sram_supply_open':
        sc=next(s for s in c.each_subcircuit() if s.circuit_ref().name.lower()==SRAM.lower())
        p=next(p for p in sc.circuit_ref().each_pin() if p.name().lower()=='vss!')
        sc.connect_pin(p.id(),c.create_net('CP0C_SRAM_SUPPLY_OPEN_CONTROL'))
    elif fault=='missing_io_resistor':
        d=next(d for d in c.each_device() if d.device_class().name.lower()=='rppd');c.remove_device(d)
    else:raise RuntimeError('Unknown integration fault '+fault)

def compare(cfg,out,variant='baseline'):
    out=Path(out);full=Path(cfg['full']);pin=json.loads(Path(cfg['pins']).read_text())
    inventories=json.loads((full/'aligned-inventory.json').read_text())
    expected={k.lower():v for k,v in pin['top_io'].items()};expected.update(pin['core_top_instances'])
    for side,rows in inventories.items():
        rows={name.lower():v for name,v in rows.items()}
        actual={name.lower():v for name,v in rows['croc_chip']['children'].items()}
        require(actual==expected,'Fresh chip instance coverage differs on '+side)
        require({n for n in rows if n.startswith('sg13g2_')}==set(pin['standard_cell_devices']),
                'Fresh standard-cell master coverage differs on '+side)
        for name,counts in pin['standard_cell_devices'].items():
            require({m.lower():v for m,v in rows[name]['devices'].items()}==counts,
                    'Fresh standard-cell transistor coverage differs: '+side+'/'+name)
    fresh=json.loads((full/'fresh-boundaries.json').read_text());traces=substrate_paths(fresh['cells'])
    require(len(fresh['cells'])==30,'Fresh I/O master coverage differs')
    splits=[g for c in fresh['cells'] for g in c['suspect_groups'] if g['pin_name']]
    require(all(g['parent_connections'] and all(t['one_parent_net'] for t in g['parent_connections']) for g in splits),
            'Fresh chip extraction has an unconnected split I/O interface')
    write_json(out/'fresh-substrate-paths.json',traces)
    reductions=json.loads((full/'io-reduction.json').read_text())
    audit={k:reduction_audit(v['before_combine'],v['after_combine']) for k,v in reductions.items()}
    write_json(out/'io-reduction-audit.json',audit)
    state=db.LayoutVsSchematic();state.read(str(full/'prepared.lvsdb'))
    require(not list(state.each_error()),'Fresh database contains extraction errors')
    a,b=state.netlist(),state.reference
    require(a.top_circuit().name=='croc_chip' and b.top_circuit().name=='CROC_CHIP','Wrong chip comparison top')
    require(len(ports(a.top_circuit()))==len(ports(b.top_circuit()))==52,'Chip port coverage differs')
    ca,cb=flatten_sram(a),flatten_sram(b)
    require(sum(s.circuit_ref().name.lower()==SRAM.lower() for s in a.top_circuit().each_subcircuit())==2 and
        sum(s.circuit_ref().name.lower()==SRAM.lower() for s in b.top_circuit().each_subcircuit())==2,'SRAM bank coverage differs')
    write_json(out/'sram-translation.json',annotate_reference(b,True,out/'sram-translation.tsv',circuit=cb))
    if variant!='baseline':apply_top_fault(b,variant)
    cmp=configure(a,b)
    adapters=[SimpleNamespace(top_circuit=lambda c=c:c) for c in (ca,cb)]
    hints,report=structural_hints(*adapters);write_json(out/'sram-structural-hints.json',report)
    for x,y in hints:cmp.same_nets(x,y,True)
    dummy,report=dangling_gate_hints(*adapters);write_json(out/'sram-dummy-hints.json',report)
    require(report['strict_gate_net_hints']==512,'SRAM dummy-gate context coverage differs')
    for x,y in dummy:cmp.same_nets(x,y,True)
    hints,report=top_hints(a.top_circuit(),b.top_circuit());write_json(out/'chip-structural-hints.json',report)
    for x,y in hints:cmp.same_nets(x,y,True)
    print('COMPARE: '+variant+'; SRAM and chip strict hints applied',flush=True)
    result,xref=comparison_result(cmp,a,b,state=state)
    result.update(variant=variant,new_extraction=True,full_chip_comparison=True,
        reference_scope='Annotated diagnostic reference',blackboxes=[],net_joins=0,candidate_promoted=False,cp0c='PENDING')
    write_json(out/'result.json',result)
    if variant=='baseline':
        state.write(str(full/'compared.lvsdb'))
        write_json(out/'database-note.json',{'database':str(full/'compared.lvsdb'),
            'scope':'Fresh extraction, integrated IO, flattened SRAM and annotated reference',
            'cross_reference_saved':True,'comparison_repeated':False})
    print('CP0C_INTEGRATION_COMPARISON_COMPLETE '+variant+' match='+str(result['strict_match']),flush=True)
