"""Geometry-grounded disposable power-pad harnesses and reference annotations."""
from cp0c_io_power_support import *

PREFIX='sg13cmos5l_'
PORTS=['iovdd','iovss','vdd','vss']
TARGETS={}
for key,pad in [('vdd','IOPadVdd'),('iovdd','IOPadIOVdd'),('vss','IOPadVss'),('iovss','IOPadIOVss')]:
    power=key.endswith('vdd')
    TARGETS[key]={'pad':PREFIX+pad,'top':'cp0c_'+pad+'Harness','ports':PORTS,
      'pass_through':'iovdd' if key=='vdd' else 'vdd',
      'device_ports':[p for p in PORTS if p!=('iovdd' if key=='vdd' else 'vdd')],
      'devices':33 if power else 4,
      'models':{'sg13_hv_nmos':3,'sg13_hv_pmos':1,'ptap1':2,'dantenna':1,'rppd':26} if power else
               {'ptap1':2,'dantenna':1,'dpantenna':1},
      'raw_models':{'sg13_hv_nmos':198,'sg13_hv_pmos':50,'ptap1':4,'dantenna':1,'rppd':26} if power else
                   {'ptap1':4,'dantenna':2,'dpantenna':2},
      'variants':['candidate','unmarked'] if power else ['candidate'],
      'contexts':['untranslated','unpropagated','unmarked'] if power else
                 (['untranslated','unpropagated'] if key=='vss' else ['untranslated'])}

JUMPERS={'iovss':[(-12000,19000,-10000,131000),(-12000,19000,1000,21000),
                   (-12000,49000,1000,51000),(-12000,129000,1000,131000)],
         'iovdd':[(90000,77500,92000,107250),(79000,77500,92000,79500),
                  (79000,105250,92000,107250)]}
TOUCHES={'iovss':['(0,126000;80000,134000)','(0,34500;80000,60000)','(0,7000;80000,32500)'],
         'iovdd':['(0,66000;80000,91500)','(0,93500;80000,119000)']}
UNSTRAPPED={'vdd':{'iovdd':2,'iovss':3,'vdd':1,'vss':1},
 'iovdd':{'iovdd':1,'iovss':3,'vdd':1,'vss':1},
 'vss':{'iovdd':1,'iovss':2,'vdd':1,'vss':1},
 'iovss':{'iovdd':1,'iovss':1,'vdd':1,'vss':1}}
TAPS={'DCPDiode':[(33.5104,197.12)],'DCNDiode':[(141.2964,221.76)],
 'Clamp_N43N43D4R':[(65.6472,386.16)],'RCClampInverter':[(69.122,406.6)],'RCClampResistor':[],
 'IOPadVdd':[(24.,160.6),(1997.6623,411.58)],'IOPadIOVdd':[(24.,160.6),(1997.9154,411.58)],
 'IOPadVss':[(24.,160.6),(5379.0466,677.04)],'IOPadIOVss':[(24.,160.6),(5379.0466,677.04)]}

def physical_devices(layout):
    """Independent count/dimensions, before reading any extracted netlist."""
    result={}
    for name,expected in [('Clamp_N43N43D4R',{(600,4400):172}),
                          ('RCClampInverter',{(500,7000):50,(500,9000):12,(9500,9000):14})]:
        c=layout.cell(PREFIX+name)
        gates=(db.Region(c.shapes(layout.layer(1,0)))&db.Region(c.shapes(layout.layer(5,0)))&
               db.Region(c.shapes(layout.layer(44,0)))).merged()
        rows=list(gates.each())
        require(all(p.area()==p.bbox().area() for p in rows),'Nonrectangular gate geometry')
        require(dict(Counter((p.bbox().width(),p.bbox().height()) for p in rows))==expected,
                'Power clamp finger geometry changed: '+name)
        result[name]=[{'width_dbu':a,'height_dbu':b,'count':n} for (a,b),n in sorted(expected.items())]
    c=layout.cell(PREFIX+'RCClampResistor')
    body=(db.Region(c.shapes(layout.layer(5,0)))&db.Region(c.shapes(layout.layer(14,0)))&
          db.Region(c.shapes(layout.layer(28,0)))&db.Region(c.shapes(layout.layer(111,0)))).merged()
    expected=db.Region()
    for i in range(26):expected.insert(db.Box(1650*i,430,1650*i+1000,20430))
    require((body^expected).is_empty(),'RC resistor bodies changed')
    require(db.Region(c.shapes(layout.layer(128,0))).is_empty(),'Unexpected existing PolyRes marker')
    result['RCClampResistor']={'count':26,'width_um':1,'length_um':20,'spacing_parameter_um':0,
        'bodies_dbu':sorted(p.bbox().to_s() for p in body.each())}
    return result,body

