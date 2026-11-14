"""Strict structural-Verilog to CDL preparation and KLayout LVS report audit."""
import collections
import hashlib
import json
from pathlib import Path
import re

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def read_cdl(paths):
    """Retain original bodies/pin order; reject duplicate or malformed definitions."""
    cells = {}
    for path in paths:
        text = Path(path).read_text()
        logical = re.sub(r'\n[ \t]*\+', ' ', text)
        current = None
        for line in logical.splitlines():
            s = line.strip()
            if not s or s.startswith('*'):
                continue
            fields = s.split()
            command = fields[0].upper()
            if command == '.SUBCKT':
                require(current is None, f'Nested subcircuit in {path}')
                name, pins = fields[1], fields[2:]
                require(name.upper() not in cells, f'Duplicate CDL circuit: {name}')
                require(all('=' not in p for p in pins), f'Parameterized interface: {name}')
                require(len({p.upper() for p in pins}) == len(pins), f'Duplicate pin: {name}')
                current = {'name': name, 'pins': pins, 'body': [], 'source': str(path)}
                cells[name.upper()] = current
            elif command == '.ENDS':
                require(current is not None, f'Unmatched .ENDS in {path}')
                require(len(fields) == 1 or fields[1].upper() == current['name'].upper(), 'Mismatched .ENDS')
                current = None
            elif current is None and command == '.PARAM' and len(fields) == 1:
                # The pinned I/O CDL includes an empty .PARAM directive.
                continue
            elif current is not None:
                current['body'].append(s)
            else:
                raise RuntimeError(f'Unexpected CDL outside subcircuit: {s[:100]}')
        require(current is None, f'Unclosed CDL circuit in {path}')
    require(cells, 'No CDL subcircuits found')
    return cells

def pin_parts(pin):
    m = re.fullmatch(r'(.+?)[<\[]([0-9]+)[>\]]', pin)
    return (m[1], int(m[2])) if m else (pin, None)

def groups(cell):
    result = {}
    for pin in cell['pins']:
        base, index = pin_parts(pin)
        result.setdefault(base, []).append(index)
    for base, indices in result.items():
        if indices != [None]:
            require(None not in indices and len(set(indices)) == len(indices), f'Mixed scalar/bus pin: {base}')
            require(set(indices) == set(range(min(indices), max(indices) + 1)), f'Sparse pin bus: {base}')
    return result

def vname(name):
    require(not re.search(r'\s|\\', name), f'Unsupported Verilog identifier: {name!r}')
    return '\\' + name + ' '

def make_stubs(cells, masters, path):
    lines = ['// Port-only interfaces; used for parsing, never synthesized.']
    for name in sorted(masters):
        require(name.upper() in cells, f'No matching CDL model for physical master: {name}')
        cell = cells[name.upper()]
        require(cell['name'] == name, f'CDL case differs: {name}')
        ports = groups(cell)
        lines.append('(* blackbox *) module ' + vname(name) + '(' + ', '.join(map(vname, ports)) + ');')
        for base, indices in ports.items():
            width = '' if indices == [None] else f'[{max(indices)}:{min(indices)}] '
            lines.append('  inout ' + width + vname(base) + ';')
        lines.append('endmodule')
    Path(path).write_text('\n'.join(lines) + '\n')

EXCLUDED_PHYSICAL = {'sg13g2_fill_1', 'sg13g2_fill_2', 'sg13g2_fill_4', 'sg13g2_fill_8', 'bondpad5l_70x70'}
POWER_PINS = {'VDD', 'VSS', 'VDD!', 'VDDARRAY!', 'VSS!', 'IOVDD', 'IOVSS'}

def expected_masters(assembly):
    return {k: v for k, v in assembly['masters'].items() if k not in EXCLUDED_PHYSICAL}

