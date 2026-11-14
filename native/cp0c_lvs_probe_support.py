"""Isolated experiments; never edits or approves a chip candidate."""
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path

CELL = 'sg13cmos5l_SecondaryProtection'

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()

def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, allow_nan=False)+'\n')

def selected_layout(path):
    import klayout.db as db
    original = db.Layout(); original.read(str(path))
    cell = original.cell(CELL)
    require(cell is not None and not cell.is_empty(), f'Missing {CELL} in {path}')
    result = db.Layout(); result.dbu = original.dbu
    top = result.create_cell(CELL); top.copy_tree(cell); top.flatten(True)
    return result, top

def region(layout, cell, layer):
    import klayout.db as db
    return db.Region(cell.begin_shapes_rec(layout.layer(layer, 0))).merged()

def fingerprint(layout, cell, exclude=()):
    """Canonical union geometry plus exact labels, ignoring duplicate polygons."""
    import klayout.db as db
    result = {}
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        if (info.layer, info.datatype) in exclude:
            continue
        shapes = cell.shapes(index)
        polygons = sorted(p.to_s() for p in db.Region(shapes).merged().each())
        texts = sorted(set(s.text.to_s() for s in shapes.each() if s.is_text()))
        if polygons or texts:
            result[f'{info.layer}/{info.datatype}'] = {'polygons':polygons, 'texts':texts}
    return result

def prepare_geometry(io_gds, chip_gds, output_dir):
    """Add one recognition marker to a disposable isolated cell only."""
    import klayout.db as db
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    library, cell = selected_layout(io_gds)
    candidate, chipcell = selected_layout(chip_gds)
    require(library.dbu == candidate.dbu, 'Source and assembled database grids differ')
    require(fingerprint(library, cell) == fingerprint(candidate, chipcell),
            'Assembled cell differs from the pinned vendor cell; stop before marker experiment')
    require(abs(library.dbu-0.001) < 1e-12, 'Unexpected database grid')
    require(region(library, cell, 128).is_empty(), 'PolyRes marker already exists; hypothesis differs')
    # Legacy resistor core: GatPoly AND SalBlock AND pSD AND EXTBlock.
    core = region(library, cell, 5) & region(library, cell, 28)
    core &= region(library, cell, 14); core &= region(library, cell, 111)
    core.merge()
    expected = db.Region(db.Box(940,1370,1940,3370))
    require((core ^ expected).is_empty() and core.count() == 1,
            'Resistor geometry differs from the reviewed 1 x 2 um rectangle')
    original = fingerprint(library, cell)
    library.write(str(out/'original.gds'))
    cell.shapes(library.layer(128,0)).insert(core)
    require(fingerprint(library, cell, ((128,0),)) == original,
            'Unexpected geometry or text change outside the diagnostic marker')
    library.write(str(out/'marker-only.gds'))
    check, checkcell = selected_layout(out/'marker-only.gds')
    require(fingerprint(check, checkcell, ((128,0),)) == original,
            'GDS round trip changed a non-marker layer')
    require((region(check, checkcell, 128) ^ expected).is_empty(), 'Marker round trip failed')
    report = {'cell':CELL, 'source_and_assembled_geometry_equal':True,
              'original_polyres_polygons':0, 'diagnostic_polyres_polygons':1,
              'marker_layer':[128,0], 'marker_bbox_um':[0.94,1.37,1.94,3.37],
              'candidate_w_um':1.0, 'candidate_l_um':2.0,
              'changed_layers':[[128,0]], 'physical_geometry_and_labels_unchanged':True,
              'scope':'Disposable isolated-cell experiment; original chip GDS is not modified.'}
    write_json(out/'geometry.json', report)
    return report

