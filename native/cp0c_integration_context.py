"""Cell-scoped I/O compatibility diagnostic; never changes a chip candidate."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import klayout.db as db

CELL = 'sg13cmos5l_SecondaryProtection'
ALIASES = {'core':'CORE', 'pad':'PAD', 'iovss':'MINUS', 'iovdd':'PLUS'}
FAULTS = ('res_width','res_length','res_bends','res_spacing','tap_area','tap_perimeter',
          'missing_resistor','missing_tap','tap_polarity','substrate_open','port_swap','diode_area')

def require(ok, message):
    if not ok: raise RuntimeError(message)

def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')

def load(path):
    s=db.LayoutVsSchematic(); s.read(str(path))
    require(not list(s.each_error()), 'Source extraction reports errors')
    return s

def isolate(nl, name):
    n=nl.dup(); c=n.circuit_by_name(name)
    require(c is not None and not list(c.each_subcircuit()), 'Expected a flat protection cell')
    for other in list(n.each_circuit_top_down()):
        if other.name!=name: n.remove(other)
    require(len(n.top_circuits())==1, 'Isolation did not produce one top')
    return n

def net_name(net):
    return net.expanded_name() if net is not None else None

def pins(c):
    ps=list(c.each_pin()); out={p.name():c.net_for_pin(p.id()) for p in ps}
    require(len(ps)==len(out) and all(x is not None for x in out.values()), 'Ambiguous or disconnected cell ports')
    return out

def graph(nl):
    c=nl.top_circuit()
    return {'top':c.name, 'pins':[(p.name(),net_name(c.net_for_pin(p.id()))) for p in c.each_pin()],
            'nets':sorted(net_name(n) for n in c.each_net()),
            'devices':sorted([d.expanded_name(), d.device_class().name,
                {p.name:d.parameter(p.id()) for p in d.device_class().parameter_definitions()},
                {t.name:net_name(d.net_for_terminal(t.id())) for t in d.device_class().terminal_definitions()}]
                for d in c.each_device())}

def one(nl, model):
    devices=[d for d in nl.top_circuit().each_device() if d.device_class().name.lower()==model]
    require(len(devices)==1, 'Expected exactly one '+model)
    return devices[0]

def geometry(path):
    """Measure the two physical tap rings, independently of extracted parameters.

    Implements the pinned active/pSD/substrate intersection and tap exclusions
    for this exact cell. Restricted geometry checks prevent generalizing this
    annotation to arbitrary taps or to a changed library revision.
    """
    l=db.Layout(); l.read(str(path)); c=l.cell(CELL)
    require(c is not None and l.dbu==.001, 'Unexpected cell or database units')
    def r(layer, datatype=0): return db.Region(c.begin_shapes_rec(l.layer(layer,datatype))).merged()
    # These layers alter the pwell/tap-marker derivation; absent in this fixture.
    for pair in ((1,22),(5,22),(46,21),(60,0),(32,0)):
        require(r(*pair).is_empty(), 'Unexpected context layer '+str(pair))
    sub=r(40); well=r(31)
    labels=[s.text for s in c.shapes(l.layer(63,0)).each() if s.is_text() and s.text.string.lower()=='sub!']
    require(len(labels)==2 and sub.count()==2, 'Tap marker/label inventory changed')
    require(all(any(p.inside(t.trans.disp) for t in labels) for p in sub.each()), 'Unlabelled substrate region')
    exclude=db.Region()
    for pair in ((5,0),(7,0),(26,0),(33,0),(156,0),(28,0),(128,0),(111,0),
                 (24,0),(1,20),(99,31),(27,0),(27,2)):
        exclude += r(*pair)
    tap=(((r(1)&r(14))&sub)-well-exclude).merged()
    require((tap^sub).is_empty(), 'Tap ties no longer equal the two labelled substrate rings')
    area=round(tap.area()*l.dbu**2,8); perimeter=round(tap.perimeter()*l.dbu,8)
    require(area==9.0304 and perimeter==53.12, 'Physical tap geometry changed')
    body=(r(5)&r(14)&r(28)&r(111)).merged()
    require((body^db.Region(db.Box(940,1370,1940,3370))).is_empty(), 'Straight resistor body changed')
    require((r(128)^body).is_empty(), 'Diagnostic PolyRes marker differs from resistor body')
    return {'status':'IO_PHYSICAL_GEOMETRY_VERIFIED','dbu_um':l.dbu,
            'tap_area_um2':area,'tap_perimeter_um':perimeter,'tap_components':tap.count(),
            'tap_polygons_dbu':[p.to_s() for p in tap.each()],
            'resistor_w_um':1.0,'resistor_l_um':2.0,'resistor_bends':0,
            'reference_tap_A_um2':9.03,'reference_tap_P_um':12.02,
            'reference_tap_w_l_um':[3.005,3.005],
            'reference_square_area_um2':3.005**2,
            'interpretation':'Cell-specific physical annotation; not a universal tap conversion or vendor signoff.'}

def translate(nl, measured, mode):
    r=one(nl,'rppd'); t=one(nl,'ptap1')
    require({p.name:r.parameter(p.id()) for p in r.device_class().parameter_definitions()}
            =={'w':1.0,'l':2.0,'ps':.18,'b':0.0,'m':1.0}, 'Legacy resistor parameters changed')
    require(abs(t.parameter('A')-9.03)<1e-9 and abs(t.parameter('P')-12.02)<1e-9,
            'Legacy tap parameters changed')
    edits=[]
    if mode in ('spacing_only','translated'):
        edits.append({'device':r.expanded_name(),'parameter':'ps','old':.18,'new':0.0,
                      'basis':'Pinned extractor calc_res_params returns zero spacing for zero bends.'})
        r.set_parameter('ps',0.0)
    if mode in ('tap_only','translated'):
        for p,key in [('A','tap_area_um2'),('P','tap_perimeter_um')]:
            edits.append({'device':t.expanded_name(),'parameter':p,'old':t.parameter(p),
                          'new':measured[key], 'basis':'Independent measurement of pinned cell GDS tap rings.'})
            t.set_parameter(p,measured[key])
    return edits

def apply_fault(nl, fault):
    c=nl.top_circuit(); r=one(nl,'rppd'); t=one(nl,'ptap1')
    parameters={'res_width':(r,'w',1.1),'res_length':(r,'l',2.1),
        'res_bends':(r,'b',1.0),'res_spacing':(r,'ps',.1),
        'tap_area':(t,'A',9.13),'tap_perimeter':(t,'P',53.22),
        'diode_area':(one(nl,'dantenna'),'A',2.2)}
    if fault in parameters:
        d,p,v=parameters[fault]; d.set_parameter(p,v)
    elif fault=='missing_resistor': c.remove_device(r)
    elif fault=='missing_tap': c.remove_device(t)
    elif fault=='tap_polarity':
        a,b=[t.net_for_terminal(p) for p in ('TIE','WELL')]
        t.connect_terminal('TIE',b); t.connect_terminal('WELL',a)
    elif fault=='substrate_open':
        r.connect_terminal('rppd_sub',c.create_net('CP0C_IO_OPEN_CONTROL'))
    elif fault=='port_swap':
        ps={p.name():p.id() for p in c.each_pin()}; ns=pins(c)
        c.connect_pin(ps['core'],ns['pad']); c.connect_pin(ps['pad'],ns['core'])
    else: raise RuntimeError('Unknown fault '+fault)

def compare(a,b):
    ca,cb=a.top_circuit(),b.top_circuit(); pa,pb=pins(ca),pins(cb)
    require(set(pa)==set(ALIASES) and set(pb)==set(ALIASES.values()), 'Cell interface changed')
    cmp=db.NetlistComparer(); cmp.dont_consider_net_names=True; cmp.same_circuits(ca,cb)
    models=('rppd','ptap1','dantenna','dpantenna')
    for model in models:
        aa=a.device_class_by_name(model); bb=b.device_class_by_name(model.upper())
        require(aa is not None and bb is not None, 'Missing class counterpart; refusing nil mapping')
        cmp.same_device_classes(aa,bb)
        for cl in (aa,bb):
            for p in cl.parameter_definitions(): cl.enable_parameter(p.name,True)
            # LVS database serialization does not retain Ruby comparator callbacks.
            # Reapply the pinned deck's 1% diode A/P convention; m stays exact.
            if model in ('dantenna','dpantenna'):
                pp={p.name:p.id() for p in cl.parameter_definitions()}
                cl.equal_parameters=(db.EqualDeviceParameters(pp['A'],0,.01)
                    + db.EqualDeviceParameters(pp['P'],0,.01) + db.EqualDeviceParameters(pp['m'],0,0))
            else:
                eq=None
                for p in cl.parameter_definitions():
                    term=db.EqualDeviceParameters(p.id(),0,0); eq=term if eq is None else eq+term
                cl.equal_parameters=eq
            if model=='ptap1': cl.clear_equivalent_terminal_ids()
    for an,bn in ALIASES.items(): cmp.same_nets(pa[an],pb[bn],True)
    x=db.NetlistCrossReference(); raw=bool(cmp.compare(a,b,x))
    counts={label:dict(Counter(str(p.status()) for p in fn(ca))) for label,fn in
            [('devices',x.each_device_pair),('pins',x.each_pin_pair),('nets',x.each_net_pair)]}
    circuits=[str(p.status()) for p in x.each_circuit_pair()]
    strict=(raw and circuits==['Match'] and counts['devices']=={'Match':4}
            and counts['pins']=={'Match':4} and counts['nets']=={'Match':5})
    return {'raw_match':raw,'strict_match':bool(strict),'pairs':counts,'circuits':circuits}

def aliases_from_cdl(path):
    """Audit every legacy parent call, rather than guessing from pin order."""
    rows=[]; parent=None; header=None
    for line in Path(path).read_text().splitlines():
        words=line.split()
        if not words: continue
        if words[0].upper()=='.SUBCKT':
            parent=words[1]
            if parent.lower()==CELL.lower(): header=words[2:]
        if words[0].upper().startswith('X') and CELL.lower() in [w.lower() for w in words]:
            i=next(i for i,w in enumerate(words) if w.lower()==CELL.lower())
            nets=words[1:i]
            if nets and nets[-1]=='/': nets=nets[:-1]
            require(len(nets)==4, 'Unexpected protection-cell parent interface')
            require(nets[1].lower()=='iovss' and nets[3].lower()=='iovdd', 'Supply alias contradicts parent CDL')
            rows.append({'parent':parent,'instance':words[0],'connections':nets})
    require(header==['core','minus','pad','plus'] and len(rows)==2, 'Reference header or parent call coverage changed')
    return {'aliases':ALIASES,'reference_header':header,'all_parent_calls':rows}

def substrate_paths(rows):
    """Resolve recorded unnamed interfaces to top ports, using circuit-local IDs."""
    index={c['cell']:c for c in rows}; paths=[]
    def walk(cell,cluster,steps,seen):
        require(cell not in seen,'Cycle in substrate hierarchy')
        groups=[g for g in index[cell]['suspect_groups'] if cluster in g['local_clusters']]
        require(len(groups)==1,'Ambiguous substrate interface continuation')
        g=groups[0]
        require(g['pin_name']=='' and len(g['pin_ids'])==1 and g['parent_connections'],
                'Substrate path no longer uses one connected unnamed interface')
        for call in g['parent_connections']:
            require(call['all_connected'] and len(call['clusters'])==1,'Open substrate interface')
            step={'cell':cell,'local_cluster':cluster,'parent':call['parent'],
                  'instance':call['instance'],'parent_cluster':call['clusters'][0],
                  'parent_net':call['nets'][0],'parent_ports':call['parent_port_names'][0]}
            chain=steps+[step]
            if call['parent'] in index:
                walk(call['parent'],call['clusters'][0],chain,seen+(cell,))
            else:
                require(call['parent']=='croc_chip' and call['parent_port_names']==[['VSS']],
                        'Substrate endpoint is not the chip VSS port')
                paths.append(chain)
    for row in rows:
        for g in row['suspect_groups']:
            if g['pin_name']=='':
                require(len(g['local_clusters'])==1,'Unexpected unnamed interface multiplicity')
                walk(row['cell'],g['local_clusters'][0],[],())
    return {'status':'IO_SUBSTRATE_CONTEXT_TRACED','path_count':len(paths),'paths':paths,
            'endpoint':'croc_chip.VSS','net_joins':0,
            'scope':'Physical extracted hierarchy only; no reference connectivity is inferred or changed.'}

def hierarchy_audit(path):
    """Report extra/split physical interfaces without joining or deleting pins.

    For each suspect group, trace its pin connections into every parent instance.
    Cluster IDs are compared only within the same owning circuit.
    """
    s=load(path); nl=s.netlist(); ref=s.reference
    parents=defaultdict(list)
    for c in nl.each_circuit():
        for sc in c.each_subcircuit(): parents[sc.circuit_ref().name].append((c,sc))
    rows=[]; totals=Counter(); calls=[]
    for c in nl.each_circuit():
        if not c.name.lower().startswith('sg13cmos5l_'): continue
        rc=ref.circuit_by_name(c.name.upper()); require(rc is not None,'Missing reference I/O master '+c.name)
        groups=defaultdict(list)
        for p in c.each_pin(): groups[p.name()].append(p)
        suspects=[]
        for name,ps in groups.items():
            if name and len(ps)==1: continue
            ids=[p.id() for p in ps]; nets=[c.net_for_pin(i) for i in ids]
            traces=[]
            for pc,sc in parents[c.name]:
                ns=[sc.net_for_pin(i) for i in ids]
                clusters=[n.cluster_id if n is not None else None for n in ns]
                refs=[]
                for n in ns:
                    refs.append([p.name() for p in pc.each_pin() if n is not None and
                                 pc.net_for_pin(p.id()) is not None and pc.net_for_pin(p.id()).cluster_id==n.cluster_id])
                traces.append({'parent':pc.name,'instance':sc.expanded_name(),
                    'nets':[net_name(n) for n in ns],'clusters':clusters,'parent_port_names':refs,
                    'all_connected':all(n is not None for n in ns),
                    'one_parent_net':None not in clusters and len(set(clusters))==1})
            row={'pin_name':name,'pin_ids':ids,'local_nets':[net_name(n) for n in nets],
                 'local_clusters':[n.cluster_id if n is not None else None for n in nets], 'parent_connections':traces}
            suspects.append(row); totals['unnamed_groups' if not name else 'duplicate_named_groups']+=1
            if name:
                totals['split_groups_with_parents']+=bool(traces)
                totals['split_groups_all_parent_instances_joined']+=bool(traces) and all(t['one_parent_net'] for t in traces)
        rows.append({'cell':c.name,'layout_pin_count':sum(1 for _ in c.each_pin()),
            'reference_pin_count':sum(1 for _ in rc.each_pin()),
            'layout_devices':dict(Counter(d.device_class().name for d in c.each_device())),
            'reference_devices':dict(Counter(d.device_class().name for d in rc.each_device())),
            'suspect_groups':suspects})
        if c.name==CELL:
            for pc,sc in parents[c.name]:
                calls.append({'side':'layout','parent':pc.name,'instance':sc.expanded_name(),
                    'connections':[(p.name(),net_name(sc.net_for_pin(p.id()))) for p in c.each_pin()]})
    for c in ref.each_circuit():
        for sc in c.each_subcircuit():
            if sc.circuit_ref().name==CELL.upper():
                calls.append({'side':'reference','parent':c.name,'instance':sc.expanded_name(),
                    'connections':[(p.name(),net_name(sc.net_for_pin(p.id()))) for p in sc.circuit_ref().each_pin()]})
    require(rows and calls,'I/O hierarchy inventory is empty')
    return {'status':'IO_HIERARCHY_REVIEW_REQUIRED','io_masters':len(rows),'counts':dict(totals),
            'substrate_context':substrate_paths(rows),
            'secondary_parent_connections':calls,'cells':rows,'net_joins':0,'pin_deletions':0,
            'new_extraction':False,'full_chip_comparison':False,'cp0c':'PENDING'}