def metal_controls(layout,cell,key):
    positive=metal_audit(layout,cell,PORTS)
    require(positive['boundary_components']=={p:1 for p in PORTS},'Disconnected supply rail')
    def copy():
        l=db.Layout();l.dbu=layout.dbu;c=l.create_cell('CONTROL');c.copy_tree(cell);c.flatten(True);return l,c
    # A physical cut across the pad must split its otherwise device-free rail.
    l,c=copy();cut=db.Region(db.Box(39000,-1000,41000,181000))
    for a in (8,19,10,29,30,49,50,125,126):
        i=l.layer(a,0);remaining=db.Region(c.shapes(i))-cut;c.shapes(i).clear();c.shapes(i).insert(remaining)
    opened=metal_audit(l,c,PORTS)
    require(opened['boundary_components'][TARGETS[key]['pass_through']]>1,'Pass-through open control escaped')
    l,c=copy();c.shapes(l.layer(50,0)).insert(db.Box(1000,19750,3000,78750))
    try:metal_audit(l,c,PORTS)
    except RuntimeError as exc:require('shorted' in str(exc),'Unexpected bridge-control error')
    else:raise RuntimeError('Supply short control escaped')
    return {'status':'ALL_FOUR_RAILS_VERIFIED','positive':positive,'open_control':opened,
            'pass_through_open_rejected':True,'supply_bridge_rejected':True,
            'scope':'independent physical metal/via extraction; both edge labels and center label per rail'}

