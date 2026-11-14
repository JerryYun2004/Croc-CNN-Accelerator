"""Output/GPIO diagnostic helpers. Only disposable fixtures are modified."""
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


PREFIX='sg13cmos5l_'
TARGETS={
 'output':{'pad':PREFIX+'IOPadOut16mA','top':'cp0c_IOPadOut16Harness',
  'ports':['c2p','iovdd','iovss','pad','vdd','vss'], 'devices':24,
  'raw_models':{'sg13_lv_nmos':2,'sg13_lv_pmos':2,'sg13_hv_nmos':14,'sg13_hv_pmos':22,
                'ptap1':8,'dantenna':3,'dpantenna':3},
  'models':{'sg13_lv_nmos':2,'sg13_lv_pmos':2,'sg13_hv_nmos':7,'sg13_hv_pmos':7,
            'ptap1':2,'dantenna':2,'dpantenna':2}},
 'gpio':{'pad':PREFIX+'IOPadInOut30mA','top':'cp0c_IOPadInOut30Harness',
  'ports':['c2p','c2p_en','iovdd','iovss','p2c','pad','vdd','vss'],'devices':41,
  'raw_models':{'sg13_lv_nmos':8,'sg13_lv_pmos':8,'sg13_hv_nmos':22,'sg13_hv_pmos':37,
                'ptap1':15,'dantenna':4,'dpantenna':4,'rppd':1},
  'models':{'sg13_lv_nmos':8,'sg13_lv_pmos':8,'sg13_hv_nmos':8,'sg13_hv_pmos':8,
            'ptap1':2,'dantenna':3,'dpantenna':3,'rppd':1}}
}
JUMPERS={'iovss':[(-12000,19000,-10000,131000),(-12000,19000,1000,21000),
                   (-12000,49000,1000,51000),(-12000,129000,1000,131000)],
         'iovdd':[(90000,77500,92000,107250),(79000,77500,92000,79500),
                  (79000,105250,92000,107250)]}
TOUCHES={'iovss':['(0,126000;80000,134000)','(0,34500;80000,60000)','(0,7000;80000,32500)'],
         'iovdd':['(0,66000;80000,91500)','(0,93500;80000,119000)']}
# Geometry measured from the pinned source GDS, independently of LVS matching.
TAPS={'DCPDiode':[(33.5104,197.12)],'DCNDiode':[(141.2964,221.76)],
      'Clamp_P8N8D':[(67.0344,394.32)],'Clamp_N8N8D':[(55.7736,328.08)],
      'Clamp_P15N15D':[(67.0344,394.32)],'Clamp_N15N15D':[(55.7736,328.08)],
      'LevelUpInv':[(1.053,7.62)],'LevelUp':[(.9135,6.69)],'LevelDown':[(2.019,14.06)],
      'SecondaryProtection':[(4.3112,25.36),(4.7192,27.76)],
      'io_nand2_x1':[(.657,4.98)],'io_nor2_x1':[(.657,4.98)],
      'io_inv_x1':[(.624,4.76)],'io_tie':[(.6255,4.77)],
      'GateDecode':[],'GateLevelUpInv':[],
      'IOPadOut16mA':[(24.,160.6),(4313.4,594.62)],
      'IOPadInOut30mA':[(24.,160.6),(4319.178,671.66)]}

