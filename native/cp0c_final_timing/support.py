"""Conservative parsing and coverage checks for final timing characterization."""
import csv
import hashlib
import json
import math
import re
from pathlib import Path

COUNTS = ('setup_violations', 'hold_violations', 'max_slew_violations',
          'max_cap_violations', 'max_fanout_violations')
SLACKS = ('setup_tt', 'hold_tt', 'setup_ff', 'hold_ff')
SAMPLES = (('load_10pf', 10), ('load_7p5pf', 7.5), ('load_5pf', 5),
           ('load_4pf', 4), ('load_3pf', 3), ('load_2pf', 2),
           ('load_1pf', 1), ('load_0p5pf', 0.5))
KINDS = {'max slew': 'max_slew_violations', 'max capacitance': 'max_cap_violations',
         'max fanout': 'max_fanout_violations'}

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n')

def rows(path):
    with Path(path).open(newline='') as f:
        return list(csv.DictReader(f, delimiter='\t'))

def table(path, columns, values):
    with Path(path).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=columns, delimiter='\t', lineterminator='\n')
        w.writeheader()
        w.writerows(values)

def metrics(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        key, value = line.split('\t')
        require(key not in result, f'Duplicate metric {key}')
        n = float(value)
        require(math.isfinite(n), f'Non-finite metric {key}')
        result[key] = n
    return result

def erc_rows(path, violators_only=True):
    """Parse complete status-bearing rows. Do not infer PASS from empty reports."""
    data = []; kind = None
    for line in Path(path).read_text().splitlines():
        if line.strip() in KINDS:
            kind = line.strip()
        if not re.search(r'\((?:VIOLATED|MET)\)', line):
            continue
        p = line.split()
        require(kind is not None and p[-1] in ('(MET)', '(VIOLATED)'), f'Unknown ERC row: {line}')
        require(len(p) == 5 or (kind == 'max fanout' and len(p) == 4), f'Malformed ERC row: {line}')
        vals = [float(x) for x in p[1:-1]]
        require(all(math.isfinite(x) for x in vals), 'Non-finite ERC value')
        violated = p[-1] == '(VIOLATED)'
        require(not violators_only or violated, 'Unexpected passing row in violation-only report')
        data.append(dict(check=kind, pin=p[0], limit=vals[0], actual=vals[1],
                         reported_slack=vals[2] if len(vals) == 3 else None, violated=violated))
    return data

def checked_snapshot(reports, label, io_pins):
    met = metrics(reports / (label + '.tsv'))
    require(set(met) == set(COUNTS + SLACKS + ('stdcell_area_um2',)), 'Timing metric schema differs')
    require(all(met[k] >= 0 and met[k].is_integer() for k in COUNTS), 'Invalid violation count')
    erc = erc_rows(reports / (label + '-erc.rpt'))
    for kind, key in KINDS.items():
        require(sum(r['check'] == kind for r in erc) == met[key], f'ERC report/count mismatch: {label}/{kind}')
    for r in erc:
        r['category'] = 'I/O' if r['pin'] in io_pins else 'Internal'
    require(not (reports / (label + '-check-setup.rpt')).read_text().strip(), 'Timing coverage check_setup has findings')
    return {'metrics': met, 'erc': erc, 'internal_erc_findings': sum(r['category'] == 'Internal' for r in erc)}

def compare_metrics(current, reference, tolerance=0.005):
    return (all(current[k] == reference[k] for k in COUNTS) and
            all(abs(current[k] - reference[k]) <= tolerance for k in SLACKS) and
            abs(current['stdcell_area_um2'] - reference['stdcell_area_um2']) < 0.001)

def audit_annotation(reports, known):
    counts = metrics(reports / 'annotation-counts.tsv')
    full = rows(reports / 'unannotated.tsv')
    prior = rows(known)
    require(set(counts) == {'full', 'partial', 'loads'} and counts['full'] == len(full), 'Annotation count mismatch')
    require(counts['partial'] == counts['loads'] == 0, 'Incomplete internal RC annotation')
    require(not (reports / 'partial-drivers.txt').read_text().strip() and not rows(reports / 'partial-loads.tsv'),
            'Partial annotation records remain')
    # All columns are compared; no new exceptions are silently admitted.
    signature = lambda records: sorted(tuple(sorted(r.items())) for r in records)
    require(signature(full) == signature(prior), 'Unannotated-driver inventory differs from reviewed exceptions')
    cats = {k: sum(r['category'] == k for r in full) for k in {r['category'] for r in full}}
    require(cats == {'LOADED_PAD_NET': 80, 'NO_NET': 20, 'NO_OTHER_LOADS': 475}, 'Unexpected exception categories')
    return {'status': 'KNOWN_ANNOTATION_SCOPE_REPRODUCED', 'categories': cats,
            'partial_drivers': 0, 'missing_loads': 0,
            'pad_package_rc_complete': False,
            'scope': '80 loaded pad drivers retain the previously documented absent-parasitic model'}

def sdc_semantics(path):
    return '\n'.join(s.strip() for s in Path(path).read_text().splitlines()
                     if s.strip() and not s.lstrip().startswith('#'))

def audit_original_sdc(path):
    text = sdc_semantics(path)
    require(not re.search(r'^set_case_analysis\b.*(?:gpio|c2p_en)', text, re.M), 'GPIO enables were constrained')
    loads = {}
    for line in text.splitlines():
        if not line.startswith('set_load '):
            continue
        m = re.fullmatch(r'set_load(?: -pin_load)? ([0-9.eE+-]+) \[get_ports \{([^{}]+)\}\]', line)
        require(m is not None, f'Unexpected saved load syntax: {line}')
        require(m[2] not in loads, 'Duplicate load constraint')
        loads[m[2]] = float(m[1])
    expected = {f'gpio{i}_io' for i in range(32)} | {'jtag_tdo_o', 'status_o', 'uart_tx_o'} | \
               {f'unused{i}_o' for i in range(4)} | {'VDD', 'VDDIO', 'VSS', 'VSSIO'}
    require(set(loads) == expected and all(v == 15 for v in loads.values()), 'Original 15 pF load contract differs')
    return {'loaded_ports': len(loads), 'signal_outputs_and_inouts': 39, 'load_pf': 15, 'gpio_enable': 'dynamic'}

def pad_result(path):
    data = erc_rows(path, violators_only=False)
    result = {'status': 'UNCHARACTERIZED', 'slew_margin_ns': None, 'cap_margin_pf': None}
    for kind, key in [('max slew', 'slew_margin_ns'), ('max capacitance', 'cap_margin_pf')]:
        subset = [r for r in data if r['check'] == kind]
        if subset:
            # Recompute margin to avoid relying on rounded display slack alone.
            result[key] = min(r['limit'] - r['actual'] for r in subset)
    if all(result[k] is not None for k in ('slew_margin_ns', 'cap_margin_pf')):
        result['status'] = 'FAIL' if any(r['violated'] for r in data) else 'PASS'
    return result

def sampled_range(records):
    """Report sampled electrical envelope, never extrapolate to a rated maximum."""
    ordered = sorted(records, key=lambda r: r['load_pf'])
    fail_seen = False; monotonic = True
    for row in ordered:
        if row['status'] == 'FAIL': fail_seen = True
        elif row['status'] == 'PASS' and fail_seen: monotonic = False
    passed = [r['load_pf'] for r in ordered if r['status'] == 'PASS']
    failed = [r['load_pf'] for r in ordered if r['status'] == 'FAIL']
    unknown = any(r['status'] == 'UNCHARACTERIZED' for r in ordered)
    return {'largest_tested_pass_pf': max(passed) if passed else None,
            'smallest_tested_fail_pf': min(failed) if failed else None,
            'monotonic_samples': monotonic, 'all_samples_characterized': not unknown,
            'usable_sampled_envelope': monotonic and not unknown and bool(passed),
            'approved_external_load_pf': None}

def spef_stats(path):
    count = dict(nets=0, ends=0, connections=0, positive_resistances=0, positive_capacitances=0)
    section = None; active = False; units = {}
    with Path(path).open() as f:
        require(f.readline().startswith('*SPEF '), 'Invalid SPEF header')
        for line in f:
            p = line.split()
            if not p: continue
            k = p[0]
            if k in ('*C_UNIT', '*R_UNIT', '*T_UNIT'): units[k] = ' '.join(p[1:])
            elif k == '*D_NET':
                require(not active and len(p) == 3, 'Malformed SPEF net')
                value = float(p[2]); require(math.isfinite(value) and value >= 0, 'Invalid net capacitance')
                active = True; section = None; count['nets'] += 1
            elif k in ('*CONN', '*CAP', '*RES'):
                require(active, 'SPEF section outside net'); section = k
            elif k == '*END':
                require(active, 'Extra SPEF END'); active = False; count['ends'] += 1
            elif active and section == '*CONN' and k in ('*I', '*P'): count['connections'] += 1
            elif active and section in ('*CAP', '*RES') and k.isdigit():
                require(len(p) == 4 if section == '*RES' else len(p) in (3, 4), 'Malformed RC record')
                value = float(p[-1]); require(math.isfinite(value) and value >= 0, 'Invalid RC')
                if value > 0: count['positive_resistances' if section == '*RES' else 'positive_capacitances'] += 1
    require(not active and count['nets'] == count['ends'] and all(count.values()), 'Incomplete or empty SPEF')
    require(units == {'*C_UNIT': '1 PF', '*R_UNIT': '1 OHM', '*T_UNIT': '1 NS'}, 'Unexpected SPEF units')
    return count
