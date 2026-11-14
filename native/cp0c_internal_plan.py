"""Build the fixed eight-net ECO plan from the reviewed physical inventory."""
import collections
import math
from pathlib import Path
from cp0c_erc_support import require
from cp0c_spef_trace import rows, table, canon


def make_plan(trace_reports, current_findings, output):
    plans = rows(Path(trace_reports)/'repair-plan.tsv')
    pins = rows(Path(trace_reports)/'net-pins.tsv')
    findings = rows(current_findings)
    require(len(plans)==8 and len(findings)==8, 'Expected eight internal repair targets.')
    actual = {(r['check'],canon(r['pin'])) for r in findings}
    expected = {('max slew' if r['kind']=='slew' else 'max fanout',canon(r['target'])) for r in plans}
    require(actual==expected and len(actual)==8, 'Internal target set changed.')
    require(collections.Counter(r['kind'] for r in plans)=={'slew':5,'fanout':3}, 'Unexpected target categories.')
    by_net = collections.defaultdict(list)
    for pin in pins: by_net[pin['net']].append(pin)
    actions = []
    for i,plan in enumerate(plans):
        netpins = by_net[plan['net']]
        drivers = [p for p in netpins if p['direction']=='OUTPUT']
        ordinary = [p for p in netpins if p['direction']=='INPUT' and p['is_diode']=='0']
        diodes = [p for p in netpins if p['direction']=='INPUT' and p['is_diode']=='1']
        require(len(drivers)==1 and canon(drivers[0]['pin'])==canon(plan['driver']), 'Driver differs.')
        require(drivers[0]['master']==plan['driver_master'], 'Driver master differs.')
        require(len(ordinary)==int(plan['ordinary_loads']) and len(diodes)==int(plan['diode_loads']), 'Load inventory differs.')
        require(all(p['net_dont_touch']=='0' for p in netpins), 'Protected net.')
        if plan['kind']=='slew':
            require(len(ordinary)==1 and canon(ordinary[0]['pin'])==canon(plan['target']), 'Unexpected SRAM target.')
            groups = [ordinary]
        else:
            # Maximum three ordinary loads leaves margin for antenna diodes.
            # Keep original diodes on their root net; never remove protection.
            count = math.ceil(len(ordinary)/3)
            require(len(diodes)+count<=8, 'Planned root fanout exceeds eight.')
            def center(p,axis):
                return (int(p[axis+'min_dbu'])+int(p[axis+'max_dbu']))/2
            spans = {a:max(center(p,a) for p in ordinary)-min(center(p,a) for p in ordinary) for a in ('x','y')}
            axis = max(spans,key=lambda a:(spans[a],a=='x'))
            ordered = sorted(ordinary,key=lambda p:(center(p,axis),p['pin']))
            small,extra = divmod(len(ordered),count)
            groups = []; start = 0
            for g in range(count):
                size = small+int(g<extra)
                groups.append(ordered[start:start+size]); start += size
        for g,group in enumerate(groups):
            require(1<=len(group)<=3, 'Unexpected subgroup size.')
            name = f'cp0c_close_n{i}_g{g}'
            for pin in group:
                actions.append(dict(buffer_name=name,kind=plan['kind'],root_net=plan['net'],
                    driver=drivers[0]['pin'],driver_master=drivers[0]['master'],
                    expected_ordinary=len(ordinary),expected_diodes=len(diodes),
                    total_groups=len(groups),load_pin=pin['pin']))
    require(len(actions)==26 and len({r['buffer_name'] for r in actions})==13, 'Expected 26 moved loads / 13 buffers.')
    require(len({r['load_pin'] for r in actions})==26, 'Load assigned more than once.')
    table(output,list(actions[0]),actions)
    return actions