def prepare(gds,reader_cdl,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    source=db.Layout();source.read(str(gds));require(source.dbu==.001,'Unexpected GDS units')
    devices,body=physical_devices(source)
    cells=cdl_cells(reader_cdl);result={}
    for key,target in TARGETS.items():
        dest=out/key;dest.mkdir();pad=target['pad'];top=target['top']
        l=db.Layout();l.dbu=source.dbu;p=l.create_cell(pad);p.copy_tree(source.cell(pad))
        names=sorted(c.name for c in l.each_cell());before={n:fingerprint(l,l.cell(n)) for n in names}
        power=key.endswith('vdd')
        expected_names={pad}|{PREFIX+n for n in
           (('Clamp_N43N43D4R','RCClampInverter','RCClampResistor') if power else ('DCPDiode','DCNDiode'))}
        require(set(names)==expected_names,'Power pad hierarchy changed')
        measures={n:tap_geometry(l,l.cell(n)) for n in names};changes={}
        # IOPadIOVss's source CDL represents its two diode children directly at
        # parent level. Sum their measured tap regions into that parent's IOVSS
        # tap; the reference has no calls to those children. No nets are joined.
        referenced=[pad] if key=='iovss' else names
        for n,rows in measures.items():
            require([(r['A'],r['P']) for r in rows]==TAPS[n.removeprefix(PREFIX)],'Tap geometry changed: '+n)
            if n not in referenced:continue
            taps=[line.split() for line in cells[n]['body'] if 'MODEL=ptap1' in line]
            if n==pad:
                require(len(taps)==2 and {t[1] for t in taps}=={'vss','iovss'},'Pad tap reference changed')
                changes[n]={}
                for t in taps:
                    row=rows[0] if t[1]=='vss' else rows[1]
                    values={a:row[a] for a in ('A','P')}
                    if key=='iovss' and t[1]=='iovss':
                        for child in ('DCNDiode','DCPDiode'):
                            for r in measures[PREFIX+child]:
                                for a in values:values[a]+=r[a]
                    changes[n][t[0]]=tuple(round(values[a],8) for a in ('A','P'))
            elif rows:
                require(len(taps)==1,'Ambiguous child tap annotation: '+n)
                changes[n]={taps[0][0]:tuple(round(sum(r[a] for r in rows),8) for a in ('A','P'))}
            else:require(not taps,'Reference has unexplained tap');changes[n]={}
        c=l.create_cell(top);c.insert(db.CellInstArray(p.cell_index(),db.Trans()))
        # Power pads expose their supply pins on Metal4; their TopMetal1 shapes
        # differ from signal pads. All temporary jumpers therefore use Metal4.
        for name,y in [('iovdd',78750),('iovss',19750),('vdd',147900),('vss',170100)]:
            require(any(poly.inside(db.Point(40000,y)) for poly in db.Region(p.begin_shapes_rec(l.layer(50,0))).each()),'Port outside metal')
            for x in (1000,40000,79000):
                require(any(poly.inside(db.Point(x,y)) for poly in db.Region(p.begin_shapes_rec(l.layer(50,0))).each()),'Rail edge label outside metal')
                c.shapes(l.layer(50,25)).insert(db.Text(name,db.Trans(x,y)))
        audits={'unstrapped':metal_audit(l,c,PORTS)}
        require(audits['unstrapped']['boundary_components']==UNSTRAPPED[key],'Unstrapped rails changed')
        l.write(str(dest/'unstrapped.gds'))
        existing=db.Region(p.begin_shapes_rec(l.layer(50,0))).merged()
        for rail in ('iovss','iovdd'):
            jumper=db.Region();[jumper.insert(db.Box(*b)) for b in JUMPERS[rail]]
            require(sorted(q.bbox().to_s() for q in existing.interacting(jumper).each())==TOUCHES[rail],
                    'Jumper touches unexpected components: '+rail)
            c.shapes(l.layer(50,0)).insert(jumper)
            audits['after_'+rail]=metal_audit(l,c,PORTS)
        require(audits['after_iovdd']['boundary_components']=={n:1 for n in PORTS},'Rails remain split')
        joins=sum(v-1 for v in UNSTRAPPED[key].values())
        require(audits['unstrapped']['metal_nets']-audits['after_iovdd']['metal_nets']==joins,'Jumpers joined unrelated nets')
        l.write(str(dest/'unmarked.gds'))
        if power:l.cell(PREFIX+'RCClampResistor').shapes(l.layer(128,0)).insert(body)
        l.write(str(dest/'candidate.gds'));check=db.Layout();check.read(str(dest/'candidate.gds'))
        for n,old in before.items():require(fingerprint(check,check.cell(n),True)==old,'Source mask changed: '+n)
        require(fingerprint(check,check.cell(top))==fingerprint(l,c),'Wrapper round trip differs')
        require(metal_audit(check,check.cell(top),PORTS)==audits['after_iovdd'],'Marker changed metal connectivity')
        controls=metal_controls(check,check.cell(top),key)
        if power:require((db.Region(check.cell(PREFIX+'RCClampResistor').shapes(check.layer(128,0)))^body).is_empty(),'Marker round trip differs')
        refs={};ledger=[]
        dropped=PREFIX+('RCClampInverter' if power else 'DCNDiode')
        variants=['translated','untranslated']+(['unpropagated'] if key!='iovss' else [])
        for variant in variants:
            lines=['* Disposable power-pad harness reference; source CDL is unchanged.']
            for n in referenced:
                propagate=not(variant=='unpropagated' and n==dropped)
                lines.append('.SUBCKT '+n+' '+' '.join(cells[n]['pins']+(['sub!'] if propagate else [])))
                for original in cells[n]['body']:
                    line=original;words=line.split();device=words[0]
                    if device.upper().startswith('X'):
                        require(words[-2]=='/' and words[-1] in referenced,'Unexpected CDL call')
                        if not(variant=='unpropagated' and words[-1]==dropped):words.insert(-2,'sub!');line=' '.join(words)
                    if variant!='untranslated' and device in changes[n]:
                        a,per=changes[n][device]
                        line,k=re.subn(r'\bA=\S+','A='+str(a)+'p',line);require(k==1,'Missing tap area')
                        line,k=re.subn(r'\bPerim=\S+','Perim='+str(per)+'u',line);require(k==1,'Missing tap perimeter')
                    if variant!='untranslated' and n==PREFIX+'RCClampResistor' and device.startswith('RR'):
                        require(' ps=180n ' in line,'Resistor spacing changed');line=line.replace(' ps=180n ',' ps=0 ')
                    if line!=original:ledger.append({'variant':variant,'cell':n,'before':original,'after':line})
                    lines.append(line)
                lines.append('.ENDS '+n)
            require(cells[pad]['pins']==PORTS,'Pad interface changed')
            interface=PORTS+['sub!'];lines+=['.SUBCKT '+top+' '+' '.join(interface),'XPAD '+' '.join(interface)+' / '+pad,'.ENDS '+top]
            path=dest/(variant+'.cdl');path.write_text('\n'.join(lines)+'\n');refs[variant]=str(path.resolve())
        report={'status':'IO_POWER_FIXTURES_PREPARED','tap_geometry':measures,'annotations':changes,'reference_edits':ledger,
            'metal_audits':audits,'jumper_boxes_dbu':JUMPERS,'jumper_layer':[50,0],
            'metal_controls':controls,
            'source_fingerprints':before,'source_masks_preserved_except_resistor_markers':True,
            'physical_devices':devices if power else {},'reference_cells':referenced,
            'net_joins':0,'implicit_connections':[],'blackboxes':[]}
        write_json(dest/'geometry.json',report)
        result[key]={**target,'references':refs,'candidate':str((dest/'candidate.gds').resolve()),
            'unmarked':str((dest/'unmarked.gds').resolve()),'geometry':report}
    return result
