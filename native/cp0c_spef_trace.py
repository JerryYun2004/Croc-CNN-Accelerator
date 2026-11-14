"""Read-only graph diagnostics for the pinned OpenRCX SPEF format.

Preserves exact source fragments. Canonicalization is only for name matching;
ambiguous normalized names are rejected. No artificial resistor is inserted.
"""
import collections
import csv
import math
from pathlib import Path
import re

from cp0c_erc_support import require


def rows(path):
    with Path(path).open() as f:
        return list(csv.DictReader(f, delimiter='\t'))


def table(path, fields, data):
    with Path(path).open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(data)


def canon(name):
    return re.sub(r'\\(.)', r'\1', name)


def unique_index(items, key):
    out = {}
    for item in items:
        norm = canon(item[key])
        require(norm not in out, f'Ambiguous normalized {key}: {item[key]}')
        out[norm] = item
    return out


def extract_graphs(spef, selected_names, excerpt):
    selected = {canon(n):n for n in selected_names}
    require(len(selected) == len(selected_names), 'Ambiguous normalized net names.')
    mapping = {}
    header = []
    graphs = {}
    graph = None
    in_net = False
    in_map = False
    section = None
    count = 0
    divider = delimiter = None

    def resolve(token):
        match = re.fullmatch(r'\*(\d+)(.*)', token)
        if match:
            require(match[1] in mapping, f'Missing SPEF name-map entry {token}')
            token = mapping[match[1]] + match[2]
        return canon(token)

    with Path(spef).open() as f, Path(excerpt).open('w') as out:
        for line in f:
            p = re.findall(r'(?:\\.|[^\s])+', line)
            if not p:
                if count == 0: header.append(line)
                elif graph is not None: out.write(line)
                continue
            key = p[0]
            if key == '*D_NET':
                require(not in_net and len(p) == 3, 'Malformed SPEF net boundary.')
                if count == 0:
                    require(divider == '/' and delimiter == ':', 'Expected pinned OpenRCX / divider and : delimiter.')
                    out.writelines(header)
                name = resolve(p[1])
                in_net, in_map, section = True, False, None
                count += 1
                graph = None
                if name in selected:
                    require(name not in graphs, f'Duplicate SPEF net: {name}')
                    graph = {'net':selected[name], 'connections':{}, 'adj':collections.defaultdict(set),
                             'rc_nodes':set(), 'resistors':0, 'zero_resistors':0, 'negative_rc':0}
                    graphs[name] = graph
                    out.write(line)
                continue
            if count == 0:
                header.append(line)
                if key == '*NAME_MAP': in_map = True
                elif key == '*PORTS': in_map = False
                elif key == '*DIVIDER': divider = p[1]
                elif key == '*DELIMITER': delimiter = p[1]
                elif in_map and re.fullmatch(r'\*\d+', key):
                    require(len(p) == 2 and key[1:] not in mapping, 'Malformed/duplicate SPEF name map.')
                    mapping[key[1:]] = p[1]
                continue
            if key == '*END':
                require(in_net, 'SPEF END outside a net.')
                if graph is not None: out.write(line)
                in_net, graph, section = False, None, None
                continue
            if graph is None:
                continue
            out.write(line)
            if key in ('*CONN','*CAP','*RES'):
                section = key
            elif section == '*CONN' and key in ('*I','*P'):
                require(len(p) >= 3, 'Malformed SPEF connection.')
                node = resolve(p[1])
                if key == '*I':
                    inst, sep, port = node.rpartition(':')
                    require(sep and inst and port, 'Malformed SPEF instance pin.')
                    pin = inst+'/'+port
                else:
                    pin = node
                require(pin not in graph['connections'], f'Duplicate SPEF pin alias: {pin}')
                graph['connections'][pin] = node
            elif section == '*RES' and key.isdigit():
                require(len(p) == 4, 'Expected a single-corner SPEF resistor.')
                value = float(p[3])
                require(math.isfinite(value), 'Nonfinite SPEF resistance.')
                graph['negative_rc'] += value < 0
                graph['zero_resistors'] += value == 0
                a,b = resolve(p[1]),resolve(p[2])
                graph['adj'][a].add(b); graph['adj'][b].add(a)
                graph['rc_nodes'].update((a,b)); graph['resistors'] += 1
            elif section == '*CAP' and key.isdigit():
                require(len(p) in (3,4), 'Malformed SPEF capacitance.')
                value = float(p[-1])
                require(math.isfinite(value), 'Nonfinite SPEF capacitance.')
                graph['negative_rc'] += value < 0
                graph['rc_nodes'].update(resolve(token) for token in p[1:-1])
    require(count > 0 and not in_net, 'Empty or truncated SPEF.')
    return graphs


def reachable(graph, node):
    if node not in graph['rc_nodes']:
        return set()
    seen = {node}; todo = [node]
    while todo:
        current = todo.pop()
        for other in graph['adj'].get(current, ()):
            if other not in seen:
                seen.add(other); todo.append(other)
    return seen