def prepare(gds,reader_cdl,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    source=db.Layout();source.read(str(gds));require(source.dbu==.001,'Unexpected GDS units')
    cells=cdl_cells(reader_cdl);result={}
    for key,target in TARGETS.items():
        dest=out/key;dest.mkdir()
        pad=target['pad'];top=target['top'];ports=target['ports']
        l=db.Layout();l.dbu=source.dbu;p=l.create_cell(pad);p.copy_tree(source.cell(pad))
        names=sorted(c.name for c in l.each_cell());before={n:fingerprint(l,l.cell(n)) for n in names}
        expected_names={pad,PREFIX+'DCPDiode',PREFIX+'DCNDiode'}
        if key=='output':expected_names|={PREFIX+x for x in ('Clamp_P8N8D','Clamp_N8N8D','LevelUpInv','GateLevelUpInv')}
        else:expected_names|={PREFIX+x for x in ('Clamp_P15N15D','Clamp_N15N15D','LevelDown','SecondaryProtection',
            'LevelUp','GateDecode','io_nand2_x1','io_nor2_x1','io_inv_x1','io_tie')}
        require(set(names)==expected_names,'Signal pad hierarchy changed')
        measures={n:tap_geometry(l,l.cell(n)) for n in names};changes={}
        for n,rows in measures.items():
            require([(r['A'],r['P']) for r in rows]==TAPS[n.removeprefix(PREFIX)],'Tap geometry changed: '+n)
            taps=[line.split() for line in cells[n]['body'] if 'MODEL=ptap1' in line]
            if n==pad:
                require(len(taps)==2 and {t[1] for t in taps}=={'vss','iovss'},'Pad tap reference changed')
                changes[n]={t[0]:tuple((rows[0] if t[1]=='vss' else rows[1])[a] for a in ('A','P')) for t in taps}
            elif rows:
                require(len(taps)==1,'Ambiguous child tap annotation: '+n)
                changes[n]={taps[0][0]:tuple(round(sum(r[a] for r in rows),8) for a in ('A','P'))}
            else:require(not taps,'Reference has unexplained tap: '+n);changes[n]={}
        c=l.create_cell(top);c.insert(db.CellInstArray(p.cell_index(),db.Trans()))
        points={'c2p':(10,40000,173500),'iovdd':(126,40000,78750),'iovss':(126,40000,19750),
            'pad':(126,40000,1500),'vdd':(50,40000,147900),'vss':(50,40000,170100)}
        if key=='gpio':points.update({'c2p':(10,18475,179045),'c2p_en':(10,21525,177023),'p2c':(10,59975,168905)})
        for n,(a,x,y) in points.items():
            require(any(poly.inside(db.Point(x,y)) for poly in db.Region(p.begin_shapes_rec(l.layer(a,0))).each()),'Port outside metal: '+n)
            c.shapes(l.layer(a,25)).insert(db.Text(n,db.Trans(x,y)))
        audits={};audits['unstrapped']=metal_audit(l,c,ports)
        require(audits['unstrapped']['boundary_components']=={n:2 if n in JUMPERS else 1 for n in ports},'Unstrapped boundary changed')
        l.write(str(dest/'unstrapped.gds'))
        existing=db.Region(p.begin_shapes_rec(l.layer(126,0))).merged()
        for rail in ('iovss','iovdd'):
            jumper=db.Region();[jumper.insert(db.Box(*b)) for b in JUMPERS[rail]]
            require(sorted(q.bbox().to_s() for q in existing.interacting(jumper).each())==TOUCHES[rail],
                    'Jumper touches unexpected components: '+rail)
            c.shapes(l.layer(126,0)).insert(jumper)
            audits['after_'+rail]=metal_audit(l,c,ports)
        require(audits['after_iovss']['boundary_components']=={n:2 if n=='iovdd' else 1 for n in ports},'IOVSS jumper control failed')
        require(audits['after_iovdd']['boundary_components']=={n:1 for n in ports},'Rails remain split')
        require(audits['unstrapped']['metal_nets']==audits['after_iovss']['metal_nets']+1==audits['after_iovdd']['metal_nets']+2,
                'Jumpers joined unrelated metal nets')
        l.write(str(dest/'unmarked.gds'))
        if key=='gpio':
            child=l.cell(PREFIX+'SecondaryProtection')
            body=(db.Region(child.shapes(l.layer(5,0)))&db.Region(child.shapes(l.layer(14,0)))&
                  db.Region(child.shapes(l.layer(28,0)))&db.Region(child.shapes(l.layer(111,0)))).merged()
            require(db.Region(child.shapes(l.layer(128,0))).is_empty(),'Unexpected PolyRes marker')
            require((body^db.Region(db.Box(940,1370,1940,3370))).is_empty(),'Child resistor geometry changed')
            child.shapes(l.layer(128,0)).insert(body)
        l.write(str(dest/'candidate.gds'));check=db.Layout();check.read(str(dest/'candidate.gds'))
        for n,old in before.items():require(fingerprint(check,check.cell(n),True)==old,'Source mask changed: '+n)
        require(fingerprint(check,check.cell(top))==fingerprint(l,c),'Wrapper round trip differs')
        require(metal_audit(check,check.cell(top),ports)==audits['after_iovdd'],'Marker changed metal connectivity')
        if key=='gpio':require((db.Region(check.cell(PREFIX+'SecondaryProtection').shapes(check.layer(128,0)))^body).is_empty(),'Marker round trip differs')
        refs={};ledger=[]
        dropped=PREFIX+('LevelUpInv' if key=='output' else 'LevelDown')
        for variant in ('translated','untranslated','unpropagated'):
            lines=['* Disposable output/GPIO harness reference. Source CDL unchanged.']
            for n in names:
                propagate=not(variant=='unpropagated' and n==dropped)
                lines.append('.SUBCKT '+n+' '+' '.join(cells[n]['pins']+(['sub!'] if propagate else [])))
                for original in cells[n]['body']:
                    line=original;words=line.split();device=words[0]
                    if device.upper().startswith('X'):
                        require(words[-2]=='/' and words[-1] in names,'Unexpected CDL call')
                        if not(variant=='unpropagated' and words[-1]==dropped):words.insert(-2,'sub!');line=' '.join(words)
                    if variant!='untranslated' and device in changes[n]:
                        a,per=changes[n][device]
                        line,k=re.subn(r'\bA=\S+','A='+str(a)+'p',line);require(k==1,'Missing tap area')
                        line,k=re.subn(r'\bPerim=\S+','Perim='+str(per)+'u',line);require(k==1,'Missing tap perimeter')
                    if variant!='untranslated' and n==PREFIX+'SecondaryProtection' and device=='RR0':
                        require(' ps=180n ' in line,'Resistor spacing changed');line=line.replace(' ps=180n ',' ps=0 ')
                    if line!=original:ledger.append({'variant':variant,'cell':n,'before':original,'after':line})
                    lines.append(line)
                lines.append('.ENDS '+n)
            require(cells[pad]['pins']==ports,'Pad interface changed')
            interface=ports+['sub!'];lines+=['.SUBCKT '+top+' '+' '.join(interface),'XPAD '+' '.join(interface)+' / '+pad,'.ENDS '+top]
            path=dest/(variant+'.cdl');path.write_text('\n'.join(lines)+'\n');refs[variant]=str(path.resolve())
        report={'status':'IO_SIGNAL_FIXTURES_PREPARED','tap_geometry':measures,'annotations':changes,'reference_edits':ledger,
            'metal_audits':audits,'jumper_boxes_dbu':JUMPERS,'jumper_layer':[126,0],'physical_jumper_controls_pass':True,
            'source_fingerprints':before,'source_masks_preserved_except_gpio_child_marker':True,
            'net_joins':0,'implicit_connections':[],'blackboxes':[]}
        write_json(dest/'geometry.json',report)
        result[key]={**target,'references':refs,'candidate':str((dest/'candidate.gds').resolve()),
            'unmarked':str((dest/'unmarked.gds').resolve()),'geometry':report}
    return result

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

def faults_for(key):
    f=['vss_tap_area','vss_tap_perimeter','iovss_tap_area','iovss_tap_perimeter','tap_polarity','wrong_supply','port_swap']
    for polarity in ('n','p'):
        f += ['driver_'+polarity+'_'+v for v in ('width','length','bulk_open','gate_open','missing')]
        f += ['esd_'+polarity+'_'+v for v in ('area','m','missing','reverse')]
    if key=='gpio':f+=['enable_open','receive_open','res_width','res_length','res_spacing','res_sub_open','missing_resistor']
    return tuple(f)

def apply_fault(nl,fault):
    c=nl.top_circuit();pins=named_pins(c);ds=list(c.each_device())
    def one(model,select=lambda d:True):
        matches=[d for d in ds if d.device_class().name.lower()==model and select(d)]
        require(len(matches)==1,'Ambiguous fault target '+model);return matches[0]
    def open_term(d,t):d.connect_terminal(t,c.create_net('FAULT_OPEN'))
    if fault.startswith(('vss_tap_','iovss_tap_')) or fault in ('tap_polarity','wrong_supply'):
        port='iovss' if fault.startswith('iovss') else 'vss'
        d=one('ptap1',lambda d:d.net_for_terminal('TIE').expanded_name()==pins[port].expanded_name())
        if fault.endswith('area'):d.set_parameter('A',d.parameter('A')+.1)
        elif fault.endswith('perimeter'):d.set_parameter('P',d.parameter('P')+.1)
        elif fault=='wrong_supply':d.connect_terminal('TIE',pins['iovss'])
        else:
            a,b=d.net_for_terminal('TIE'),d.net_for_terminal('WELL');d.connect_terminal('TIE',b);d.connect_terminal('WELL',a)
    elif fault.startswith('driver_'):
        _,pol,op=fault.split('_',2);d=one('sg13_hv_'+pol+'mos',lambda d:d.parameter('W')>30)
        if op in ('width','length'):
            p='W' if op=='width' else 'L';d.set_parameter(p,d.parameter(p)+.1)
        elif op.endswith('_open'):open_term(d,'B' if op=='bulk_open' else 'G')
        elif op=='missing':c.remove_device(d)
        else:raise RuntimeError('Unknown driver control')
    elif fault.startswith('esd_'):
        _,pol,op=fault.split('_');d=one('dantenna' if pol=='n' else 'dpantenna',lambda d:d.parameter('A')>30)
        if op=='area':d.set_parameter('A',d.parameter('A')+5)
        elif op=='m':d.set_parameter('m',d.parameter('m')+1)
        elif op=='missing':c.remove_device(d)
        elif op=='reverse':
            a,b=d.net_for_terminal('A'),d.net_for_terminal('C');d.connect_terminal('A',b);d.connect_terminal('C',a)
        else:raise RuntimeError('Unknown ESD control')
    elif fault.startswith('res_') or fault=='missing_resistor':
        d=one('rppd')
        if fault=='missing_resistor':c.remove_device(d)
        elif fault=='res_sub_open':open_term(d,'rppd_sub')
        else:
            p={'res_width':'w','res_length':'l','res_spacing':'ps'}[fault];d.set_parameter(p,d.parameter(p)+.1)
    elif fault=='port_swap':
        pids={p.name().lower():p.id() for p in c.each_pin()};c.connect_pin(pids['c2p'],pins['pad']);c.connect_pin(pids['pad'],pins['c2p'])
    elif fault in ('enable_open','receive_open'):
        name='c2p_en' if fault=='enable_open' else 'p2c';p=next(p for p in c.each_pin() if p.name().lower()==name)
        c.connect_pin(p.id(),c.create_net('FAULT_PORT_OPEN'))
    else:raise RuntimeError('Unknown fault '+fault)
