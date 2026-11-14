"""Power-pad diagnostic helpers. Only disposable fixtures are modified."""
from collections import Counter, defaultdict
import hashlib, json, math, re
from pathlib import Path
import klayout.db as db

def require(ok,msg):
    if not ok: raise RuntimeError(msg)

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def write_json(path,data):Path(path).write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')

def fingerprint(layout,cell,exclude_marker=False):
    result={}
    for i in layout.layer_indexes():
        info=layout.get_info(i)
        if exclude_marker and (info.layer,info.datatype)==(128,0):continue
        shapes=cell.shapes(i)
        polygons=sorted(p.to_s() for p in db.Region(shapes).merged().each())
        texts=sorted(s.text.to_s() for s in shapes.each() if s.is_text())
        if polygons or texts:result[info.to_s()]={'polygons':polygons,'texts':texts}
    return {'layers':result,'instances':sorted((i.cell.name,sorted(str(t) for t in i.cell_inst.each_cplx_trans()))
                                             for i in cell.each_inst())}

def cdl_cells(path):
    text=re.sub(r'\n[ \t]*\+',' ',Path(path).read_text());out={};current=None
    for line in text.splitlines():
        words=line.split()
        if not words or words[0].startswith('*'):continue
        if words[0].upper()=='.SUBCKT':
            require(current is None,'Nested CDL circuit');current=words[1]
            require(current not in out,'Duplicate CDL circuit')
            out[current]={'pins':words[2:],'body':[]}
        elif words[0].upper()=='.ENDS':current=None
        elif current:out[current]['body'].append(line.strip())
    require(current is None,'Unclosed CDL circuit');return out

def graph(nl):
    c=nl.top_circuit()
    def nn(n):return n.expanded_name() if n is not None else None
    return {'top':c.name,'pins':[(p.name(),nn(c.net_for_pin(p.id()))) for p in c.each_pin()],
            'nets':sorted(nn(n) for n in c.each_net()),'devices':sorted([
            d.expanded_name(),d.device_class().name,
            {p.name:d.parameter(p.id()) for p in d.device_class().parameter_definitions()},
            {t.name:nn(d.net_for_terminal(t.id())) for t in d.device_class().terminal_definitions()}]
            for d in c.each_device())}

def active(nl):
    # In the observed 0.30.9 runtime, classes in Netlist.dup() copies have
    # colliding IDs. Do not use those IDs as dictionary keys. Resolve each
    # device's exact class name against the mutable class enumeration instead.
    # This also avoids case-normalizing imported MOS class names. Device class
    # IDs are documented as unique; the collision is an observed copy behavior,
    # not the documented meaning of the API.
    mutable={}
    for cl in nl.each_device_class():
        require(cl.name not in mutable,'Duplicate device class name '+cl.name)
        mutable[cl.name]=cl
    result={}
    for d in nl.top_circuit().each_device():
        name=d.device_class().name
        require(name in mutable,'Missing mutable device class '+name)
        cl=mutable[name];result.setdefault(cl.name.lower(),{})[name]=cl
    return {k:list(v.values()) for k,v in result.items()}

def named_pins(c):
    ps=list(c.each_pin()); names=[p.name().lower() for p in ps]
    require(len(names)==len(set(names)),'Duplicate parent pin names')
    result={p.name().lower():c.net_for_pin(p.id()) for p in ps}
    require(all(n is not None for n in result.values()),'Disconnected parent port')
    return result

