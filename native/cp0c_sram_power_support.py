"""Bounded SRAM supply-via planning and verification for the reviewed CP0C design."""
from collections import Counter
import hashlib,json,re
from pathlib import Path

DEF_SHA='ed9bb4299f13bb8e71efb1092f43f5f718693c8ae4be7e588630362774249d31'
ODB_SHA='67bba20d204c54a94dc4b1bcf573279ff05b899c39bfd220dd9f8e7a7fc9d9a3'
SDC_SHA='f0216b26bf10e67bee572a0c8058413f6f0cbfb2c9d167368fb17bc92efe202c'
LEF_SHA='6e483caff960deaeb97e69a205bbb811b94b4aba15d27d48658e9d5248733cfd'
GDS_SHA='4808589ebcb00c10d331854db001a7cfd7cb0cac2efe3847a83cad7a745a59fa'
SRAM='RM_IHPSG13_1P_512x32_c2_bm_bist'
VIA='CP0C_SRAM_TV1_3X3'
LAYERS={'Metal4':(50,0),'TopVia1':(125,0),'TopMetal1':(126,0)}
EXPECTED={'VDD!':40,'VDDARRAY!':32,'VSS!':72}

def require(ok,msg):
    if not ok:raise RuntimeError(msg)
def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write_json(path,obj):Path(path).write_text(json.dumps(obj,indent=2)+'\n')
def norm(s):return ' '.join(s.split())
def section(s,name):
    m=re.search(r'^'+re.escape(name)+r'\b[^\n]*\n(.*?)^END '+re.escape(name)+r'\b[^\n]*',s,re.M|re.S)
    require(m is not None,'Missing DEF section '+name);return m

def records(s,name):
    block=section(s,name)[1]
    rows=re.findall(r'^\s*-\s+(\S+)\s(.*?);',block,re.M|re.S)
    require(len(rows)==len({k for k,v in rows}),'Duplicate DEF records in '+name)
    return dict(rows)

def rect_inside(a,b):return b[0]<=a[0]<a[2]<=b[2] and b[1]<=a[1]<a[3]<=b[3]
def overlap(a,b):return min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])
def via_boxes():
    return [('Metal4',[-1150,-1150,1150,1150]),('TopMetal1',[-1470,-1470,1470,1470])]+[
        ('TopVia1',[x-210,y-210,x+210,y+210]) for x in (-840,0,840) for y in (-840,0,840)]
def moved(box,x,y):return [box[0]+x,box[1]+y,box[2]+x,box[3]+y]

def power_routes(s):
    result={}
    for net,body in records(s,'SPECIALNETS').items():
        parts=re.split(r'\s*(?:\+\s+ROUTED|\bNEW)\s+',body)
        result[net]=(norm(parts[0]),Counter(norm(p) for p in parts[1:] if p.strip()))
    return result

def sram_instances(s):
    """Preserve OpenDB names byte for byte from this pinned writer's DEF.

    DefOut::Impl::writeInst writes inst->getName() directly. Removing its
    backslashes changes the key used by dbBlock::findInst's exact lookup.
    These are data strings transported through TSV, not Tcl source literals.
    """
    macro=[]
    for name,body in records(s,'COMPONENTS').items():
        if body.split()[0]!=SRAM:continue
        m=re.search(r'\+ FIXED \( (\d+) (\d+) \) N',body)
        require(m is not None,'Expected fixed, unrotated SRAM.')
        macro.append((name,int(m[1]),int(m[2])))
    require(sorted((x,y) for _,x,y in macro)==[(350240,1372480),(1149280,1372480)],'SRAM placements differ.')
    return macro

