"""Strict GDS assembly and streaming DRC report summaries."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

# IHP issue 410 confirms these exact cells are intentional empty placeholders.
# Approve them only from the reviewed, byte-pinned 512x32 SRAM GDS.
SRAM_TOP='RM_IHPSG13_1P_512x32_c2_bm_bist'
SRAM_GDS_SHA256='04acb39cd6b783e89a6a85237c356e7e425cfd6926e84a006c4dad8f6f743906'
SRAM_EMPTY_CELLS=frozenset('RM_IHPSG13_1P_BITKIT_16x2_'+suffix for suffix in (
    'CORNER','EDGE_TB','LE_con_corner','LE_con_edge_lr','LE_con_tap_lr',
    'POWER_ramtap','TAP','TAP_LR'))

def require(condition,message):
    if not condition: raise RuntimeError(message)

def sha256(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def parse_def(path):
    s=Path(path).read_text()
    name=re.search(r'\bDESIGN\s+(\S+)\s*;',s)
    unit=re.search(r'\bUNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;',s)
    area=re.search(r'\bDIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*;',s)
    block=re.search(r'\bCOMPONENTS\s+(\d+)\s*;(.*?)\bEND COMPONENTS\b',s,re.S)
    require(all((name,unit,area,block)),'Incomplete DEF header or component section.')
    components=re.findall(r'^\s*-\s+(\S+)\s+(\S+)',block[2],re.M)
    require(len(components)==int(block[1]),'DEF component count does not match its header.')
    require(len({x[0] for x in components})==len(components),'Duplicate DEF instance names.')
    return {'top':name[1],'units':int(unit[1]),'die':[int(x)/int(unit[1]) for x in area.groups()],
            'components':len(components),'masters':dict(Counter(x[1] for x in components))}

def instance_signature(top,masters):
    rows=[]
    for i in top.each_inst():
        if i.cell.name in masters:
            rows.append((i.cell.name,str(i.cplx_trans),i.na,i.nb,str(i.a),str(i.b)))
    return sorted(rows)

def counts(top,masters):
    result=Counter()
    for i in top.each_inst():
        if i.cell.name in masters: result[i.cell.name]+=i.size()
    return dict(result)

def polygon_present(cell):
    """Exclude text-only or empty cells from physical coverage."""
    for li in cell.layout().layer_indexes():
        it=cell.begin_shapes_rec(li)
        while not it.at_end():
            s=it.shape()
            if s.is_box() or s.is_polygon() or s.is_simple_polygon() or s.is_path(): return True
            it.next()
    return False

def empty_reference_signature(layout,names):
    rows=[]
    for cell in layout.each_cell():
        for inst in cell.each_inst():
            if inst.cell.name in names:
                rows.append([cell.name,inst.cell.name,str(inst.dcplx_trans),
                             inst.na,inst.nb,str(inst.da),str(inst.db)])
    return sorted(rows)

def sram_empty_reference(path):
    import klayout.db as db
    require(sha256(path)==SRAM_GDS_SHA256,'SRAM placeholder reference GDS checksum differs.')
    lib=db.Layout();lib.read(str(path))
    require([c.name for c in lib.top_cells()]==[SRAM_TOP],'SRAM reference top differs.')
    names=sorted(c.name for c in lib.each_cell() if c.is_empty())
    require(set(names)==SRAM_EMPTY_CELLS,'Pinned SRAM empty-cell set differs.')
    require(polygon_present(lib.cell(SRAM_TOP)),'SRAM master lacks physical geometry.')
    return {'source_file':str(path),'source_sha256':SRAM_GDS_SHA256,
            'source_issue':'https://github.com/IHP-GmbH/IHP-Open-PDK/issues/410',
            'cells':names,'references':empty_reference_signature(lib,names)}

def audit_empty_cells(layout,reference=None):
    names=sorted(c.name for c in layout.each_cell() if c.is_empty())
    expected=set(reference['cells']) if reference else set()
    require(set(names)==expected,
            f'Empty-cell set differs: unexpected={sorted(set(names)-expected)}, '
            f'missing upstream placeholders={sorted(expected-set(names))}')
    if reference:
        require(empty_reference_signature(layout,expected)==reference['references'],
                'SRAM placeholder parent references or placements changed.')
    return names

def assemble(defpath,lefs,gdss,layer_map,out,report):
    import klayout.db as db
    meta=parse_def(defpath);masters=meta['masters']; require(meta['top']=='croc_chip','Unexpected DEF top.')
    opt=db.LoadLayoutOptions()
    opt.lefdef_config.map_file=str(layer_map)
    opt.lefdef_config.lef_files=[str(p) for p in lefs]
    layout=db.Layout();layout.read(str(defpath),opt)
    top=layout.cell(meta['top']);require(top is not None,'DEF top missing after read.')
    require(counts(top,masters)==masters,'KLayout DEF component coverage differs.')
    before=instance_signature(top,masters)
    root_geometry={str(layout.get_info(li)):top.shapes(li).size() for li in layout.layer_indexes() if top.shapes(li).size()}
    # Check libraries independently, before clearing LEF abstracts.
    found={}; libraries=[]; empty_reference=None
    for gds in gdss:
        lib=db.Layout();lib.read(str(gds))
        require(lib.cell(meta['top']) is None,'Library collides with design top.')
        digest=sha256(gds)
        if digest==SRAM_GDS_SHA256 and SRAM_TOP in masters:
            require(empty_reference is None,'Duplicate SRAM placeholder reference library.')
            empty_reference=sram_empty_reference(gds)
        elif any(lib.cell(n) is not None for n in SRAM_EMPTY_CELLS):
            raise RuntimeError('SRAM placeholder names found outside the pinned SRAM library.')
        supplied=[]
        for name in masters:
            c=lib.cell(name)
            if c is not None and polygon_present(c):
                require(name not in found,f'Master {name} supplied by multiple libraries.')
                found[name]=str(gds);supplied.append(name)
        libraries.append({'file':str(gds),'sha256':digest,'masters':supplied})
    missing=sorted(set(masters)-set(found))
    require(not missing,f'Missing physical GDS masters: {missing}')
    for c in layout.each_cell():
        if c.cell_index()!=top.cell_index() and not c.name.startswith('VIA_'): c.clear()
    for gds in gdss: layout.read(str(gds))
    require(instance_signature(top,masters)==before,'GDS merge changed component placements.')
    require(root_geometry=={str(layout.get_info(li)):top.shapes(li).size() for li in layout.layer_indexes() if top.shapes(li).size()},
            'GDS merge changed top-level routing shape counts.')
    final=db.Layout();final.dbu=layout.dbu
    finaltop=final.create_cell(meta['top']);finaltop.copy_tree(top)
    empty=audit_empty_cells(final,empty_reference)
    if empty: print(f'UPSTREAM_SRAM_PLACEHOLDERS_VERIFIED: {len(empty)} exact cells; source hash and references match.',flush=True)
    opts=db.SaveLayoutOptions();opts.write_context_info=False
    final.write(str(out),opts)
    check=db.Layout();check.read(str(out));ct=check.cell(meta['top'])
    require([c.name for c in check.top_cells()]==[meta['top']],'Unexpected GDS top cells.')
    require(counts(ct,masters)==masters,'GDS round trip changed component counts.')
    require(instance_signature(ct,masters)==before,'GDS round trip changed component transforms.')
    audit_empty_cells(check,empty_reference)
    meta.update(gds_sha256=sha256(out),gds_dbu=check.dbu,cell_count=check.cells(),
                libraries=libraries,empty_cells=empty,upstream_empty_reference=empty_reference,
                component_placements_preserved=True,
                top_routing_shape_counts_preserved=True)
    Path(report).write_text(json.dumps(meta,indent=2)+'\n')
    return meta

def merge_seal(chip_path,seal_path,out,report,sram_reference=None):
    import klayout.db as db
    layout=db.Layout();layout.read(str(chip_path));chip=layout.cell('croc_chip')
    require(chip is not None,'Chip top missing.')
    empty_reference=sram_empty_reference(sram_reference) if sram_reference else None
    audit_empty_cells(layout,empty_reference)
    require(layout.cell('sealring_top') is None,'Chip collides with seal top.')
    seal=db.Layout();seal.read(str(seal_path))
    require([x.name for x in seal.top_cells()]==['sealring_top'],'Unexpected seal top cells.')
    common={x.name for x in layout.each_cell()} & {x.name for x in seal.each_cell()}
    require(not common,f'Seal/chip cell-name collisions: {common}')
    layout.read(str(seal_path));ring=layout.cell('sealring_top')
    top=layout.create_cell('croc_chip_sealed');shift=round(42/layout.dbu)
    top.insert(db.CellInstArray(chip.cell_index(),db.Trans(shift,shift)))
    top.insert(db.CellInstArray(ring.cell_index(),db.Trans()))
    require([x.name for x in layout.top_cells()]==['croc_chip_sealed'],'Unexpected sealed top cells.')
    b=top.dbbox();bbox=[b.left,b.bottom,b.right,b.top]
    require(all(abs(a-b)<.002 for a,b in zip(bbox,[0,0,2000,2000])),f'Sealed dimensions differ: {bbox}')
    opts=db.SaveLayoutOptions();opts.write_context_info=False;layout.write(str(out),opts)
    verify=db.Layout();verify.read(str(out))
    require([x.name for x in verify.top_cells()]==['croc_chip_sealed'],'Sealed GDS round-trip top differs.')
    empty=audit_empty_cells(verify,empty_reference)
    layers=[]
    for li in verify.layer_indexes():
        info=verify.get_info(li)
        if not verify.cell('croc_chip_sealed').begin_shapes_rec(li).at_end(): layers.append(str(info))
    result={'top':'croc_chip_sealed','bbox_um':bbox,'area_mm2':4.0,'chip_offset_um':[42,42],
            'cell_count':verify.cells(),'layers_present':layers,'sha256':sha256(out),
            'empty_cells':empty,'upstream_empty_reference':empty_reference}
    Path(report).write_text(json.dumps(result,indent=2)+'\n');return result

def summarize_rdb(path,table_path,samples_path):
    """Count all markers, including waived/visited markers; retain bounded samples."""
    counts=Counter(); samples=[]; categories=set();top=None;root_name=None
    for event,e in ET.iterparse(path,events=('start','end')):
        if root_name is None and event=='start': root_name=e.tag
        if event!='end': continue
        if e.tag=='top-cell': top=e.text
        elif e.tag=='category' and e.find('name') is not None:
            categories.add(e.findtext('name'));e.clear()
        elif e.tag=='item':
            cat=e.findtext('category');require(cat is not None,'RDB marker without category.')
            counts[cat]+=1
            if len(samples)<200:
                samples.append({'category':cat,'cell':e.findtext('cell'),
                    'visited':e.findtext('visited'),'values':[v.text for v in e.findall('./values/value')]})
            e.clear()
    require(root_name=='report-database','Invalid KLayout results database.')
    require(top=='croc_chip_sealed',f'DRC report top differs: {top}')
    required={'V1.c','V2.c','V3.c','TV1.c','TV1.d','Cnt.c','CntB.h1'}
    require(required<=categories,f'Enclosure-check categories missing: {sorted(required-categories)}')
    Path(table_path).write_text('category\tmarkers\n'+''.join(f'{c}\t{n}\n' for c,n in counts.most_common()))
    Path(samples_path).write_text(json.dumps(samples,indent=2)+'\n')
    return {'markers':sum(counts.values()),'violating_categories':len(counts),
            'registered_categories':len(categories),'required_enclosure_categories':sorted(required),
            'top':top,'top_findings':counts.most_common(15)}