def build_dummy(snapshot):
    """Rebuild the complete three-device graph from the inspector's actual data."""
    import klayout.db as db
    require(snapshot['name'].upper() == 'RSC_IHPSG13_CDLYX1_DUMMY', 'Wrong diagnostic circuit')
    require(not snapshot['subcircuits'] and not snapshot['device_inventory_truncated'], 'Incomplete graph')
    nl = db.Netlist(); circuit = db.Circuit(); circuit.name = snapshot['name']; nl.add(circuit)
    nets, classes = {}, {}
    def net(row):
        require(row is not None, 'Unexpected disconnected terminal')
        key = (row['name'], row['cluster_id'])
        if key not in nets:
            nets[key] = circuit.create_net(row['name'])
        return nets[key]
    for pin in snapshot['pins']:
        circuit.connect_pin(circuit.create_pin(pin['name']), net(pin['net']))
    for item in snapshot['device_inventory']:
        model = item['model']
        require(model in ('sg13_lv_nmos','sg13_lv_pmos','res_metal1','LVSRES'), 'Unexpected model')
        if model not in classes:
            cls = db.DeviceClassMOS4Transistor() if 'mos' in model else db.DeviceClassResistor()
            cls.name = model
            if 'mos' not in model:
                # Geometry convention of the pinned RES2 extractor. LVSRES has
                # no physical R value in CDL; never treat its default zero as R.
                cls.enable_parameter('W',True); cls.enable_parameter('L',True)
                cls.enable_parameter('R',False)
            nl.add(cls); classes[model] = cls
        device = circuit.create_device(classes[model], item['expanded_name'])
        for name,value in item['parameters'].items():
            if name == 'rfmode':
                require(value == 0, 'RF device unsupported by this probe')
            else:
                device.set_parameter(name,value)
        for name,value in item['terminals'].items():
            device.connect_terminal(name,net(value))
    return nl

def dummy_comparison(snapshot, mapped=False, defect=None):
    import klayout.db as db
    reference = copy.deepcopy(snapshot['reference'])
    resistor = next(d for d in reference['device_inventory'] if d['model']=='LVSRES')
    if defect == 'width':
        resistor['parameters']['W'] *= 2
    elif defect == 'missing':
        reference['device_inventory'].remove(resistor)
    elif defect == 'wiring':
        resistor['terminals']['B'] = copy.deepcopy(resistor['terminals']['A'])
    a, b = build_dummy(snapshot['layout']), build_dummy(reference)
    comparer = db.NetlistComparer()
    if mapped:
        ac = a.device_class_by_name('res_metal1'); bc = b.device_class_by_name('LVSRES')
        # KLayout interprets a nil mapping as an instruction to IGNORE that
        # device class. A missing counterpart must be a failure, not a waiver.
        if ac is None or bc is None:
            return False
        comparer.same_device_classes(ac, bc)
    # No forced net matches, blackboxes, device deletion or parameter tolerance.
    return bool(comparer.compare(a,b))

def dummy_probe(snapshot, output_dir):
    out = Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    outcomes = {'unmapped':dummy_comparison(snapshot), 'geometry_model_mapping':dummy_comparison(snapshot,True)}
    outcomes.update({f'negative_{d}':dummy_comparison(snapshot,True,d) for d in ('width','missing','wiring')})
    expected = {'unmapped':False,'geometry_model_mapping':True,
                'negative_width':False,'negative_missing':False,'negative_wiring':False}
    result = {'status':'SRAM_DUMMY_MODEL_PROBE_CONFIRMED' if outcomes == expected else 'SRAM_DUMMY_PROBE_REVIEW_REQUIRED',
              'outcomes':outcomes, 'expected':expected,
              'scope':'Three-device inspector snapshot only; not full SRAM or chip LVS.',
              'comparison':'L and W geometry; resistor model equivalence; no forced net mapping.',
              'remaining_sram_macro_issues':['res_metal2/3 versus LVSRES',
                  '904 additional NMOS and 904 additional PMOS per aligned macro definition',
                  '189 versus 191 macro pins']}
    write_json(out/'summary.json',result)
    return result

def extraction_inventory(db_path):
    import klayout.db as db
    saved = db.LayoutVsSchematic(); saved.read(str(db_path))
    nl = saved.netlist()
    matches = [c for c in nl.each_circuit() if c.name.upper()==CELL.upper()]
    require(len(matches)==1, 'Probe cell vanished or became ambiguous during alignment')
    circuit = matches[0]
    devices = []
    for device in circuit.each_device():
        cls = device.device_class()
        devices.append({'model':cls.name,
            'parameters':{p.name:device.parameter(p.id()) for p in cls.parameter_definitions()},
            'terminals':{t.name:(device.net_for_terminal(t.id()).name if device.net_for_terminal(t.id()) else None)
                         for t in cls.terminal_definitions()}})
    # Net iteration is on the owning extracted circuit, never an xref proxy.
    named_nets = [n.name for n in circuit.each_net()]
    merged = any({'core','pad'} <= set(n.lower().replace('|',',').split(',')) for n in named_nets)
    distinct = any(n.lower()=='core' for n in named_nets) and any(n.lower()=='pad' for n in named_nets) and not merged
    return {'models':dict(Counter(d['model'].lower() for d in devices)), 'devices':devices,
            'core_pad_merged':merged, 'core_pad_distinct':distinct,
            'net_names':named_nets, 'extraction_errors':[str(e) for e in saved.each_error()]}