def diagnose(graph, driver, load):
    if graph is None: return 'NET_ABSENT_FROM_SPEF'
    conn = graph['connections']
    driver, load = canon(driver),canon(load)
    if driver not in conn: return 'DRIVER_ABSENT_FROM_CONN'
    if load not in conn: return 'LOAD_ABSENT_FROM_CONN'
    if conn[driver] not in graph['rc_nodes']: return 'DRIVER_WITHOUT_RC_NODE'
    if conn[load] not in graph['rc_nodes']: return 'LOAD_WITHOUT_RC_NODE'
    if conn[load] not in reachable(graph,conn[driver]): return 'DISCONNECTED_RC_COMPONENT'
    return 'REACHABLE_IN_SPEF_REVIEW_STA_MAPPING'


def analyze(spef, report_dir):
    report_dir = Path(report_dir)
    targets = rows(report_dir/'target-map.tsv')
    pins = rows(report_dir/'net-pins.tsv')
    pin_index = unique_index(pins,'pin')
    nets = rows(report_dir/'nets.tsv')
    net_index = unique_index(nets,'net')
    graphs = extract_graphs(spef,[n['net'] for n in nets],report_dir/'selected-nets.spef')
    findings = []
    for target in targets:
        if target['kind'] != 'partial': continue
        net = canon(target['net'])
        load = pin_index[canon(target['physical_load'])]
        driver = pin_index[canon(target['physical_driver'])]
        require(canon(load['net']) == canon(driver['net']) == net, 'Net/pin mapping mismatch.')
        findings.append(dict(net=target['net'], driver=target['physical_driver'], load=target['physical_load'],
            category=diagnose(graphs.get(net),target['physical_driver'],target['physical_load']),
            driver_wire_junction=driver['wire_junction_id'], load_wire_junction=load['wire_junction_id'],
            load_connected_flag=load['connected_flag'], ordered_flag=net_index[net]['ordered_flag']))
    table(report_dir/'missing-load-diagnosis.tsv',
          ['net','driver','load','category','driver_wire_junction','load_wire_junction','load_connected_flag','ordered_flag'], findings)
    summary = []
    for net in nets:
        graph = graphs.get(canon(net['net']))
        summary.append(dict(net=net['net'], spef_present=int(graph is not None),
            connections=len(graph['connections']) if graph else 0, resistors=graph['resistors'] if graph else 0,
            zero_resistors=graph['zero_resistors'] if graph else 0,
            negative_rc=graph['negative_rc'] if graph else 0))
    table(report_dir/'spef-net-summary.tsv',['net','spef_present','connections','resistors','zero_resistors','negative_rc'],summary)
    return collections.Counter(r['category'] for r in findings)


def repair_plan(report_dir, classified_file):
    report_dir = Path(report_dir)
    targets = rows(report_dir/'target-map.tsv')
    pins = rows(report_dir/'net-pins.tsv')
    nets = {r['net']:r for r in rows(report_dir/'nets.tsv')}
    by_net = collections.defaultdict(list)
    for pin in pins: by_net[pin['net']].append(pin)
    violations = {r['pin']:r for r in rows(classified_file) if r['category']=='Internal'}
    plans = []
    for target in targets:
        kind = target['kind']
        if kind not in ('slew','fanout'): continue
        reported = target['reported_load'] if kind=='slew' else target['reported_driver']
        violation = violations[reported]
        net_pins = by_net[target['net']]
        drivers = [p for p in net_pins if p['direction']=='OUTPUT']
        inputs = [p for p in net_pins if p['direction']=='INPUT']
        diodes = [p for p in inputs if p['is_diode']=='1']
        ordinary = [p for p in inputs if p['is_diode']=='0']
        blocks = []
        if len(drivers)!=1: blocks.append('not_single_driver')
        if int(nets[target['net']]['bterms'])!=0: blocks.append('top_level_connection')
        if any(p['direction'] not in ('INPUT','OUTPUT') for p in net_pins): blocks.append('unexpected_direction')
        if any(p['net_dont_touch']=='1' for p in net_pins): blocks.append('protected_net')
        groups = 1 if kind=='slew' else math.ceil(len(ordinary)/6)
        if kind=='fanout' and len(diodes)+groups>8: blocks.append('root_fanout_still_exceeds_8')
        plans.append(dict(kind=kind,target=reported,net=target['net'],
            driver=drivers[0]['pin'] if len(drivers)==1 else ';'.join(p['pin'] for p in drivers),
            driver_master=drivers[0]['master'] if len(drivers)==1 else '',
            ordinary_loads=len(ordinary),diode_loads=len(diodes),proposed_buffer_groups=groups,
            limit=violation['limit'],actual=violation['actual'],
            proposal='isolate_SRAM_input_near_macro' if kind=='slew' else 'buffer_ordinary_loads_max_6_per_group_keep_existing_diodes',
            status='BLOCKED_RC_COVERAGE'+(';'+';'.join(blocks) if blocks else '')))
    require(len(plans)==8,'Expected five slew and three fanout repair targets.')
    table(report_dir/'repair-plan.tsv',list(plans[0]),plans)
    return plans
