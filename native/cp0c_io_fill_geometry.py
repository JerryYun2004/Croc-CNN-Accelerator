"""Expose physical filler/corner segments without joining equal supply labels."""
from cp0c_io_fill_support import *

TARGETS={k:{'cell':'sg13cmos5l_'+n,'top':'cp0c_'+n+'Boundary','width':w}
    for k,n,w in [('filler1000','Filler1000',5000),('filler400','Filler400',2000),
                  ('filler200','Filler200',1000),('corner','Corner',180000)]}
LAYERS={8:8,30:7,50:7,126:5}
CHAIN=(8,19,10,29,30,49,50,125,126)
TAP_PORTS=['m1_t2','m1_t3','m1_t4','m1_t6']
PORTS=TAP_PORTS+['sub!']
PREFIX={8:'m1',30:'m3',50:'m4',126:'tm1'}

def mask_digest(layout,cell):
    fp=fingerprint(layout,cell)
    for v in fp['layers'].values():v['texts']=[]
    fp['layers']={k:v for k,v in fp['layers'].items() if v['polygons']}
    return hashlib.sha256(json.dumps(fp,sort_keys=True).encode()).hexdigest()

def connectivity(layout,cell):
    l=db.Layout();l.dbu=layout.dbu;c=l.create_cell('AUDIT');c.copy_tree(cell);c.flatten(True)
    state=db.LayoutToNetlist(db.RecursiveShapeIterator(l,c,[]));state.threads=2
    layers={a:state.make_polygon_layer(l.layer(a,0),str(a)) for a in CHAIN}
    for layer in layers.values():state.connect(layer)
    for a,b in zip(CHAIN,CHAIN[1:]):state.connect(layers[a],layers[b])
    for a in LAYERS:state.connect(layers[a],state.make_text_layer(l.layer(a,25),str(a)+'text'))
    state.extract_netlist()
    require(not list(state.each_error()),'Physical connectivity extraction has errors')
    names={f'{PREFIX[a]}_t{i}' for a,count in LAYERS.items() for i in range(count)}
    groups=[]
    for net in state.netlist().top_circuit().each_net():
        labels=sorted(set(net.name.lower().split(','))&names)
        if labels:groups.append(labels)
    require(set(n for g in groups for n in g)==names,'Missing physical track labels')
    return {'groups':sorted(groups),'components':{n:sum(n in g for g in groups) for n in sorted(names)},
            'labelled_nets':len(groups),'total_nets':len(list(state.netlist().top_circuit().each_net()))}

def clone(layout,cell):
    l=db.Layout();l.dbu=layout.dbu;c=l.create_cell('CONTROL');c.copy_tree(cell);c.flatten(True)
    return l,c

def physical_controls(layout,cell,key,tracks,positive):
    # One geometric cross-section cuts every conducting layer and via. Each of
    # the 27 independent track labels must split into two physical components.
    width=TARGETS[key]['width'];x=175000 if key=='corner' else width//2
    l,c=clone(layout,cell);cut=db.Region(db.Box(x-50,-1000,x+50,181000))
    for a in CHAIN:
        i=l.layer(a,0);r=db.Region(c.shapes(i))-cut;c.shapes(i).clear();c.shapes(i).insert(r)
    opened=connectivity(l,c)
    require(all(n==2 for n in opened['components'].values()),'A physical track-open control escaped')
    bridges=[]
    # Every pair of adjacent tracks on each conductor layer must cause a new
    # connection. These are independent disposable masks, never baseline edits.
    for a in LAYERS:
        rows=[t for t in tracks if t['layer']==a]
        for left,right in zip(rows,rows[1:]):
            l,c=clone(layout,cell)
            p,q=left['points'][1],right['points'][1]
            require(p[0]==q[0],'Bridge endpoints have different abscissae')
            c.shapes(l.layer(a,0)).insert(db.Box(p[0]-30,min(p[1],q[1]),p[0]+30,max(p[1],q[1])))
            bad=connectivity(l,c)
            require(bad['groups']!=positive['groups'] and bad['labelled_nets']<positive['labelled_nets'],
                    'A physical track-short control escaped')
            bridges.append({'layer':a,'tracks':[left['name'],right['name']],'rejected':True})
    return {'status':'TRACK_CONTROLS_VERIFIED','opened':opened,'open_tracks_rejected':27,
            'adjacent_short_controls':bridges,'short_controls_rejected':len(bridges)}