def compare(a,b,ports,expected_devices):
    ca,cb=a.top_circuit(),b.top_circuit();pa,pb=named_pins(ca),named_pins(cb)
    require(set(pa)==set(pb)==set(ports),'Unexpected parent interface')
    cmp=db.NetlistComparer();cmp.dont_consider_net_names=True;cmp.same_circuits(ca,cb)
    ac,bc=active(a),active(b)
    for model in set(ac)&set(bc):
        for aa in ac[model]:
            for bb in bc[model]:cmp.same_device_classes(aa,bb)
    # Never map a missing model to nil: missing devices/models must fail.
    for model,classes in list(ac.items())+list(bc.items()):
        for cl in classes:
            params={p.name:p.id() for p in cl.parameter_definitions()}
            enabled=('L','W','rfmode') if model.startswith('sg13_') else tuple(params)
            for name in params:cl.enable_parameter(name,name in enabled)
            eq=None
            for name in enabled:
                require(name in params,'Missing primary parameter '+model+'/'+name)
                tol=.01 if model in ('dantenna','dpantenna') and name in ('A','P') else 0
                term=db.EqualDeviceParameters(params[name],1e-8 if model.startswith('sg13_') and name in ('L','W') else 0,tol);eq=term if eq is None else eq+term
            cl.equal_parameters=eq
            if model=='ptap1':cl.clear_equivalent_terminal_ids()
            if model=='rppd':
                # The straight resistor's two conducting ends are symmetric;
                # the substrate is a distinct third terminal. Serialization
                # of the custom class does not retain the desired end symmetry.
                terms={t.name.lower():t.id() for t in cl.terminal_definitions()}
                require(set(terms)=={'rppd_1','rppd_2','rppd_sub'},'RPPD interface changed')
                cl.clear_equivalent_terminal_ids()
                cl.equivalent_terminal_id(terms['rppd_1'],terms['rppd_2'])
                cl.strict=False
    for name in ports:cmp.same_nets(pa[name],pb[name],True)
    x=db.NetlistCrossReference();raw=bool(cmp.compare(a,b,x))
    counts={name:dict(Counter(str(p.status()) for p in fn(ca))) for name,fn in
        [('devices',x.each_device_pair),('pins',x.each_pin_pair),('nets',x.each_net_pair)]}
    circuits=[str(p.status()) for p in x.each_circuit_pair()]
    strict=(raw and circuits==['Match'] and counts['devices']=={'Match':expected_devices}
            and counts['pins']=={'Match':len(ports)} and counts['nets']=={'Match':len(list(ca.each_net()))}
            and len(list(ca.each_net()))==len(list(cb.each_net())) and
            len(list(ca.each_device()))==len(list(cb.each_device()))==expected_devices)
    return {'raw_match':raw,'strict_match':bool(strict),'pair_counts':counts,'circuits':circuits,
        'active_classes':{side:{model:sorted(cl.name for cl in classes)
            for model,classes in sorted(models.items())}
            for side,models in (('layout',ac),('reference',bc))},
        'class_lookup':'exact names from active devices against mutable class enumeration',
        'resistor_symmetry':'rppd_1/rppd_2 only; rppd_sub stays distinct'}

def metal_audit(layout,cell,ports):
    # Independent metal/via-only connectivity check, without implicit labels.
    l=db.Layout();l.dbu=layout.dbu;c=l.create_cell('METAL_AUDIT');c.copy_tree(cell);c.flatten(True)
    state=db.LayoutToNetlist(db.RecursiveShapeIterator(l,c,[]));state.threads=2
    chain=(8,19,10,29,30,49,50,125,126)
    layers={a:state.make_polygon_layer(l.layer(a,0),str(a)) for a in chain}
    for r in layers.values():state.connect(r)
    for a,b in zip(chain,chain[1:]):state.connect(layers[a],layers[b])
    for a in (8,10,30,50,126):
        state.connect(layers[a],state.make_text_layer(l.layer(a,25),str(a)+'text'))
    state.extract_netlist();nets=list(state.netlist().top_circuit().each_net())
    groups={p:[i for i,n in enumerate(nets) if p in n.name.lower().split(',')] for p in set(ports)-{'sub!'}}
    require(all(groups.values()),'Missing metal boundary label')
    for p in groups:
        for q in groups:
            require(p==q or not set(groups[p])&set(groups[q]),'Boundary ports shorted: '+p+'/'+q)
    return {'boundary_components':{p:len(v) for p,v in sorted(groups.items())},
        'ports_mutually_separate':True,'metal_nets':len(nets),'implicit_connections':[]}

