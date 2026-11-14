"""Filler/corner diagnostic helpers. Only disposable fixtures are modified."""
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
    for models in (ac,bc):
        require(set(models)<={'ptap1'},'Unexpected non-tap device class')
        for classes in models.values():
            for cl in classes:
                params={p.name:p.id() for p in cl.parameter_definitions()}
                require(set(params)=={'A','P'},'Tap parameter interface differs')
                for name in params:cl.enable_parameter(name,True)
                cl.equal_parameters=db.EqualDeviceParameters(params['A'],0,0)+db.EqualDeviceParameters(params['P'],0,0)
                cl.clear_equivalent_terminal_ids()
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
        'class_lookup':'exact names from active devices against mutable class enumeration'}

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