def plan(defpath,lefpath):
    require(sha(defpath)==DEF_SHA,'Routed DEF is not the reviewed candidate.')
    require(sha(lefpath)==LEF_SHA,'SRAM LEF differs from the reviewed physical abstract.')
    s=Path(defpath).read_text();lef=Path(lefpath).read_text()
    require('UNITS DISTANCE MICRONS 1000 ;' in s,'DEF units differ.')
    require(VIA not in s,'Candidate already contains this ECO.')
    macro=sram_instances(s)
    pins={}
    for pin in EXPECTED:
        m=re.search(r'^  PIN '+re.escape(pin)+r'\s*\n(.*?)^  END '+re.escape(pin)+r'\s*$',lef,re.M|re.S)
        require(m is not None,'Missing SRAM LEF pin '+pin)
        rr=re.findall(r'LAYER (\S+)\s*;\s*RECT ([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+)\s*;',m[1])
        require(rr and all(r[0]=='Metal4' for r in rr),'Unexpected supply pin layer')
        pins[pin]=[list(map(lambda v:round(float(v)*1000),r[1:])) for r in rr]
    routes=power_routes(s);stripes={};oldvias={}
    for net in ('VDD','VSS'):
        stripes[net]=[];oldvias[net]=[]
        for path,multiplicity in routes[net][1].items():
            m=re.fullmatch(r'TopMetal1 (\d+) \+ SHAPE STRIPE \( (\d+) (\d+) \) \( (\d+) (\d+) \)',path)
            if m:
                w,x1,y1,x2,y2=map(int,m.groups());require(y1==y2 and w%2==0,'Unexpected TM1 stripe geometry')
                stripes[net].extend([[min(x1,x2),y1-w//2,max(x1,x2),y1+w//2]]*multiplicity)
            v=re.fullmatch(r'Metal4 0 \+ SHAPE \S+ \( (\d+) (\d+) \) (\S+)',path)
            if v:oldvias[net].extend([(int(v[1]),int(v[2]),v[3])]*multiplicity)
        require(len(stripes[net])>=12,'Power stripe inventory incomplete')
    result=[]
    for inst,dx,dy in sorted(macro):
        require(not any(dx<=x<=dx+416640 and dy<=y<=dy+191340 for vs in oldvias.values() for x,y,_ in vs),
                'Reviewed missing-via condition no longer holds.')
        for pin,localpins in pins.items():
            net='VSS' if pin=='VSS!' else 'VDD';other='VDD' if net=='VSS' else 'VSS'
            for p in localpins:
                pr=moved(p,dx,dy)
                for stripe in stripes[net]:
                    if not overlap(pr,stripe):continue
                    x=(max(pr[0],stripe[0])+min(pr[2],stripe[2]))//2
                    y=(max(pr[1],stripe[1])+min(pr[3],stripe[3]))//2
                    lower=moved(via_boxes()[0][1],x,y);upper=moved(via_boxes()[1][1],x,y)
                    require(x%5==y%5==0,'Via center is off the 5 nm grid')
                    require(rect_inside(lower,pr) and rect_inside(upper,stripe),'3x3 via footprint does not fit')
                    require(not any(overlap(upper,r) for r in stripes[other]),'Via touches opposite-net TM1')
                    require(not any(overlap(lower,moved(q,dx,dy)) for p2,qq in pins.items()
                                    if ('VSS' if p2=='VSS!' else 'VDD')!=net for q in qq),'Via touches opposite SRAM supply')
                    result.append({'instance':inst,'pin':pin,'net':net,'x':x,'y':y,'pin_rect':pr,'stripe_rect':stripe})
    counts=Counter((r['instance'],r['pin']) for r in result)
    require(len(result)==288 and all(counts[(inst,p)]==n for inst,_,_ in macro for p,n in EXPECTED.items()),'Unexpected supply crossing coverage')
    require(len({(r['x'],r['y']) for r in result})==288,'Duplicate via placement')
    return {'version':'1.1-exact-instance-names','via_name':VIA,'arrays':288,'cuts':2592,'via_boxes':via_boxes(),
            'old_TopVia1_arrays':{n:len(v) for n,v in oldvias.items()},'old_arrays_in_SRAM_footprints':0,
            'source_def_sha256':DEF_SHA,'source_lef_sha256':LEF_SHA,'placements':result}

def emit_plan(p,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);write_json(out/'plan.json',p)
    (out/'placements.tsv').write_text('net\tinstance\tpin\tx\ty\n'+''.join(
        '\t'.join(str(r[k]) for k in ('net','instance','pin','x','y'))+'\n' for r in p['placements']))

def patch_def_for_test(source,dest,p):
    """Independent DEF construction used for parser and geometry validation only."""
    s=Path(source).read_text();v=section(s,'VIAS');head=s[v.start():s.index('\n',v.start())]
    n=int(re.search(r'VIAS (\d+)',head)[1]);added='    - '+VIA+''.join(
        f' + RECT {layer} ( {b[0]} {b[1]} ) ( {b[2]} {b[3]} )' for layer,b in via_boxes())+' ;\n'
    s=s[:v.start()]+f'VIAS {n+1} ;\n'+v[1]+added+'END VIAS'+s[v.end():]
    sp=section(s,'SPECIALNETS');body=sp[1]
    for net in ('VDD','VSS'):
        m=re.search(r'(^\s*-\s+'+net+r'\s.*?);',body,re.M|re.S);require(m is not None,'Missing supply')
        extra=''.join(f'\n      NEW Metal4 0 + SHAPE STRIPE ( {r["x"]} {r["y"]} ) {VIA}' for r in p['placements'] if r['net']==net)
        body=body[:m.end()-1]+extra+' ;'+body[m.end():]
    s=s[:sp.start(1)]+body+s[sp.end(1):];Path(dest).write_text(s)

def audit_def_delta(before,after,p):
    a=Path(before).read_text();b=Path(after).read_text()
    # All sections except physical power routes and via definitions must agree.
    def remainder(s):
        for name in ('SPECIALNETS','VIAS'):
            m=section(s,name);s=s[:m.start()]+s[m.end():]
        return norm(s)
    require(remainder(a)==remainder(b),'Non-PDN DEF content changed (includes cells, pins, signal routing).')
    av,bv=records(a,'VIAS'),records(b,'VIAS')
    require(set(bv)==set(av)|{VIA},'Via definition scope changed')
    require(all(norm(av[k])==norm(bv[k]) for k in av),'Existing via definition changed')
    rr=re.findall(r'\+ RECT (\S+) \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)',norm(bv[VIA]))
    actual=Counter((r[0],tuple(map(int,r[1:]))) for r in rr)
    require(actual==Counter((layer,tuple(box)) for layer,box in via_boxes()),'Inserted via geometry differs')
    ar,br=power_routes(a),power_routes(b);require(ar.keys()==br.keys(),'Power net set changed')
    for net in ar:
        require(ar[net][0]==br[net][0],'Power pin assignments changed on '+net)
        expected=Counter(ar[net][1]);expected.update(f'Metal4 0 + SHAPE STRIPE ( {r["x"]} {r["y"]} ) {VIA}' for r in p['placements'] if r['net']==net)
        require(expected==br[net][1],'Unexpected power-wire change on '+net)
    return {'status':'DEF_DELTA_VERIFIED','arrays_added':288,'cuts_added':2592,'signal_routing_unchanged':True,'cell_placements_unchanged':True,'old_power_geometry_unchanged':True}

def audit_supply_database(path):
    import klayout.db as db
    saved=db.LayoutVsSchematic();saved.read(str(path));nl=saved.netlist()
    macro=nl.circuit_by_name(SRAM);top=nl.circuit_by_name('croc_chip')
    require(macro is not None and top is not None,'Missing extracted SRAM/chip circuit')
    pins={p.name():p for p in macro.each_pin()}
    require(all(p in pins for p in EXPECTED),'Extracted SRAM still lacks a supply boundary pin')
    rows=[]
    for sc in top.each_subcircuit():
        if sc.circuit_ref().name!=SRAM:continue
        mapping={name:sc.net_for_pin(pins[name].id()).name if sc.net_for_pin(pins[name].id()) else None for name in EXPECTED}
        require(mapping=={'VDD!':'VDD','VDDARRAY!':'VDD','VSS!':'VSS'},'Extracted SRAM supply is open or on the wrong net: '+repr(mapping))
        rows.append({'instance_id':sc.id(),'supply_nets':mapping})
    require(len(rows)==2,'Expected exactly two extracted SRAM instances')
    errors=[str(e) for e in saved.each_error()];require(not errors,'Extraction errors remain')
    return {'status':'EXTRACTED_SRAM_SUPPLIES_CONNECTED','instances':rows,'macro_pin_count':len(pins),'extraction_errors':errors,'full_lvs_pass_implied':False}

def audit_gds_delta(before,after,p):
    """Hierarchy-preserving comparison: only one new via cell and its placements."""
    import klayout.db as db
    a,b=db.Layout(),db.Layout();a.read(str(before));b.read(str(after))
    require(a.dbu==b.dbu==.001,'Unexpected GDS database unit')
    names_a={c.name for c in a.each_cell()};names_b={c.name for c in b.each_cell()}
    extra=names_b-names_a
    require(names_a<=names_b and len(extra)==1,'Unexpected GDS cell set change')
    via_name=next(iter(extra));via=b.cell(via_name)
    require(via.child_instances()==0,'ECO via unexpectedly hierarchical')
    expected={}
    for layer,box in via_boxes():
        expected.setdefault(LAYERS[layer],db.Region()).insert(db.Box(*box))
    for li in b.layer_indexes():
        info=b.get_info(li);key=(info.layer,info.datatype)
        got=db.Region(via.shapes(li));want=expected.get(key,db.Region())
        require((got^want).is_empty(),'ECO via GDS shape differs on '+str(info))
    def shapes(c):
        return Counter((str(c.layout().get_info(li)),sh.to_s()) for li in c.layout().layer_indexes() for sh in c.shapes(li).each())
    def instances(c):
        return Counter((i.cell.name,str(i.cplx_trans),i.na,i.nb,str(i.a),str(i.b)) for i in c.each_inst())
    for name in names_a:
        ca,cb=a.cell(name),b.cell(name)
        require(shapes(ca)==shapes(cb),'Existing cell geometry changed: '+name)
        old,new=instances(ca),instances(cb)
        if name=='croc_chip':
            require(new-old and not (old-new),'Existing top instance changed')
            added=new-old
            require(sum(added.values())==288 and all(k[0]==via_name for k in added),'Unexpected GDS instance additions')
            pts=Counter((i.cplx_trans.disp.x,i.cplx_trans.disp.y) for i in cb.each_inst() if i.cell.name==via_name)
            require(pts==Counter((r['x'],r['y']) for r in p['placements']),'GDS via coordinates differ')
            require(all(i.size()==1 and i.cplx_trans.angle==0 and not i.cplx_trans.is_mirror() and i.cplx_trans.mag==1
                        for i in cb.each_inst() if i.cell.name==via_name),'Unexpected via transform')
        else:require(old==new,'Existing child hierarchy changed: '+name)
    # Enclosures must be entirely within existing physical conductors.
    top=a.cell('croc_chip');require(top is not None,'Missing original GDS top')
    per_layer={}
    for layer in ('Metal4','TopMetal1'):
        li=a.find_layer(*LAYERS[layer]);require(li is not None,'Missing conductor layer')
        existing=db.Region(top.begin_shapes_rec(li))
        addition=db.Region()
        box=next(box for lname,box in via_boxes() if lname==layer)
        for r in p['placements']:addition.insert(db.Box(*moved(box,r['x'],r['y'])))
        require((addition-existing).is_empty(),'ECO metal extends beyond existing '+layer)
        per_layer[layer]={'new_metal_area_um2':0,'contained_enclosures':288}
    return {'status':'GDS_DELTA_VERIFIED','arrays':288,'cuts':2592,'new_via_cell':via_name,'existing_cells':len(names_a),'enclosures':per_layer}

if __name__=='__main__':
    import sys
    mode=sys.argv[1]
    if mode=='gds-audit':write_json(sys.argv[5],audit_gds_delta(sys.argv[2],sys.argv[3],json.loads(Path(sys.argv[4]).read_text())))
    elif mode=='supply-audit':write_json(sys.argv[3],audit_supply_database(sys.argv[2]))
    else:raise SystemExit('Unknown worker mode')
