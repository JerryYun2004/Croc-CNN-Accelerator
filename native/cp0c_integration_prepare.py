"""Prepare disposable full-chip inputs with an explicit annotation ledger."""
from collections import Counter
import hashlib,json,re
from pathlib import Path
from cp0c_integration_common import db,require,sha,write_json,fingerprint,cdl_cells
from cp0c_integration_context import hierarchy_audit
from cp0c_integration_sram import verify_marker_masters

def validate_context(report):
    c=report['counts']
    require(report['io_masters']==29 and c['unnamed_groups']==29 and
        c['duplicate_named_groups']==c['split_groups_with_parents']==
        c['split_groups_all_parent_instances_joined']==17,'Saved chip I/O boundary coverage differs')
    require(report['substrate_context']['endpoint']=='croc_chip.VSS' and
        report['substrate_context']['path_count']>0,'Substrate does not reach chip VSS')

def prepare_reference(source,annotations,pin,dest):
    text=Path(source).read_text();cells=cdl_cells(source);io=annotations['cells']
    ledger=[]
    for name,c in io.items():
        require(name in cells and c['pins']==cells[name]['pins']+['sub!'],
                'Source reference interface differs: '+name)
    top_names=[n for n in cells if n.lower()=='croc_chip'];require(len(top_names)==1,'Reference chip top missing')
    name=top_names[0];top=cells[name];counts=Counter();body=[]
    index={n.lower():n for n in cells};require(len(index)==len(cells),'Case-colliding reference definitions')
    reachable=set();todo=[name]
    while todo:
        current=todo.pop()
        if current in reachable:continue
        reachable.add(current)
        for line in cells[current]['body']:
            words=line.split()
            if words[0].upper().startswith('X'):
                target=index.get(words[-1].lower())
                require(target is not None,'Unknown reachable subcircuit call: '+current+'/'+line)
                todo.append(target)
    require({n for n in reachable if n.startswith('sg13cmos5l_')}==set(io),'Reachable I/O library coverage differs')
    removed=set(cells)-reachable
    require(len(top['pins'])==52 and 'VSS' in top['pins'],'Reference top interface differs')
    for line in top['body']:
        w=line.split();old=line
        if w[0].upper().startswith('X') and w[-1] in io:
            child=w[-1];slash=int(w[-2]=='/');connections=w[1:-1-slash]
            require(len(connections)==len(cells[child]['pins']),'Top I/O call arity differs')
            roles=dict(zip(cells[child]['pins'],connections))
            require(roles.get('vss')=='VSS' and roles.get('iovss')=='VSSIO',
                    'I/O supply call differs from reviewed chip')
            w.insert(len(w)-1-slash,'VSS');line=' '.join(w);counts[child]+=1
            ledger.append({'cell':name,'before':old,'after':line,'basis':'Physical substrate traces terminate at chip VSS'})
        body.append(line)
    require(dict(counts)==pin['top_io'],'Top I/O instance coverage differs')
    replacements={**io,name:{'pins':top['pins'],'body':body}};seen=set()
    pattern=re.compile(r'^\.SUBCKT\s+(\S+)[^\n]*(?:\n|$).*?^\.ENDS[^\n]*(?:\n|$)',re.M|re.S|re.I)
    def replace(m):
        n=m[1]
        if n in removed:return ''
        if n not in replacements:return m[0]
        require(n not in seen,'Duplicate reference subcircuit '+n);seen.add(n);v=replacements[n]
        if n in io:
            ledger.append({'cell':n,'old_pins':cells[n]['pins'],'new_pins':v['pins'],
                'old_body':cells[n]['body'],'new_body':v['body'],'origins':annotations['origins'][n]})
        return '.SUBCKT '+n+' '+' '.join(v['pins'])+'\n'+'\n'.join(v['body'])+'\n.ENDS '+n+'\n'
    result=pattern.sub(replace,text);require(seen==set(replacements),'Incomplete reference replacement')
    Path(dest).write_text(result);check=cdl_cells(dest)
    require(set(check)==reachable,'Reference elaboration coverage changed')
    for n in reachable:
        require(check[n]==(replacements[n] if n in replacements else cells[n]),'Unexpected reference edit '+n)
    return {'source_sha256':sha(source),'annotated_sha256':sha(dest),'top_io_instances':dict(counts),
        'edits':ledger,'non_io_circuit_bodies_unchanged':True,'chip_signal_connections_unchanged':True,
        'reachability_root':name,'reachable_circuits':sorted(reachable),'unused_library_definitions_removed':sorted(removed),
        'unused_removal_basis':'Transitive closure of every X call from croc_chip; no instantiated circuit is removed',
        'substrate_connection_basis':'Saved physical extraction: every traced I/O substrate endpoint is croc_chip.VSS',
        'scope':'Geometry-annotated diagnostic reference, not unmodified vendor CDL'}

