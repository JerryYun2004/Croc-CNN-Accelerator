"""Pinned full input-pad diagnostic. All edits are to disposable fixtures."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import klayout.db as db

PAD='sg13cmos5l_IOPadIn'
TOP='cp0c_IOPadInHarness'
LD='sg13cmos5l_LevelDown'
SP='sg13cmos5l_SecondaryProtection'
DP='sg13cmos5l_DCPDiode'
DN='sg13cmos5l_DCNDiode'
PORTS={'p2c','iovdd','iovss','pad','vdd','vss','sub!'}
MODELS={'sg13_lv_nmos':1,'sg13_hv_nmos':1,'sg13_lv_pmos':1,'sg13_hv_pmos':1,
        'rppd':1,'ptap1':2,'dantenna':2,'dpantenna':2}
FAULTS=('vss_tap_area','vss_tap_perimeter','iovss_tap_area','iovss_tap_perimeter',
        'tap_polarity','mos_width','mos_length','mos_bulk_open','res_width','res_length',
        'res_spacing','res_sub_open','missing_resistor','wrong_supply','port_swap',
        'esd_n_area','esd_p_area','esd_n_m','esd_p_m','esd_n_missing','esd_p_missing','esd_n_reverse')
PIN_LABELS={'p2c':(10,39955,169590),'pad':(126,49055,1830),
    'iovdd':(126,41800,81805),'iovss':(126,41800,22545),
    'vss':(50,41450,172270),'vdd':(50,41365,150595)}
JUMPER_BOXES=[(-12000,19000,-10000,131000),(-12000,19000,1000,21000),
    (-12000,49000,1000,51000),(-12000,129000,1000,131000)]

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
    strict=(raw and circuits==['Match'] and counts['devices']=={'Match':11}
            and counts['pins']=={'Match':7} and counts['nets']=={'Match':9})
    return {'raw_match':raw,'strict_match':bool(strict),'pair_counts':counts,'circuits':circuits,
        'active_classes':{side:{model:sorted(cl.name for cl in classes)
            for model,classes in sorted(models.items())}
            for side,models in (('layout',ac),('reference',bc))},
        'class_lookup':'exact names from active devices against mutable class enumeration'}

def metal_audit(layout,cell):
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
    groups={p:[i for i,n in enumerate(nets) if p in n.name.lower().split(',')] for p in PORTS-{'sub!'}}
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

def prepare(gds,reader_cdl,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    source=db.Layout();source.read(str(gds));require(source.dbu==.001,'Unexpected GDS units')
    l=db.Layout();l.dbu=source.dbu;p=l.create_cell(PAD);p.copy_tree(source.cell(PAD))
    require({c.name for c in l.each_cell()}=={PAD,LD,SP,DP,DN},'Pad hierarchy differs')
    before={c.name:fingerprint(l,c) for c in l.each_cell()}
    expected={PAD:[(10.0575,67.65),(11.9235,80.09),(5379.0466,677.04)],
        LD:[(2.019,14.06)],SP:[(4.3112,25.36),(4.7192,27.76)],
        DP:[(33.5104,197.12)],DN:[(141.2964,221.76)]}
    measures={c.name:tap_geometry(l,c) for c in l.each_cell()}
    require({n:[(x['A'],x['P']) for x in rows] for n,rows in measures.items()}==expected,
            'Pinned tap dimensions changed')
    c=l.create_cell(TOP);c.insert(db.CellInstArray(p.cell_index(),db.Trans()))
    for name,(layer,x,y) in PIN_LABELS.items():
        region=db.Region(p.begin_shapes_rec(l.layer(layer,0)))
        require(any(poly.inside(db.Point(x,y)) for poly in region.each()),'Port label outside metal: '+name)
        c.shapes(l.layer(layer,25)).insert(db.Text(name,db.Trans(x,y)))
    open_audit=metal_audit(l,c)
    require(open_audit['boundary_components']=={n:(2 if n=='iovss' else 1) for n in sorted(PORTS-{'sub!'})},
            'Unstrapped boundary differs')
    l.write(str(out/'unstrapped.gds'))
    jumper=db.Region()
    for box in JUMPER_BOXES:jumper.insert(db.Box(*box))
    # Only the three IOVSS top-metal bars may be touched by the jumper.
    existing=db.Region(p.begin_shapes_rec(l.layer(126,0))).merged()
    touched=existing.interacting(jumper)
    require(sorted(poly.bbox().to_s() for poly in touched.each())==
            ['(0,126000;80000,134000)','(0,34500;80000,60000)','(0,7000;80000,32500)'],
            'Jumper touches unexpected top-metal components')
    c.shapes(l.layer(126,0)).insert(jumper)
    closed_audit=metal_audit(l,c)
    require(closed_audit['boundary_components']=={n:1 for n in sorted(PORTS-{'sub!'})} and
            open_audit['metal_nets']==closed_audit['metal_nets']+1,'Jumper did not join exactly the IOVSS groups')
    l.write(str(out/'unmarked.gds'))
    child=l.cell(SP);body=(db.Region(child.shapes(l.layer(5,0)))&
        db.Region(child.shapes(l.layer(14,0)))&db.Region(child.shapes(l.layer(28,0)))&
        db.Region(child.shapes(l.layer(111,0)))).merged()
    require(db.Region(child.shapes(l.layer(128,0))).is_empty(),'Existing PolyRes marker')
    require((body^db.Region(db.Box(940,1370,1940,3370))).is_empty(),'Resistor body differs')
    child.shapes(l.layer(128,0)).insert(body);l.write(str(out/'marker.gds'))
    check=db.Layout();check.read(str(out/'marker.gds'))
    for name,old in before.items():require(fingerprint(check,check.cell(name),True)==old,'Source masks changed: '+name)
    require(fingerprint(check,check.cell(TOP))==fingerprint(l,c),'Wrapper round trip differs')
    require((db.Region(check.cell(SP).shapes(check.layer(128,0)))^body).is_empty(),'Marker round trip differs')
    require(metal_audit(check,check.cell(TOP))==closed_audit,'Marker changed metal connectivity')
    cells=cdl_cells(reader_cdl);order=(SP,LD,DP,DN,PAD)
    # Physical geometry annotations are specific to these exact source cells.
    changes={SP:{'RCP0T_XR1':(9.0304,53.12)},LD:{'RCP0T_XR0':(2.019,14.06)},
        DP:{'RCP0T_XR0':(33.5104,197.12)},DN:{'RCP0T_XR0':(141.2964,221.76)},
        PAD:{'RCP0T_XR1':(21.981,147.74),'RCP0T_XR2':(5379.0466,677.04)}}
    refs={};ledger=[]
    for variant in ('translated','untranslated','unpropagated'):
        lines=['* Disposable complete input-pad diagnostic reference.']
        for name in order:
            propagate=not (variant=='unpropagated' and name==LD)
            lines.append('.SUBCKT '+name+' '+' '.join(cells[name]['pins']+(['sub!'] if propagate else [])))
            for line in cells[name]['body']:
                old=line;words=line.split();device=words[0]
                if device.upper().startswith('X'):
                    require(words[-2]=='/' and words[-1] in order,'Unexpected reference call')
                    if not (variant=='unpropagated' and words[-1]==LD):
                        words.insert(-2,'sub!');line=' '.join(words)
                if variant!='untranslated' and device in changes[name]:
                    area,perimeter=changes[name][device]
                    line,n=re.subn(r'\bA=\S+','A='+str(area)+'p',line);require(n==1,'Missing tap area')
                    line,n=re.subn(r'\bPerim=\S+','Perim='+str(perimeter)+'u',line);require(n==1,'Missing tap perimeter')
                if variant!='untranslated' and name==SP and device=='RR0':
                    require(' ps=180n ' in line,'Source resistor spacing changed');line=line.replace(' ps=180n ',' ps=0 ')
                if line!=old:ledger.append({'variant':variant,'cell':name,'before':old,'after':line})
                lines.append(line)
            lines.append('.ENDS '+name)
        require(cells[PAD]['pins']==['iovdd','iovss','p2c','pad','vdd','vss'],'Pad interface changed')
        interface=cells[PAD]['pins']+['sub!']
        lines+=['.SUBCKT '+TOP+' '+' '.join(interface),'XPAD '+' '.join(interface)+' / '+PAD,'.ENDS '+TOP]
        path=out/(variant+'.cdl');path.write_text('\n'.join(lines)+'\n');refs[variant]=str(path.resolve())
    report={'status':'IO_PAD_FIXTURES_PREPARED','tap_geometry':measures,'annotations':changes,
        'reference_edits':ledger,'unstrapped_metal':open_audit,'strapped_metal':closed_audit,
        'open_jumper_control_pass':True,'jumper_layer':[126,0],'jumper_boxes_dbu':JUMPER_BOXES,
        'source_cells_preserved_except_child_marker':True,'original_files_read_only':True,
        'source_cell_fingerprints':before,'boundary_port':'sub!',
        'physical_harness_connection':'IOVSS only; no implicit connection',
        'expected_translated_taps':{'vss':{'A':24.0,'P':161.8},'iovss':{'A':5562.8838,'P':1149.04}}}
    write_json(out/'geometry.json',report)
    return {'top':TOP,'references':refs,'unmarked':str((out/'unmarked.gds').resolve()),
        'marker':str((out/'marker.gds').resolve()),'geometry':report}

def one(nl,model,kind=None):
    ds=[d for d in nl.top_circuit().each_device() if d.device_class().name.lower()==model]
    if model=='ptap1':
        target=named_pins(nl.top_circuit())[kind]
        ds=[d for d in ds if d.net_for_terminal('TIE').expanded_name()==target.expanded_name()]
    elif model in ('dantenna','dpantenna'):
        ds=[d for d in ds if d.parameter('A')>30]
    require(len(ds)==1,'Ambiguous fault target '+model+'/'+str(kind));return ds[0]

def apply_fault(nl,fault):
    c=nl.top_circuit();t=one(nl,'ptap1','vss');io=one(nl,'ptap1','iovss')
    r=one(nl,'rppd');m=one(nl,'sg13_hv_nmos');dn=one(nl,'dantenna');dp=one(nl,'dpantenna')
    params={'vss_tap_area':(t,'A',24.1),'vss_tap_perimeter':(t,'P',161.9),
        'iovss_tap_area':(io,'A',5562.9838),'iovss_tap_perimeter':(io,'P',1149.14),
        'mos_width':(m,'W',2.75),'mos_length':(m,'L',.5),
        'res_width':(r,'w',1.1),'res_length':(r,'l',2.1),'res_spacing':(r,'ps',.1),
        'esd_n_area':(dn,'A',40),'esd_p_area':(dp,'A',40),'esd_n_m':(dn,'m',1),'esd_p_m':(dp,'m',1)}
    if fault in params:
        d,p,v=params[fault];d.set_parameter(p,v)
    elif fault in ('tap_polarity','esd_n_reverse'):
        d,terms=(t,('TIE','WELL')) if fault=='tap_polarity' else (dn,('A','C'))
        a,b=[d.net_for_terminal(x) for x in terms];d.connect_terminal(terms[0],b);d.connect_terminal(terms[1],a)
    elif fault in ('mos_bulk_open','res_sub_open'):
        d,term=(m,'B') if fault=='mos_bulk_open' else (r,'rppd_sub')
        d.connect_terminal(term,c.create_net('FAULT_OPEN'))
    elif fault in ('missing_resistor','esd_n_missing','esd_p_missing'):
        c.remove_device({'missing_resistor':r,'esd_n_missing':dn,'esd_p_missing':dp}[fault])
    elif fault=='wrong_supply':t.connect_terminal('TIE',named_pins(c)['iovss'])
    elif fault=='port_swap':
        ps={p.name().lower():p.id() for p in c.each_pin()};ns=named_pins(c)
        c.connect_pin(ps['p2c'],ns['pad']);c.connect_pin(ps['pad'],ns['p2c'])
    else:raise RuntimeError('Unknown fault '+fault)