def tap_geometry(layout,cell):
    def r(a,b=0):return db.Region(cell.shapes(layout.layer(a,b))).merged()
    for pair in ((1,22),(5,22),(46,21),(60,0),(32,0)):
        require(r(*pair).is_empty(),'Unexpected tap-context geometry in '+cell.name)
    exclude=db.Region()
    for pair in ((5,0),(7,0),(26,0),(33,0),(156,0),(28,0),(128,0),(111,0),
                 (24,0),(1,20),(99,31),(27,0),(27,2)):exclude+=r(*pair)
    tap=((r(1)&r(14)&r(40))-r(31)-exclude).merged()
    require((tap^r(40)).is_empty(),'Tap tie/support geometry differs: '+cell.name)
    # Parent marker labels can be inherited from children; the native deck
    # performs marker recognition and connectivity. This is a geometry measure.
    return sorted([{'A':round(p.area()*layout.dbu**2,8),'P':round(p.perimeter()*layout.dbu,8),
        'bbox':p.bbox().to_s(),'polygon':p.to_s()} for p in tap.each()],key=lambda x:x['A'])


def reduction_audit(pre,post):
    require(pre['nets']==post['nets'] and pre['pins']==post['pins'],'Reduction changed nets/pins')
    require(len(set(pre['nets']))==len(pre['nets']),'Ambiguous physical net names')
    def groups(inv):
        out=defaultdict(list);fixed=[]
        for d in inv['devices']:
            m=d['model'].lower();p=d['parameters'];t=d['terminals']
            require(all(v is not None for v in t.values()),'Unconnected device terminal')
            if m=='ptap1':
                require(set(p)=={'A','P'} and set(t)=={'TIE','WELL'},'Tap interface changed')
                k=(m,t['TIE'],t['WELL'])
            elif m in ('dantenna','dpantenna'):
                require(set(p)=={'A','P','m'} and set(t)=={'A','C'},'Diode interface changed')
                k=(m,t['A'],t['C'],p['A'],p['P'])
            elif m.startswith('sg13_'):
                require(m in ('sg13_lv_nmos','sg13_hv_nmos','sg13_lv_pmos','sg13_hv_pmos') and
                    set(t)=={'S','G','D','B'} and set(p)=={'L','W','AS','AD','PS','PD','rfmode'},'MOS interface changed')
                # D/S exchange is intrinsic to these symmetric MOS classes.
                k=(m,t['G'],t['B'],tuple(sorted((t['S'],t['D']))),p['L'],p['rfmode'])
            else:fixed.append(d);continue
            out[k].append(d)
        return out,sorted(fixed,key=lambda d:d['name'])
    a,fa=groups(pre);b,fb=groups(post);require(fa==fb,'Reduction changed fixed devices')
    require(set(a)==set(b),'Reduction changed model, endpoints, length or RF mode')
    rows=[]
    for k,ds in a.items():
        require(len(b[k])==1,'Parallel group not completely combined');p=b[k][0]['parameters'];m=k[0]
        if m=='ptap1':checks={n:math.fsum(d['parameters'][n] for d in ds) for n in ('A','P')}
        elif m in ('dantenna','dpantenna'):checks={'m':math.fsum(d['parameters']['m'] for d in ds)}
        else:checks={'W':math.fsum(d['parameters']['W'] for d in ds),
            'diffusion_area':math.fsum(d['parameters']['AS']+d['parameters']['AD'] for d in ds),
            'diffusion_perimeter':math.fsum(d['parameters']['PS']+d['parameters']['PD'] for d in ds)}
        actual={**p,'diffusion_area':p.get('AS',0)+p.get('AD',0),'diffusion_perimeter':p.get('PS',0)+p.get('PD',0)}
        require(all(abs(actual[n]-v)<=1e-8 for n,v in checks.items()),'Reduction parameter sum changed: '+str(k))
        rows.append({'group':k,'before':len(ds),'after':1,'parameters':p})
    return {'status':'PARALLEL_REDUCTION_VERIFIED','groups':rows,'net_joins':0,'fixed_devices_unchanged':True,
            'pins_unchanged':True,'nets_unchanged':True}

