"""Read-only SRAM context audit and disposable, explicit reference translation."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

SRAM = 'RM_IHPSG13_1P_512x32_c2_bm_bist'
BITCELL = 'RM_IHPSG13_1P_BITKIT_CELL'
ARRAY_R = re.compile(r'RAM<[01]>\.COL<\d+>\.RAM<\d+>\.CELL<\d+>\.([012])')
ARRAY_M = re.compile(r'RAM<[01]>\.COL<\d+>\.RAM<\d+>\.CELL<\d+>\.([NP]\d+)')

def require(ok, message):
    if not ok: raise RuntimeError(message)

def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def write_json(path, data):
    Path(path).write_text(json.dumps(data,indent=2)+'\n')

def isolate(nl, name):
    n=nl.dup(); root=n.circuit_by_name(name)
    require(root is not None, 'Missing SRAM circuit: '+name)
    keep=set()
    def visit(c):
        if c.name in keep: return
        keep.add(c.name)
        for sc in c.each_subcircuit(): visit(sc.circuit_ref())
    visit(root)
    for c in list(n.each_circuit_top_down()):
        if c.name not in keep: n.remove(c)
    require(len(n.top_circuits())==1, 'SRAM isolation left multiple tops')
    return n

def summary(nl):
    c=nl.top_circuit()
    return {'top':c.name,'devices':dict(Counter(d.device_class().name for d in c.each_device())),
            'device_count':sum(1 for _ in c.each_device()),
            'subcircuits':sum(1 for _ in c.each_subcircuit()),
            'nets':sum(1 for _ in c.each_net()),'pins':sum(1 for _ in c.each_pin())}

def pins(c):
    ps=list(c.each_pin()); names=[p.name() for p in ps]
    require(len(names)==len(set(names)), 'Duplicate macro pin names')
    out={p.name():c.net_for_pin(p.id()) for p in ps}
    require(all(n is not None for n in out.values()), 'Disconnected macro pin')
    return out

def placements(layout, target):
    import klayout.db as db
    memo={}; output=[]
    def contains(cell):
        if cell.name==target: return True
        key=cell.cell_index()
        if key not in memo: memo[key]=any(contains(layout.cell(i)) for i in cell.each_child_cell())
        return memo[key]
    def visit(cell,tr):
        if cell.name==target: output.append(tr); return
        for inst in cell.each_inst():
            if contains(inst.cell):
                for t in inst.cell_inst.each_cplx_trans(): visit(inst.cell,tr*t)
    visit(layout.cell(SRAM),db.ICplxTrans())
    return output

def trace_context(saved, layout, isolated, output):
    """Match every bitcell MOS by source placement and native extracted location.

    Compare cluster IDs within the SAME macro circuit, never name-only supply
    equivalence. No net joins, label insertion, or geometry edits are performed.
    """
    import klayout.db as db
    macro=saved.netlist().circuit_by_name(SRAM)
    require(macro is not None,'Missing physical SRAM')
    pp=pins(macro); require(len(pp)==191,'Unexpected macro interface')
    supplies={n:pp[n].cluster_id for n in ('VSS!','VDD!','VDDARRAY!')}
    require(len(set(supplies.values()))==3,'Macro supplies are unexpectedly merged')
    leaf=isolated.netlist().top_circuit()
    require(leaf.name==BITCELL,'Wrong isolated bitcell')
    mos=[d for d in leaf.each_device() if d.device_class().name.startswith('sg13_')]
    require(len(mos)==6,'Expected six isolated MOS devices')
    index={}
    for d in macro.each_device():
        model=d.device_class().name
        if not model.startswith('sg13_'): continue
        key=(model,round(d.trans.disp.x/layout.dbu),round(d.trans.disp.y/layout.dbu))
        index.setdefault(key,[]).append(d)
    transforms=placements(layout,BITCELL)
    require(len(transforms)==16384,'Unexpected bitcell placement count')
    counts=Counter(); used=set(); failures=[]; samples=[]
    with (Path(output)/'bitcell-context.tsv').open('w') as f:
        f.write('cell\tplacement\tleaf_device\tmacro_device\tx_um\ty_um\tmodel\tbulk_cluster\tsupply_sd_cluster\tpass\n')
        for i,tr in enumerate(transforms):
            for d in mos:
                model=d.device_class().name
                point=tr*db.Point(round(d.trans.disp.x/layout.dbu),round(d.trans.disp.y/layout.dbu))
                hits=index.get((model,point.x,point.y),[])
                require(len(hits)==1,f'Expected one MOS at bitcell {i}, {model}, {point}; found {len(hits)}')
                actual=hits[0]
                require(actual.id() not in used,'Two bitcell sites mapped to one MOS')
                used.add(actual.id())
                nmos=model=='sg13_lv_nmos'
                supply='VSS!' if nmos else 'VDDARRAY!'
                bulk=actual.net_for_terminal('B')
                ok=bulk is not None and bulk.cluster_id==supplies[supply]
                ok=ok and all(abs(actual.parameter(p)-d.parameter(p))<1e-9 for p in ('L','W','rfmode'))
                # The two latch NMOS and both PMOS have a supply S/D terminal.
                # Extraction can swap MOS S/D, so inspect the unordered pair.
                supply_sd=not nmos or any(d.net_for_terminal(t).name=='VSS' for t in ('S','D'))
                sd=[actual.net_for_terminal(t) for t in ('S','D')]
                sd_ids=[n.cluster_id for n in sd if n is not None]
                if supply_sd:
                    ok=ok and sd_ids.count(supplies[supply])==1
                    counts['vss_branches' if nmos else 'vddarray_branches']+=1
                counts['nmos_bulk' if nmos else 'pmos_bulk']+=1
                row={'cell':i,'placement':str(tr),'leaf_device':d.expanded_name(),
                     'macro_device':actual.expanded_name(),'x_um':point.x*layout.dbu,'y_um':point.y*layout.dbu,
                     'model':model,'bulk_cluster':bulk.cluster_id if bulk else None,
                     'supply_sd_cluster':supplies[supply] if supply_sd else None,'pass':bool(ok)}
                f.write('\t'.join(str(v) for v in row.values())+'\n')
                if i<2: samples.append(row)
                if not ok: failures.append(row)
    expected={'nmos_bulk':65536,'pmos_bulk':32768,'vss_branches':32768,'vddarray_branches':32768}
    require(dict(counts)==expected,'Array terminal coverage differs')
    report={'status':'SRAM_ARRAY_CONTEXT_VERIFIED' if not failures else 'SRAM_ARRAY_CONTEXT_MISMATCH',
            'bitcells':len(transforms),'unique_mos':len(used),'checks':dict(counts),
            'supply_clusters':supplies,'failure_count':len(failures),'failures':failures[:50],
            'samples':samples,'trace_sha256':sha(Path(output)/'bitcell-context.tsv'),
            'net_joins':0,'geometry_changes':0,'basis':'Pinned GDS placements + saved native extraction device positions and cluster IDs'}
    write_json(Path(output)/'context.json',report)
    require(not failures,'Array-context connectivity check failed; inspect context.json')
    return report

def resistor_scope(name):
    m=ARRAY_R.fullmatch(name)
    if m: return ('array','res_metal3' if m[1]=='2' else 'res_metal2')
    if re.fullmatch(r'COLCTRL<\d+>\.(A_I8[789]|A_I9[01]|A_R2)\.0',name):return ('column','res_metal3')
    if re.fullmatch(r'A_ROWDEC\.SEL<\d+>\.(I11|R0)\.0',name):return ('decoder','res_metal2')
    if re.fullmatch(r'A_DLY[LH]\.IDM<[1-6]>\.0',name):return ('dummy','res_metal1')
    raise RuntimeError('Unclassified reference resistor: '+name)

def verify_marker_masters(layout):
    """Verify physical layer annotations for all four resistor-bearing masters."""
    import klayout.db as db
    expected={'RM_IHPSG13_1P_BITKIT_CELL':{(10,200,600):2,(30,200,600):1},
              'RM_IHPSG13_1P_DEC04':{(10,260,600):2},
              'RM_IHPSG13_1P_COLCTRL2':{(30,260,600):6},
              'RSC_IHPSG13_CDLYX1_DUMMY':{(8,260,600):1}}
    rows={}
    for name,want in expected.items():
        c=layout.cell(name); require(c is not None,'Missing resistor-bearing master '+name)
        found=Counter()
        for layer in (8,10,30):
            metal=db.Region(c.begin_shapes_rec(layout.layer(layer,0)))
            marker=db.Region(c.begin_shapes_rec(layout.layer(layer,29)))
            core=(metal&marker).merged()
            for p in core.each():
                box=p.bbox(); require(p.area()==box.area(),'Nonrectangular resistor core')
                w,l=sorted((box.width(),box.height())); found[(layer,w,l)]+=1
        require(found==want,'Physical resistor master differs: '+name)
        rows[name]={str(k):v for k,v in found.items()}
    return rows

def annotate_reference(nl, translated, output=None):
    """Add layer models to legacy cards; change W only for bitcell descendants.

    The native extraction has already combined series array resistors. Keep
    their extracted reference L values (0.6 or 1.2 um) and connectivity intact.
    """
    import klayout.db as db
    c=nl.top_circuit(); classes={}; counts=Counter(); width_changes=0; rows=[]
    for model in ('res_metal1','res_metal2','res_metal3'):
        dc=db.DeviceClassResistor();dc.name=model;nl.add(dc);classes[model]=dc
    for d in list(c.each_device()):
        if d.device_class().name.upper()!='LVSRES':continue
        name=d.expanded_name(); scope,model=resistor_scope(name)
        params={p.name:d.parameter(p.id()) for p in d.device_class().parameter_definitions()}
        require(abs(params['W']-.26)<1e-9,'Unexpected reference resistor W: '+name)
        require(any(abs(params['L']-v)<1e-9 for v in ((.6,1.2) if scope=='array' else (.6,))),
                'Unexpected reference resistor L: '+name)
        new_w=.2 if scope=='array' and translated else params['W']
        nd=c.create_device(classes[model],d.name)
        for t in ('A','B'): nd.connect_terminal(t,d.net_for_terminal(t))
        for p,v in params.items():nd.set_parameter(p,new_w if p=='W' else v)
        nd.trans=d.trans
        rows.append((name,scope,model,params['L'],params['W'],new_w))
        width_changes+=int(new_w!=params['W']);counts[scope]+=1
        c.remove_device(d)
    require(dict(counts)=={'array':32896,'column':192,'decoder':16,'dummy':12},'Reference translation scope differs')
    require(width_changes==(32896 if translated else 0),'Unexpected width edit count')
    if output:
        p=Path(output);p.write_text('device\tscope\tmodel\tlength_um\told_width_um\tnew_width_um\n'+
                                   ''.join('\t'.join(map(str,r))+'\n' for r in sorted(rows)))
    return {'layer_annotations':dict(counts),'array_width_changes':width_changes,
            'peripheral_width_changes':0,'terminal_changes':0,'length_changes':0,
            'source_reference_modified':False}

def active_classes(c):
    groups={}
    for d in c.each_device():
        dc=d.device_class(); require(dc is not None,'Device without class')
        groups.setdefault(dc.name.lower(),{})[dc.id()]=dc
    return {k:list(v.values()) for k,v in groups.items()}

def comparator(a,b):
    import klayout.db as db
    ca,cb=a.top_circuit(),b.top_circuit();pa,pb=pins(ca),pins(cb)
    require(set(pa)==set(pb) and len(pa)==191,'Macro interface differs')
    cmp=db.NetlistComparer();cmp.max_depth=20;cmp.max_branch_complexity=256
    # Auto-generated internal names differ between extraction and CDL. All
    # external pin names remain strictly constrained below.
    cmp.dont_consider_net_names=True
    cmp.same_circuits(ca,cb)
    ac,bc=active_classes(ca),active_classes(cb)
    require(set(ac)==set(bc),'Active model sets differ')
    for model,aa in ac.items():
        for x in aa:
            for y in bc[model]:cmp.same_device_classes(x,y)
    for nl in (a,b):
        for dc in nl.each_device_class():
            if dc.name.lower().startswith('res_metal'):
                dc.enable_parameter('R',False);dc.enable_parameter('L',True);dc.enable_parameter('W',True)
            elif dc.name.startswith('sg13_lv_'):
                for p in ('L','W','rfmode'):dc.enable_parameter(p,True)
    for name in pa:cmp.same_nets(pa[name],pb[name],True)
    return cmp

def reference_array_bulk_audit(nl):
    counts=Counter(); failures=[]
    for d in nl.top_circuit().each_device():
        m=ARRAY_M.fullmatch(d.expanded_name())
        if not m:continue
        expected='VSS!' if m[1].startswith('N') else 'VDDARRAY!'
        net=d.net_for_terminal('B')
        counts[expected]+=1
        if net is None or net.expanded_name()!=expected:failures.append(d.expanded_name())
    require(dict(counts)=={'VSS!':65536,'VDDARRAY!':32768},'Reference array MOS coverage differs')
    require(not failures,'Reference bulk connectivity differs')
    return {'bulk_connections':dict(counts),'failures':failures}

def graph_data(nl):
    """Lossless flat graph serialization, including unusual KLayout net names."""
    c=nl.top_circuit(); nets=[n.expanded_name() for n in c.each_net()]
    require(len(nets)==len(set(nets)), 'Duplicate expanded net names in flat macro')
    require(not list(c.each_subcircuit()), 'Graph serialization requires a flat circuit')
    devices=[]
    for d in c.each_device():
        dc=d.device_class()
        devices.append([d.expanded_name(),dc.name,
            sorted((p.name,d.parameter(p.id())) for p in dc.parameter_definitions()),
            sorted((t.name,d.net_for_terminal(t.id()).expanded_name()) for t in dc.terminal_definitions())])
    return {'schema':'CP0C-flat-graph-1','top':c.name,'nets':sorted(nets),
            'pins':[(p.name(),c.net_for_pin(p.id()).expanded_name()) for p in c.each_pin()],
            'devices':sorted(devices)}

def graph_digest(nl):
    return hashlib.sha256(json.dumps(graph_data(nl),separators=(',',':')).encode()).hexdigest()

def read_graph(path):
    """Restore five explicitly supported models without parsing SPICE names."""
    import klayout.db as db
    data=json.loads(Path(path).read_text())
    require(data['schema']=='CP0C-flat-graph-1','Unknown graph schema')
    n=db.Netlist();classes={}
    for name in ('sg13_lv_nmos','sg13_lv_pmos','res_metal1','res_metal2','res_metal3'):
        if name.startswith('sg13_'):
            dc=db.DeviceClassMOS4Transistor()
            dc.add_parameter(db.DeviceParameterDefinition('rfmode','rf mode',0,True))
        else:dc=db.DeviceClassResistor()
        dc.name=name;n.add(dc);classes[name]=dc
    c=db.Circuit();c.name=data['top'];n.add(c)
    nets={name:c.create_net(name) for name in data['nets']}
    for name,net in data['pins']:c.connect_pin(c.create_pin(name),nets[net])
    for name,model,params,terminals in data['devices']:
        require(model in classes,'Unsupported serialized device class '+model)
        d=c.create_device(classes[model],name)
        for p,v in params:d.set_parameter(p,v)
        for t,net in terminals:d.connect_terminal(t,nets[net])
    return n

def save_graph(nl,path):
    data=graph_data(nl);text=json.dumps(data,separators=(',',':'))
    expected=hashlib.sha256(text.encode()).hexdigest()
    Path(path).write_text(text)
    restored=read_graph(path)
    require(graph_digest(restored)==expected,'Saved graph changed connectivity or parameters')
    return restored,expected

def structural_hints(a,b,max_rounds=24):
    """Exact, name-independent neighborhood refinement for strict net hints.

    Only pins, model/geometry and connectivity participate. MOS S/D and
    resistor A/B are symmetric. Unique matching colors supply strict hints;
    they never join nets or replace the native comparison verdict.
    """
    graphs=[];intern={}
    def color(key):
        if key not in intern:intern[key]=len(intern)
        return intern[key]
    for nl in (a,b):
        c=nl.top_circuit();nets=list(c.each_net());indices={n.expanded_name():i for i,n in enumerate(nets)}
        require(len(indices)==len(nets),'Ambiguous net names for refinement')
        port_names={}
        for p in c.each_pin():port_names.setdefault(c.net_for_pin(p.id()).expanded_name(),[]).append(p.name())
        colors=[color(('net',tuple(sorted(port_names.get(n.expanded_name(),[]))))) for n in nets]
        adjacency=[[] for n in nets]
        for d in c.each_device():
            model=d.device_class().name.lower();mos=model.startswith('sg13_')
            params=(round(d.parameter('L'),9),round(d.parameter('W'),9))
            if mos:params+= (d.parameter('rfmode'),)
            i=len(colors);colors.append(color(('device',model,params)));adjacency.append([])
            for t in d.device_class().terminal_definitions():
                label=('SD' if t.name in ('S','D') else t.name) if mos else 'AB'
                j=indices[d.net_for_terminal(t.id()).expanded_name()]
                adjacency[i].append((label,j));adjacency[j].append((label,i))
        graphs.append({'nets':nets,'colors':colors,'adjacency':adjacency})
    rounds=[];first_difference=None
    for iteration in range(max_rounds):
        intern={};updated=[]
        for g in graphs:
            old=g['colors'];updated.append([color((old[i],tuple(sorted((edge,old[j]) for edge,j in adj))))
                                           for i,adj in enumerate(g['adjacency'])])
        counts=[Counter(x) for x in updated]
        different=counts[0]!=counts[1]
        if different and first_difference is None:first_difference=iteration+1
        old_count=len(set(graphs[0]['colors'])|set(graphs[1]['colors']))
        new_count=len(intern)
        for g,cs in zip(graphs,updated):g['colors']=cs
        rounds.append({'round':iteration+1,'colors':new_count,'histograms_equal':not different})
        if new_count==old_count:break
    groups=[]
    for g in graphs:
        rows={}
        for net,cl in zip(g['nets'],g['colors']):rows.setdefault(cl,[]).append(net)
        groups.append(rows)
    hints=[(aa[0],groups[1][cl][0]) for cl,aa in groups[0].items()
           if len(aa)==1 and cl in groups[1] and len(groups[1][cl])==1]
    return hints,{'rounds':rounds,'first_histogram_difference':first_difference,
                  'strict_unique_net_hints':len(hints),'net_joins':0,
                  'basis':'Matching unique pin/model/parameter/connectivity neighborhoods; native comparison still required'}

def dangling_gate_hints(a,b):
    """Resolve indistinguishable edge dummy MOS without ignoring any device.

    Each candidate is exactly one NMOS whose gate net has one terminal and no
    port, while S/D/B all attach to the strictly paired VSS macro pin. A group
    is paired only when model, L/W/rfmode and multiplicity agree on both sides.
    Every proposed pair remains a STRICT constraint in the native comparison.
    """
    groups=[]
    for nl in (a,b):
        c=nl.top_circuit();pp=pins(c);vss=pp['VSS!'].expanded_name()
        portnets={n.expanded_name() for n in pp.values()};found={}
        for d in c.each_device():
            if d.device_class().name.lower()!='sg13_lv_nmos':continue
            if any(d.net_for_terminal(t).expanded_name()!=vss for t in ('S','D','B')):continue
            gate=d.net_for_terminal('G')
            if gate.expanded_name() in portnets or len(list(gate.each_terminal()))!=1:continue
            key=(d.device_class().name.lower(),round(d.parameter('L'),9),round(d.parameter('W'),9),d.parameter('rfmode'))
            found.setdefault(key,[]).append((d.expanded_name(),gate))
        groups.append(found)
    hints=[];report=[]
    for key in sorted(groups[0].keys()|groups[1].keys()):
        aa=sorted(groups[0].get(key,[]),key=lambda row:row[0]);bb=sorted(groups[1].get(key,[]),key=lambda row:row[0])
        equal=len(aa)==len(bb)
        report.append({'model_and_parameters':key,'layout_count':len(aa),'reference_count':len(bb),'paired':equal})
        if equal:hints.extend((x[1],y[1]) for x,y in zip(aa,bb))
    return hints,{'strict_gate_net_hints':len(hints),'groups':report,'devices_ignored':0,'net_joins':0,
                  'basis':'Identical one-device floating-gate branches with S/D/B on strictly paired VSS'}