def layout_fingerprints(layout,exclude=()):
    result={}
    for c in layout.each_cell():
        fp=fingerprint(layout,c,c.name in exclude)
        result[c.name]=hashlib.sha256(json.dumps(fp,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result

def prepare_gds(source,annotations,dest):
    l=db.Layout();l.read(str(source));require(l.dbu==.001,'Unexpected GDS units')
    require([c.name for c in l.top_cells()]==['croc_chip'],'Unexpected full-chip GDS top')
    require(not any(c.name.startswith(('SP6T','DP8T')) for c in l.each_cell()),
            'PDK implicit SRAM patterns would match this layout')
    for name,expected in annotations['physical_fingerprints'].items():
        c=l.cell(name);require(c is not None,'Missing physical I/O master '+name)
        actual=hashlib.sha256(json.dumps(fingerprint(l,c),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        require(actual==expected,'Pinned I/O geometry differs: '+name)
    names=('sg13cmos5l_SecondaryProtection','sg13cmos5l_RCClampResistor')
    before=layout_fingerprints(l,names);markers={}
    for name in names:
        c=l.cell(name);layer=l.layer(128,0)
        require(db.Region(c.shapes(layer)).is_empty(),'Unexpected existing diagnostic marker')
        def r(a):return db.Region(c.shapes(l.layer(a,0)))
        body=(r(5)&r(14)&r(28)&r(111)).merged();expected=db.Region()
        if name.endswith('SecondaryProtection'):expected.insert(db.Box(940,1370,1940,3370))
        else:
            for i in range(26):expected.insert(db.Box(1650*i,430,1650*i+1000,20430))
        require((body^expected).is_empty(),'Pinned resistor bodies differ')
        markers[name]=sorted(p.to_s() for p in body.each());c.shapes(layer).insert(body)
    require(layout_fingerprints(l,names)==before,'Preparing markers changed source masks')
    opts=db.SaveLayoutOptions();opts.write_context_info=False;l.write(str(dest),opts)
    q=db.Layout();q.read(str(dest));require(layout_fingerprints(q,names)==before,'GDS round trip changed masks/placement/text')
    for name,shapes in markers.items():
        require(sorted(p.to_s() for p in db.Region(q.cell(name).shapes(q.layer(128,0))).merged().each())==shapes,
                'Diagnostic marker round trip differs')
    return {'source_sha256':sha(source),'diagnostic_sha256':sha(dest),'source_cell_fingerprints':before,
        'only_changes':{'layer':[128,0],'cell_bodies':markers},'physical_rail_jumpers_added':0,
        'source_geometry_changed':False,'non_marker_geometry_and_placements_preserved':True,
        'warning':'This is an extraction copy. Do not use it as fabrication GDS.'}

def prepare(cfg,out):
    out=Path(out);pin=json.loads(Path(cfg['pins']).read_text())
    annotations=json.loads(Path(cfg['annotations']).read_text())
    print('AUDIT: saved chip I/O boundaries',flush=True)
    context=hierarchy_audit(cfg['database']);validate_context(context);write_json(out/'saved-boundaries.json',context)
    print('PREPARE: complete reference and extraction-only GDS',flush=True)
    report=prepare_reference(cfg['reference'],annotations,pin,out/'reference.cdl')
    write_json(out/'reference-annotations.json',report)
    layout=db.Layout();layout.read(cfg['sram_gds']);write_json(out/'sram-marker-masters.json',verify_marker_masters(layout))
    write_json(out/'gds-preservation.json',prepare_gds(cfg['gds'],annotations,out/'diagnostic.gds.gz'))
    print('CP0C_INTEGRATION_PREPARED',flush=True)