def convert_json(json_path, cells, expected, out, report_dir, top='croc_chip'):
    """Map every instance by named ports into the authoritative CDL pin order."""
    data = json.loads(Path(json_path).read_text())
    require(top in data['modules'], 'Missing intended top module')
    module = data['modules'][top]
    require(not module.get('processes') and not module.get('memories'), 'Reference is not a structural netlist')
    actual = collections.Counter(c['type'] for c in module['cells'].values())
    require(dict(actual) == expected, f'Verilog/GDS master counts differ: actual={dict(actual)} expected={expected}')
    require(sum(actual.values()) > 0, 'Empty reference is forbidden')
    top_bits = {}
    top_pins = []
    for name, port in module['ports'].items():
        bits = port['bits']
        is_bus = len(bits) != 1 or port.get('offset', 0) != 0 or port.get('upto', 0)
        for i, bit in enumerate(bits):
            index = port.get('offset', 0) + (len(bits)-1-i if port.get('upto', 0) else i)
            pin = f'{name}[{index}]' if is_bus else name
            require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\[[0-9]+\])?', pin), f'Unsupported top port: {pin}')
            require(type(bit) is int and bit not in top_bits, f'Aliased/constant top port: {pin}')
            require(pin.upper() not in {p.upper() for p in top_pins}, 'Case-colliding top ports')
            top_bits[bit] = pin
            top_pins.append(pin)
    require(top_pins, 'Reference has no top ports')
    lines = ['* Structural reference from the saved powered Verilog; no optimization.', '.SUBCKT ' + top]
    lines += ['+ ' + ' '.join(top_pins[i:i+16]) for i in range(0, len(top_pins), 16)]
    mapping = ['spice_instance\tverilog_instance\tmaster']
    open_pins = []
    consumed = 0
    for number, (name, inst) in enumerate(sorted(module['cells'].items()), 1):
        cell = cells[inst['type'].upper()]
        ports = groups(cell)
        connections = inst['connections']
        require(not inst.get('parameters'), f'Unexpected cell parameters: {name}')
        require(set(connections) <= set(ports), f'Unknown instance ports on {name}: {set(connections)-set(ports)}')
        for base, bits in connections.items():
            width = 1 if ports[base] == [None] else len(ports[base])
            require(len(bits) in (0, width), f'Wrong bus width on {name}.{base}')
        nodes = []
        for pin in cell['pins']:
            base, index = pin_parts(pin)
            bits = connections.get(base, [])
            if not bits:
                require(base.upper() not in POWER_PINS, f'Unconnected supply pin: {name}.{pin}')
                net = f'CP0OPEN{number}_{len(nodes)}'
                open_pins.append({'instance': name, 'pin': pin, 'net': net})
            else:
                bit = bits[0 if index is None else index-min(ports[base])]
                require(type(bit) is int, f'Unresolved constant {bit!r} at {name}.{pin}; physical tie connectivity required')
                net = top_bits.get(bit, f'CP0NET{bit}')
                consumed += 1
            nodes.append(net)
        spice_name = f'XCP0I{number}'
        mapping.append('\t'.join([spice_name, name, cell['name']]))
        lines.append(spice_name)
        all_fields = nodes + [cell['name']]
        lines += ['+ ' + ' '.join(all_fields[i:i+16]) for i in range(0, len(all_fields), 16)]
    lines.append('.ENDS ' + top)
    Path(out).write_text('\n'.join(lines) + '\n')
    r = Path(report_dir)
    (r/'instance-map.tsv').write_text('\n'.join(mapping) + '\n')
    result = {'top': top, 'instances': sum(actual.values()), 'masters': dict(actual),
              'connected_terminals': consumed, 'open_terminals': open_pins,
              'top_pins': top_pins, 'optimization': False, 'blackboxed_lvs_cells': []}
    (r/'reference.json').write_text(json.dumps(result, indent=2) + '\n')
    return result

def audit_lvs(dbpath, verdict_path, report_path, expected_top):
    """A successful process, nonempty extraction and an actual comparison are mandatory."""
    import klayout.db as db
    verdict = json.loads(Path(verdict_path).read_text())
    require(type(verdict.get('success')) is bool, 'Missing machine-readable comparison verdict')
    lvs = db.LayoutVsSchematic()
    lvs.read(str(dbpath))
    xref = lvs.xref()
    require(xref is not None, 'No LVS cross-reference: comparison was not performed')
    rows = []
    samples = []
    counts = collections.Counter()
    top_found = False
    for pair in xref.each_circuit_pair():
        a, b = pair.first(), pair.second()
        names = [a.name if a else None, b.name if b else None]
        rows.append({'layout': names[0], 'reference': names[1], 'status': str(pair.status())})
        if all(n and n.upper() == expected_top.upper() for n in names):
            top_found = True
        counts[str(pair.status())] += 1
        if a is not None and b is not None and pair.status() != db.NetlistCrossReference.Match:
            for kind, method in [('net', xref.each_net_pair), ('pin', xref.each_pin_pair),
                                 ('device', xref.each_device_pair), ('subcircuit', xref.each_subcircuit_pair)]:
                for item in method(a):
                    if item.status() != db.NetlistCrossReference.Match and len(samples) < 200:
                        samples.append({'circuit': names, 'kind': kind, 'status': str(item.status()),
                                        'layout': str(item.first()) if item.first() is not None else None,
                                        'reference': str(item.second()) if item.second() is not None else None})
    layout_devices = sum(sum(1 for _ in c.each_device()) for c in lvs.netlist().each_circuit())
    errors = [str(e) for e in lvs.each_error()]
    strict = bool(verdict['success'] and top_found and rows and layout_devices > 0 and not errors
                  and all(r['status'] == str(db.NetlistCrossReference.Match) for r in rows))
    outcome = 'LVS_MATCH' if strict else 'LVS_REVIEW_REQUIRED'
    result = {'status': outcome, 'deck_verdict': verdict, 'top_pair_present': top_found,
              'layout_devices_per_definition': layout_devices, 'circuit_status_counts': dict(counts),
              'circuits': rows, 'mismatch_samples': samples, 'extraction_errors': errors,
              'scope': f'{expected_top}, transistor extraction, strict top ports; '
                       f'tap devices {"enabled" if verdict.get("tap_device_extraction") else "disabled"}; no circuit blackboxes'}
    Path(report_path).write_text(json.dumps(result, indent=2) + '\n')
    return result

def smoke_layout(stdgds, out):
    import klayout.db as db
    ly = db.Layout()
    ly.read(str(stdgds))
    cell = ly.cell('sg13g2_inv_1')
    require(cell is not None and not cell.is_empty(), 'Missing smoke inverter geometry')
    options = db.SaveLayoutOptions()
    options.select_cell(cell.cell_index())
    ly.write(str(out), options)

if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'audit':
        audit_lvs(*sys.argv[2:])
    elif sys.argv[1] == 'smoke-layout':
        smoke_layout(*sys.argv[2:])
    else:
        raise SystemExit('Unknown worker mode')
