"""Geometry preparation and strict replay for one pinned I/O parent."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import klayout.db as db

TOP='sg13cmos5l_LevelDown'
CHILD='sg13cmos5l_SecondaryProtection'
PORTS={'core','iovdd','iovss','pad','vdd','vss','sub!'}
MODELS={'sg13_lv_nmos':1,'sg13_hv_nmos':1,'sg13_lv_pmos':1,'sg13_hv_pmos':1,
        'rppd':1,'ptap1':2,'dantenna':1,'dpantenna':1}
FAULTS=('parent_tap_area','parent_tap_perimeter','child_tap_perimeter','tap_polarity',
        'mos_width','mos_length','mos_bulk_open','child_substrate_open','wrong_supply',
        'res_width','res_length','res_spacing','missing_resistor','port_swap')

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

def measure_tap(layout,cell):
    def r(a,b=0):return db.Region(cell.shapes(layout.layer(a,b))).merged()
    for pair in ((1,22),(5,22),(46,21),(60,0),(32,0)):
        require(r(*pair).is_empty(),'Unexpected well/context geometry in '+cell.name)
    sub=r(40); labels=[s.text for s in cell.shapes(layout.layer(63,0)).each()
                      if s.is_text() and s.text.string.lower()=='sub!']
    require(not sub.is_empty() and all(any(p.inside(t.trans.disp) for t in labels) for p in sub.each()),
            'Missing labelled substrate shape')
    exclude=db.Region()
    for pair in ((5,0),(7,0),(26,0),(33,0),(156,0),(28,0),(128,0),(111,0),
                 (24,0),(1,20),(99,31),(27,0),(27,2)):exclude+=r(*pair)
    tap=(((r(1)&r(14))&sub)-r(31)-exclude).merged()
    require((tap^sub).is_empty(),'Tap derivation differs from labelled physical shapes')
    return {'A':round(tap.area()*layout.dbu**2,8),'P':round(tap.perimeter()*layout.dbu,8),
            'components':tap.count(),'polygons_dbu':[p.to_s() for p in tap.each()]}

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

def prepare(gds,reader_cdl,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    lib=db.Layout();lib.read(str(gds));require(lib.dbu==.001,'Unexpected GDS units')
    source=lib.cell(TOP);require(source is not None,'Missing parent master')
    l=db.Layout();l.dbu=lib.dbu;c=l.create_cell(TOP);c.copy_tree(source)
    require({x.name for x in l.each_cell()}=={TOP,CHILD},'Unexpected parent hierarchy')
    inst=list(c.each_inst());require(len(inst)==1 and inst[0].cell.name==CHILD and
        str(inst[0].trans)=='r0 0,-8330','Child placement changed')
    before={x.name:fingerprint(l,x) for x in l.each_cell()}
    l.write(str(out/'original.gds'))
    measured={x.name:measure_tap(l,x) for x in l.each_cell()}
    require({k:(v['A'],v['P'],v['components']) for k,v in measured.items()}==
            {TOP:(2.019,14.06,1),CHILD:(9.0304,53.12,2)},'Pinned tap geometry differs')
    child=l.cell(CHILD); marker=l.layer(128,0)
    require(db.Region(child.shapes(marker)).is_empty(),'Recognition marker already present')
    body=db.Region(child.shapes(l.layer(5,0))) & db.Region(child.shapes(l.layer(14,0)))
    body &= db.Region(child.shapes(l.layer(28,0))); body &= db.Region(child.shapes(l.layer(111,0)))
    require((body^db.Region(db.Box(940,1370,1940,3370))).is_empty(),'Straight resistor shape differs')
    child.shapes(marker).insert(body);l.write(str(out/'marker.gds'))
    check=db.Layout();check.read(str(out/'marker.gds'))
    for x in check.each_cell():
        require(fingerprint(check,x,True)==before[x.name],'Geometry changed beyond the child recognition marker')
    require((db.Region(check.cell(CHILD).shapes(check.layer(128,0)))^body).is_empty(),'Marker round trip failed')
    cells=cdl_cells(reader_cdl)
    require(cells[TOP]['pins']==['core','iovdd','iovss','pad','vdd','vss'] and
            cells[CHILD]['pins']==['core','minus','pad','plus'],'CDL interface changed')
    require(len(cells[TOP]['body'])==6 and len(cells[CHILD]['body'])==4,'CDL inventory changed')
    expected_call='XI0 net4 iovss pad iovdd / '+CHILD
    require(expected_call in cells[TOP]['body'],'Parent call changed')
    refs={};ledger=[]
    for variant in ('untranslated','unpropagated','translated'):
        lines=['* Disposable LevelDown reference; original library is preserved.']
        for name in (CHILD,TOP):
            pins=list(cells[name]['pins'])
            # Explicit harness port; the body remains separate from both local
            # supply terminals. No net is joined to VSS/IOVSS in this fixture.
            if name==TOP or variant!='unpropagated':pins+=['sub!']
            lines.append('.SUBCKT '+name+' '+' '.join(pins))
            for line in cells[name]['body']:
                original=line
                if line==expected_call and variant!='unpropagated':
                    line='XI0 net4 iovss pad iovdd sub! / '+CHILD
                if variant!='untranslated':
                    if line.startswith('RCP0T_'):
                        old={'A':'2.016p','Perim':'5.68u'} if name==TOP else {'A':'9.03p','Perim':'12.02u'}
                        for key,v in old.items():
                            require(line.split().count(key+'='+v)==1,'Tap source parameter changed')
                            value=measured[name]['A' if key=='A' else 'P'];unit='p' if key=='A' else 'u'
                            line=line.replace(key+'='+v,key+'='+str(value)+unit)
                    elif line.startswith('RR0 '):
                        require(name==CHILD and ' ps=180n ' in line and line.endswith(' b=0'),'Resistor source differs')
                        line=line.replace(' ps=180n ',' ps=0 ')
                if line!=original:ledger.append({'variant':variant,'cell':name,'before':original,'after':line})
                lines.append(line)
            lines.append('.ENDS '+name)
        path=out/(variant+'.cdl');path.write_text('\n'.join(lines)+'\n');refs[variant]=str(path.resolve())
    report={'status':'IO_PARENT_FIXTURES_PREPARED','top':TOP,'tap_geometry':measured,
        'marker_cell':CHILD,'marker_layer':[128,0],'marker_area_um2':2.0,
        'source_geometry_unchanged':True,'copy_changes':'One child PolyRes recognition marker only',
        'reference_edits':ledger,'boundary_port':'sub!','boundary_port_short_to_supply':False,
        'source_cell_fingerprints':before}
    write_json(out/'geometry.json',report)
    return {'top':TOP,'original':str((out/'original.gds').resolve()),'marker':str((out/'marker.gds').resolve()),
            'references':refs,'geometry':report}

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

def compare(a,b):
    ca,cb=a.top_circuit(),b.top_circuit();pa,pb=named_pins(ca),named_pins(cb)
    require(set(pa)==set(pb)==PORTS,'Unexpected parent interface')
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
                term=db.EqualDeviceParameters(params[name],0,tol);eq=term if eq is None else eq+term
            cl.equal_parameters=eq
            if model=='ptap1':cl.clear_equivalent_terminal_ids()
    for name in PORTS:cmp.same_nets(pa[name],pb[name],True)
    x=db.NetlistCrossReference();raw=bool(cmp.compare(a,b,x))
    counts={name:dict(Counter(str(p.status()) for p in fn(ca))) for name,fn in
        [('devices',x.each_device_pair),('pins',x.each_pin_pair),('nets',x.each_net_pair)]}
    circuits=[str(p.status()) for p in x.each_circuit_pair()]
    strict=(raw and circuits==['Match'] and counts['devices']=={'Match':9}
            and counts['pins']=={'Match':7} and counts['nets']=={'Match':9})
    return {'raw_match':raw,'strict_match':bool(strict),'pair_counts':counts,'circuits':circuits,
        'active_classes':{side:{model:sorted(cl.name for cl in classes)
            for model,classes in sorted(models.items())}
            for side,models in (('layout',ac),('reference',bc))},
        'class_lookup':'exact names from active devices against mutable class enumeration'}

def select(nl,model,which=None):
    ds=[d for d in nl.top_circuit().each_device() if d.device_class().name.lower()==model]
    if which=='parent':ds=[d for d in ds if abs(d.parameter('A')-2.019)<1e-8]
    if which=='child':ds=[d for d in ds if abs(d.parameter('A')-9.0304)<1e-8]
    require(len(ds)==1,'Ambiguous fault target '+model+'/'+str(which));return ds[0]

def apply_fault(nl,fault):
    c=nl.top_circuit();t=select(nl,'ptap1','parent');ch=select(nl,'ptap1','child')
    r=select(nl,'rppd');m=select(nl,'sg13_hv_nmos')
    changes={'parent_tap_area':(t,'A',2.119),'parent_tap_perimeter':(t,'P',14.16),
        'child_tap_perimeter':(ch,'P',53.22),'mos_width':(m,'W',2.75),'mos_length':(m,'L',.5),
        'res_width':(r,'w',1.1),'res_length':(r,'l',2.1),'res_spacing':(r,'ps',.1)}
    if fault in changes:
        d,p,v=changes[fault];d.set_parameter(p,v)
    elif fault=='tap_polarity':
        a,b=t.net_for_terminal('TIE'),t.net_for_terminal('WELL')
        t.connect_terminal('TIE',b);t.connect_terminal('WELL',a)
    elif fault=='mos_bulk_open':m.connect_terminal('B',c.create_net('CP0P_MOS_BULK_OPEN'))
    elif fault=='child_substrate_open':ch.connect_terminal('WELL',c.create_net('CP0P_CHILD_BULK_OPEN'))
    elif fault=='wrong_supply':t.connect_terminal('TIE',named_pins(c)['iovss'])
    elif fault=='missing_resistor':c.remove_device(r)
    elif fault=='port_swap':
        ids={p.name().lower():p.id() for p in c.each_pin()};ps=named_pins(c)
        c.connect_pin(ids['core'],ps['pad']);c.connect_pin(ids['pad'],ps['core'])
    else:raise RuntimeError('Unknown control '+fault)
