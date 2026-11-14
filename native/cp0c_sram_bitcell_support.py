"""Prepare isolated, geometry-preserving SRAM library experiments."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

BITCELL = 'RM_IHPSG13_1P_BITKIT_CELL'
CDL_CELL = 'RM_IHPSG13_512x32_c2_1P_BITKIT_CELL'
PINS = 'BLC_BOT BLC_TOP BLT_BOT BLT_TOP LWL NW PW RWL VDD VSS'.split()
RESISTORS = {
    'R0': ('BLT_BOT', 'BLT_TOP', 'res_metal2'),
    'R1': ('BLC_BOT', 'BLC_TOP', 'res_metal2'),
    'R2': ('RWL', 'LWL', 'res_metal3'),
}
VARIANTS = ('original', 'model_only', 'translated', 'wrong_width',
            'wrong_length', 'wrong_layer', 'missing', 'short', 'open')

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()

def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')

def cell_cards(path):
    text = re.sub(r'\n[ \t]*\+', ' ', Path(path).read_text())
    blocks = re.findall(r'^\.SUBCKT\s+(\S+)\s+([^\n]*)\n(.*?)^\.ENDS[^\n]*$',
                        text, flags=re.M | re.S | re.I)
    found = [(pins.split(), [s.strip() for s in body.splitlines()
              if s.strip() and not s.lstrip().startswith('*')])
             for name, pins, body in blocks if name.upper() == CDL_CELL.upper()]
    require(len(found) == 1, 'Expected exactly one reviewed bitcell CDL definition')
    pins, cards = found[0]
    require(pins == PINS and len(cards) == 9, 'Bitcell interface or device count changed')
    require(Counter(s[0].upper() for s in cards) == {'M': 6, 'R': 3}, 'Unexpected bitcell devices')
    for name, (a, b, _) in RESISTORS.items():
        rows = [s for s in cards if s.split()[0] == name]
        require(rows == [f'{name} {a} {b} lvsres w=2.6e-07 l=6e-07'],
                f'Unexpected pinned resistor card: {name}')
    return cards

def variants(top, pins, cards, mappings, directory):
    """Only R cards change; MOS cards and reference port order stay exact."""
    out = Path(directory); out.mkdir(parents=True, exist_ok=True)
    changes = []
    for variant in VARIANTS:
        body = []
        for card in cards:
            fields = card.split(); name = fields[0]
            if name not in mappings or variant == 'original':
                body.append(card); continue
            a, b, model = mappings[name]
            width = '2.6e-07' if variant == 'model_only' else '2e-07'
            length = '6e-07'
            if name == 'R0':
                if variant == 'wrong_width': width = '2.1e-07'
                if variant == 'wrong_length': length = '6.1e-07'
                if variant == 'wrong_layer': model = 'res_metal3' if model == 'res_metal2' else 'res_metal2'
                if variant == 'missing': continue
                if variant == 'short': b = a
                if variant == 'open': b = 'CP0B_OPEN'
            body.append(f'{name} {a} {b} {model} w={width} l={length}')
        path = out/(variant+'.cdl')
        path.write_text('* Isolated diagnostic reference; not a promoted library.\n'
                        +'.SUBCKT '+top+' '+' '.join(pins)+'\n'
                        +'\n'.join(body)+'\n.ENDS '+top+'\n')
        changes.append({'variant': variant, 'path': str(path.resolve()),
                        'sha256': sha(path), 'original_cards': cards, 'cards': body})
    return changes

def flattened_copy(source, name, new_name=None):
    import klayout.db as db
    c = source.cell(name)
    require(c is not None and not c.is_empty(), f'Missing physical cell: {name}')
    ly = db.Layout(); ly.dbu = source.dbu
    top = ly.create_cell(new_name or name); top.copy_tree(c); top.flatten(True)
    # The vendor GDS has coincident duplicate shapes. Canonical union preserves
    # geometry while keeping this one-cell fixture small.
    for idx in list(ly.layer_indexes()):
        shapes = top.shapes(idx)
        texts = {s.text.to_s(): s.text.dup() for s in shapes.each() if s.is_text()}
        region = db.Region(shapes).merged()
        shapes.clear(); shapes.insert(region)
        for key in sorted(texts): shapes.insert(texts[key])
    return ly, top

def fingerprint(ly, cell):
    import klayout.db as db
    result = {}
    for idx in ly.layer_indexes():
        reg = db.Region(cell.begin_shapes_rec(idx)).merged()
        texts = set(); it = cell.begin_shapes_rec(idx)
        while not it.at_end():
            if it.shape().is_text(): texts.add(it.shape().text.transformed(it.trans()).to_s())
            it.next()
        polys = sorted(p.to_s() for p in reg.each())
        if polys or texts:
            result[ly.get_info(idx).to_s()] = {'polygons': polys, 'texts': sorted(texts)}
    return result

def prepare(gds, cdl, out):
    import klayout.db as db
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    source = db.Layout(); source.read(str(gds))
    require(source.dbu == 0.001, 'Unexpected source GDS grid')
    fixtures = []; geometry = {}
    for tag, cell, layer in [('metal2', 'lvsres_db_0x0283509a', 10),
                              ('metal3', 'lvsres_db_0x02834b9a', 30),
                              ('bitcell', BITCELL, None)]:
        topname = BITCELL if layer is None else 'CP0B_'+tag.upper()
        ly, top = flattened_copy(source, cell, topname)
        original = fingerprint(source, source.cell(cell))
        require(fingerprint(ly, top) == original, 'Isolating the cell changed geometry or labels')
        additions = []
        if layer:
            core = (db.Region(top.shapes(ly.layer(layer,0))) &
                    db.Region(top.shapes(ly.layer(layer,29)))).merged()
            require((core ^ db.Region(db.Box(-300,0,300,200))).is_empty(),
                    f'Unexpected resistor core geometry: {cell}')
            metal = db.Region(top.shapes(ly.layer(layer,0))).merged()
            require((metal ^ db.Region(db.Box(-500,0,500,200))).is_empty(),
                    'Unexpected resistor terminal metal')
            for label, x in [('A',-400), ('B',400)]:
                top.shapes(ly.layer(layer,25)).insert(db.Text(label,db.Trans(x,100)))
                additions.append({'label':label,'layer':[layer,25],'point_dbu':[x,100]})
            cards = ['R0 A B lvsres w=2.6e-07 l=6e-07']; pins = ['A','B']
            mapping = {'R0':('A','B','res_'+tag)}
        else:
            cards = cell_cards(cdl); pins = PINS; mapping = RESISTORS
            for metal_layer, expected in [(10,2), (30,1)]:
                core = (db.Region(top.shapes(ly.layer(metal_layer,0))) &
                        db.Region(top.shapes(ly.layer(metal_layer,29)))).merged()
                require(core.count() == expected, 'Bitcell resistor marker count changed')
                for p in core.each():
                    require(sorted([p.bbox().width(),p.bbox().height()]) == [200,600]
                            and p.area() == 120000, 'Bitcell resistor dimensions changed')
        fixture_dir = out/tag; fixture_dir.mkdir(exist_ok=True)
        path = fixture_dir/'layout.gds'; ly.write(str(path))
        check = db.Layout(); check.read(str(path))
        require(fingerprint(check,check.top_cell()) == fingerprint(ly,top), 'GDS round-trip changed fixture')
        geometry[tag] = {'source_cell':cell, 'top':topname, 'dbu_um':ly.dbu,
                         'polygons_unchanged':True, 'added_terminal_labels':additions,
                         'source_geometry_sha256':hashlib.sha256(json.dumps(original,sort_keys=True).encode()).hexdigest(),
                         'fixture_sha256':sha(path)}
        refs = variants(topname,pins,cards,mapping,fixture_dir/'references')
        fixtures.append({'name':tag,'top':topname,'layout':str(path.resolve()),
                         'references':refs,'expected_devices':9 if layer is None else 1,
                         'scope':'Unmodified bitcell boundary' if layer is None else 'Isolated physical resistor with two added text labels'})
    write_json(out/'geometry.json',geometry)
    write_json(out/'fixtures.json',fixtures)
    return fixtures

def evaluate(reports):
    require(set(reports) == {'metal2','metal3','bitcell'}, 'Missing native fixture report')
    controls = {}
    for name in ('metal2','metal3'):
        r = reports[name]
        require(r['extraction_errors'] == [] and r['inventory_ok'], f'Invalid {name} extraction')
        cases = {c['variant']:c for c in r['cases']}
        require(set(cases) == set(VARIANTS), f'Missing {name} comparison case')
        controls[name] = {v: cases[v]['strict_match'] is (v == 'translated') for v in VARIANTS}
    bit = reports['bitcell']
    require(bit['extraction_errors'] == [] and bit['inventory_ok'], 'Unexpected bitcell extraction; inspect raw database')
    cases = {c['variant']:c for c in bit['cases']}
    require(set(cases) == set(VARIANTS), 'Missing bitcell comparison case')
    leaf_ok = all(all(v.values()) for v in controls.values())
    cell_ok = leaf_ok and all(cases[v]['strict_match'] is (v == 'translated') for v in VARIANTS)
    return {'status': ('SRAM_BITCELL_COMPATIBILITY_VERIFIED' if cell_ok else
                       'SRAM_BITCELL_BOUNDARY_REVIEW_REQUIRED' if leaf_ok and not cases['translated']['strict_match'] else
                       'SRAM_BITCELL_PROBE_REVIEW_REQUIRED'),
            'resistor_controls_pass':leaf_ok,'resistor_controls':controls,
            'bitcell_strict_match':cases['translated']['strict_match'],
            'bitcell_negative_controls_meaningful':cases['translated']['strict_match'],
            'bitcell_all_controls_pass':cell_ok,
            'full_sram_lvs_performed':False,'full_chip_lvs_performed':False,
            'candidate_modified':False,'cp0c':'PENDING'}

def audit_databases(probe, report, fixture):
    """Independently read every native saved comparison before accepting JSON."""
    import klayout.db as db
    rows = []
    for case in report['cases']:
        path = Path(probe)/case['variant']/'layout.lvsdb'
        saved = db.LayoutVsSchematic(); saved.read(str(path))
        require(saved.xref() is not None and saved.reference is not None,
                f'Missing saved comparison: {path}')
        require(not list(saved.each_error()), f'Saved extraction errors: {path}')
        a,b = saved.netlist().top_circuit(),saved.reference.top_circuit()
        require(a is not None and b is not None, f'Empty comparison: {path}')
        require(a.name.upper() == b.name.upper() == fixture['top'].upper(), f'Wrong top: {path}')
        require(len(list(a.each_device())) == fixture['expected_devices'] and not list(a.each_subcircuit()),
                f'Unexpected saved layout inventory: {path}')
        pa,pb = Counter(p.name() for p in a.each_pin()),Counter(p.name() for p in b.each_pin())
        ports_ok = pa == pb and all(n == 1 for n in pa.values())
        pairs = list(saved.xref().each_circuit_pair())
        top_match = len(pairs) == 1 and pairs[0].status() == db.NetlistCrossReference.Match
        strict = ports_ok and top_match and report['inventory_ok']
        require(strict == case['strict_match'] and ports_ok == case['port_set_ok'],
                f'Saved database disagrees with native summary: {path}')
        rows.append({'variant':case['variant'],'database_sha256':sha(path),
                     'strict_match':strict,'port_set_ok':ports_ok,
                     'circuit_statuses':[str(p.status()) for p in pairs]})
    require(len(rows) == len(VARIANTS), 'Missing saved comparisons')
    return {'status':'SAVED_COMPARISONS_AUDITED','cases':rows}
