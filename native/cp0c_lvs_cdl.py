"""Translate only the pinned I/O library's legacy resistor and tap notation."""
import collections
import json
from pathlib import Path
import re

from cp0c_lvs_support import read_cdl, require, sha256

IO_SHA = '55368473d268802e83b7c2e4dca0420048c2f2e0dfd20572019f448e0c8f7938'


def translate_io(source, target, report):
    """Keep the original file; write an auditable, device-preserving derivative."""
    require(sha256(source) == IO_SHA, 'Legacy CDL translation requires the exact pinned I/O file')
    text = Path(source).read_text()
    # Fold continuation lines only. Comments, interfaces and all other cards remain.
    logical = re.sub(r'\n[ \t]*\+', ' ', text)
    output, changes = [], []
    cell = None
    for line in logical.splitlines():
        fields = line.split()
        if fields and fields[0].upper() == '.SUBCKT':
            cell = fields[1]
        replacement, kind = line, None
        if fields and fields[0][0].upper() == 'R' and '$[res_rppd]' in fields:
            require(len(fields) >= 7 and fields[4].startswith('$SUB=')
                    and fields[5] == '$[res_rppd]', 'Unexpected legacy rppd card')
            name, a, b, value, substrate, model, *params = fields
            require(all('=' in p for p in params), 'Unexpected rppd parameter token')
            require(not any(p.upper().startswith(('R=', 'MODEL=')) for p in params), 'Duplicate rppd value/model')
            # The pinned reader compares poly-resistor geometry, not the SPICE R value.
            # Retain that value explicitly as R= and preserve all geometric parameters.
            replacement = ' '.join([name, a, b, substrate.split('=', 1)[1], 'rppd', 'R='+value, *params])
            kind = 'res_rppd_to_rppd'
        elif fields and fields[0][0].upper() == 'X' and 'ptap1' in fields:
            require(len(fields) >= 6 and fields[3:5] == ['/', 'ptap1'], 'Unexpected ptap1 card')
            name, tie, well, slash, model, *params = fields
            require(all('=' in p for p in params), 'Unexpected ptap1 parameter token')
            values = {p.split('=', 1)[0].upper(): p.split('=', 1)[1] for p in params}
            require(len(values) == len(params) and 'R' in values and 'MODEL' not in values, 'Ambiguous ptap1 parameters')
            # Native CustomReader supports ptap1 as an R device with TIE/WELL terminals.
            replacement = ' '.join(['RCP0T_'+name, tie, well, values['R'], 'MODEL=ptap1', *params])
            kind = 'ptap1_subcircuit_to_device'
        if kind:
            changes.append({'circuit': cell, 'kind': kind, 'original': line, 'translated': replacement})
        output.append(replacement)
    counts = dict(collections.Counter(c['kind'] for c in changes))
    require(counts == {'res_rppd_to_rppd': 29, 'ptap1_subcircuit_to_device': 64},
            f'Unexpected pinned legacy card counts: {counts}')
    Path(target).write_text('\n'.join(output)+'\n')
    before, after = read_cdl([source]), read_cdl([target])
    require(before.keys() == after.keys(), 'Translation changed circuit inventory')
    for name, a in before.items():
        b = after[name]
        require(a['pins'] == b['pins'] and len(a['body']) == len(b['body']),
                f'Translation changed interface or device count: {name}')
    result = {'source_sha256': sha256(source), 'translated_sha256': sha256(target),
              'counts': counts, 'changes': changes, 'devices_removed': 0,
              'connections_added': 0, 'circuit_blackboxes': [],
              'full_chip_tap_device_extraction': True}
    Path(report).write_text(json.dumps(result, indent=2)+'\n')
    return result


def reference_expectations(paths, output):
    """Inventory every circuit, and specify terminals/geometry of translated devices."""
    import klayout.db as db
    delegate = db.NetlistSpiceReaderDelegate()
    cells = read_cdl(paths)
    result = {'circuits': {}, 'special_devices': []}
    for key, cell in cells.items():
        devices, instances = 0, 0
        for line in cell['body']:
            f = line.split()
            kind = f[0][0].upper()
            require(kind in 'XMRD', f'Unsupported reference element: {line}')
            if kind == 'X':
                positional = []
                for token in f[1:]:
                    if '=' in token:
                        break
                    positional.append(token)
                require(positional[-1].upper() in cells, f'Undefined reference subcircuit: {line}')
                instances += 1
            else:
                devices += 1
            special = None
            if kind == 'R' and f[4] == 'rppd':
                special = {'model': 'rppd', 'nets': dict(zip(['rppd_1', 'rppd_2', 'rppd_sub'], f[1:4]))}
                params = {p.split('=',1)[0].upper(): p.split('=',1)[1] for p in f[5:]}
                special['parameters'] = {p.lower(): float(delegate.value_from_string(params[p]))*scale
                                         for p, scale in [('W',1e6),('L',1e6),('PS',1e6),('B',1),('M',1)]}
            elif kind == 'R' and f[0].startswith('RCP0T_'):
                special = {'model': 'ptap1', 'nets': dict(zip(['TIE','WELL'], f[1:3]))}
                params = {p.split('=',1)[0].upper(): p.split('=',1)[1] for p in f[4:]}
                special['parameters'] = {'A': float(delegate.value_from_string(params['A']))*1e12,
                                         'P': float(delegate.value_from_string(params['PERIM']))*1e6}
            if special:
                # SPICE stores the name after the element-code character.
                special.update(circuit=key, name=f[0][1:].upper())
                result['special_devices'].append(special)
        result['circuits'][key] = {'pins': cell['pins'], 'devices': devices, 'subcircuits': instances}
    Path(output).write_text(json.dumps(result, indent=2)+'\n')
    return result