def prepare(gds,reader_cdl,out,expected=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    source=db.Layout();source.read(str(gds));require(source.dbu==.001,'Unexpected GDS units')
    cells=cdl_cells(reader_cdl);result={}
    for key,t in TARGETS.items():
        dest=out/key;dest.mkdir();l=db.Layout();l.dbu=source.dbu
        src=source.cell(t['cell']);require(src is not None and not list(src.each_inst()),'Expected one leaf cell')
        c=l.create_cell(t['top']);c.copy_tree(src)
        before=mask_digest(source,src)
        # Keep every mask polygon unchanged. Replace only conductor-layer text
        # labels in this disposable copy with unique physical segment names.
        removed=[]
        for a in LAYERS:
            shapes=c.shapes(l.layer(a,25))
            require(all(s.is_text() for s in shapes.each()),'Unexpected polygon on pin-text layer')
            removed += [{'layer':a,'text':s.text.to_s()} for s in shapes.each()]
            shapes.clear()
        tracks=[]
        for a,count in LAYERS.items():
            polys=sorted(db.Region(c.shapes(l.layer(a,0))).merged().each(),key=lambda p:p.bbox().bottom)
            require(len(polys)==count,'Track inventory changed')
            for i,p in enumerate(polys):
                name=f'{PREFIX[a]}_t{i}';bb=p.bbox();x=179800 if key=='corner' else t['width']-100
                stripe=db.Region(p)&db.Region(db.Box(x-1,-1000,x+1,181000))
                require(stripe.count()==1,'Ambiguous track cross-section')
                y=(stripe.bbox().bottom+stripe.bbox().top)//2
                points=[(y,179800),(x,y)] if key=='corner' else [(100,y),(x,y)]
                require(all(p.inside(db.Point(*pt)) for pt in points),'Endpoint lies outside track')
                for pt in points:c.shapes(l.layer(a,25)).insert(db.Text(name,db.Trans(*pt)))
                tracks.append({'name':name,'layer':a,'bbox':bb.to_s(),'polygon':p.to_s(),'points':points})
        require(mask_digest(l,c)==before,'Source mask geometry changed')
        positive=connectivity(l,c)
        require(all(n==1 for n in positive['components'].values()),'Physical track is open')
        taps=tap_geometry(l,c);require(len(taps)==4,'Expected four physical tap regions')
        regions=sorted(db.Region(c.shapes(l.layer(8,0))).merged().each(),key=lambda p:p.bbox().bottom)
        assigned={}
        for tap in taps:
            # The unique overlapping Metal1 conductor identifies the tie port.
            body=db.Region(db.Polygon.from_s(tap['polygon'].split(' props=')[0]))
            hits=[i for i,p in enumerate(regions) if not (body&db.Region(p)).is_empty()]
            require(len(hits)==1,'Tap not uniquely covered by Metal1')
            name=f'm1_t{hits[0]}';require(name not in assigned,'Multiple tap bodies on one track')
            assigned[name]=tap
        require(set(assigned)==set(TAP_PORTS),'Tap-to-track mapping changed')
        if expected:
            e=expected[key]
            require(before==e['mask_sha256'],'Pinned mask differs')
            require(positive==e['physical_connectivity'],'Physical track partition differs')
            require(assigned==e['tap_geometry'],'Physical tap geometry differs')
        controls=physical_controls(l,c,key,tracks,positive)
        path=dest/'candidate.gds';l.write(str(path))
        check=db.Layout();check.read(str(path))
        require(mask_digest(check,check.top_cell())==before,'GDS round trip changed masks')
        require(connectivity(check,check.top_cell())==positive,'GDS round trip changed tracks')
        original=cells[t['cell']]
        require(original['pins']==['iovdd','iovss','vdd','vss'] and len(original['body'])==2,
                'Vendor filler/corner CDL changed')
        taps_by_supply={line.split()[1]:line for line in original['body']}
        require(set(taps_by_supply)=={'iovss','vss'} and
                all('MODEL=ptap1' in v and v.split()[2]=='sub!' for v in taps_by_supply.values()),
                'Unexpected vendor tap model or endpoints')
        lines=['* Diagnostic expanded physical boundary; vendor CDL remains unchanged.',
               '.SUBCKT '+t['top']+' '+' '.join(PORTS)]
        ledger=[]
        for i,name in enumerate(TAP_PORTS):
            supply='vss' if name=='m1_t6' else 'iovss';orig=taps_by_supply[supply]
            words=orig.split();words[0]='RCP0T_TAP'+str(i);words[1]=name
            line=' '.join(words);tap=assigned[name]
            line,n=re.subn(r'\bA=\S+',f"A={tap['A']}p",line);require(n==1,'Missing source tap area')
            line,n=re.subn(r'\bPerim=\S+',f"Perim={tap['P']}u",line);require(n==1,'Missing source tap perimeter')
            lines.append(line);ledger.append({'port':name,'source_supply':supply,'before':orig,'after':line})
        lines.append('.ENDS '+t['top']);ref=dest/'boundary.cdl';ref.write_text('\n'.join(lines)+'\n')
        report={'status':'IO_FILL_FIXTURE_PREPARED','mask_sha256':before,'source_masks_unchanged':True,
            'removed_labels':removed,'tracks':tracks,'physical_connectivity':positive,'physical_controls':controls,
            'tap_geometry':assigned,'reference_edits':ledger,'new_mask_shapes':0,'net_joins':0,
            'reference_scope':'four physical taps with separate Metal1 tie ports and a common substrate boundary',
            'implicit_connections':[],'blackboxes':[]}
        write_json(dest/'geometry.json',report)
        result[key]={**t,'candidate':str(path.resolve()),'references':{'boundary':str(ref.resolve())},
                     'device_ports':TAP_PORTS,'models':{'ptap1':4},'geometry':report}
    return result
