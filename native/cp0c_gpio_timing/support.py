"""GPIO diagnostic cases and explicit, non-signoff evidence comparisons."""
import math
import re
from pathlib import Path
from base_support import (require, rows, metrics, erc_rows, sdc_semantics, audit_original_sdc,
                          COUNTS, SLACKS, compare_metrics)

GPIOS = {f'gpio{i}_io' for i in range(32)}
OUTPUTS = {'jtag_tdo_o', 'status_o', 'uart_tx_o'} | {f'unused{i}_o' for i in range(4)}
CASES = [
    dict(name='dynamic_15pf', load=15, oe=None, stimulus=None, prior='original_15pf'),
    dict(name='dynamic_3pf', load=3, oe=None, stimulus=None, prior='load_3pf'),
    dict(name='dynamic_0p5pf', load=.5, oe=None, stimulus=None, prior='load_0p5pf'),
    dict(name='transmit_3pf', load=3, oe=1, stimulus=None),
    dict(name='transmit_0p5pf', load=.5, oe=1, stimulus=None),
    dict(name='receive_3pf', load=3, oe=0, stimulus=None),
    dict(name='receive_0p5pf', load=.5, oe=0, stimulus=None),
    dict(name='receive_fast_3pf', load=3, oe=0, stimulus=.2),
    dict(name='receive_slow_3pf', load=3, oe=0, stimulus=4),
    dict(name='dynamic_fast_3pf', load=3, oe=None, stimulus=.2),
]

def variant_sdc(source, dest, case):
    """Change only an exact, pinned SDC copy before any timing calculation."""
    audit_original_sdc(source)
    text = Path(source).read_text()
    if case['name'] == 'dynamic_15pf':
        Path(dest).write_text(text)
        return dict(changed_loads=0, removed_drivers=0, case_analysis_pins=0, input_slews=0)
    out = []; changed = set(); removed = set()
    for line in text.splitlines():
        m = re.fullmatch(r'set_load -pin_load 15\.0000 \[get_ports \{([^{}]+)\}\]', line)
        if m and m[1] in GPIOS | OUTPUTS:
            require(m[1] not in changed, 'Duplicate output load')
            changed.add(m[1]); line = f'set_load -pin_load {case["load"]:.4f} [get_ports {{{m[1]}}}]'
        d = re.fullmatch(r'set_driving_cell -lib_cell sg13cmos5l_IOPadOut16mA -pin \{pad\} '
                         r'-input_transition_rise 0\.0000 -input_transition_fall 0\.0000 '
                         r'\[get_ports \{(gpio\d+_io)\}\]', line)
        if case['stimulus'] is not None and d:
            require(d[1] in GPIOS and d[1] not in removed, 'Unexpected GPIO driver')
            removed.add(d[1]); continue
        out.append(line)
    require(changed == GPIOS | OUTPUTS, 'Did not change exactly the 39 signal loads')
    require(removed == (GPIOS if case['stimulus'] is not None else set()), 'GPIO stimulus coverage differs')
    out.append('# DIAGNOSTIC ONLY: generated copy; no accepted constraint change.')
    for port in sorted(GPIOS):
        if case['oe'] is not None:
            out.append(f'set_case_analysis {case["oe"]} [get_pins {{pad_{port}/c2p_en}}]')
        if case['stimulus'] is not None:
            out.append(f'set_input_transition {case["stimulus"]:.4f} [get_ports {{{port}}}]')
    Path(dest).write_text('\n'.join(out)+'\n')
    return dict(changed_loads=len(changed), removed_drivers=len(removed),
                case_analysis_pins=32 if case['oe'] is not None else 0,
                input_slews=32 if case['stimulus'] is not None else 0)

def audit_runtime_sdc(path, case):
    """Verify the written runtime constraints include the intended diagnostic only."""
    text = sdc_semantics(path)
    loads = {}; drivers = set(); fixed = {}; slews = {}
    for line in text.splitlines():
        if line.startswith('set_load '):
            m = re.fullmatch(r'set_load(?: -pin_load)? ([0-9.eE+-]+) \[get_ports \{([^{}]+)\}\]', line)
            require(m is not None and m[2] not in loads, 'Unknown/duplicate runtime load')
            loads[m[2]] = float(m[1])
        if line.startswith('set_driving_cell ') and 'gpio' in line:
            m = re.search(r'\[get_ports \{(gpio\d+_io)\}\]$', line)
            require(m is not None and m[1] not in drivers, 'Unknown/duplicate GPIO driver')
            drivers.add(m[1])
        if line.startswith('set_case_analysis '):
            m = re.fullmatch(r'set_case_analysis ([01]) \[get_pins \{pad_(gpio\d+_io)/c2p_en\}\]', line)
            require(m is not None and m[2] not in fixed, 'Unexpected case analysis')
            fixed[m[2]] = int(m[1])
        if line.startswith('set_input_transition '):
            m = re.fullmatch(r'set_input_transition ([0-9.eE+-]+) \[get_ports \{(gpio\d+_io)\}\]', line)
            require(m is not None and m[2] not in slews, 'Unexpected input slew')
            slews[m[2]] = float(m[1])
    expected = {p: float(case['load']) for p in GPIOS | OUTPUTS}
    expected.update({p: 15.0 for p in ['VDD','VDDIO','VSS','VSSIO']})
    require(loads == expected, 'Runtime output loads differ')
    require(drivers == (GPIOS if case['stimulus'] is None else set()), 'Runtime driver model differs')
    require(fixed == ({p: case['oe'] for p in GPIOS} if case['oe'] is not None else {}), 'Runtime OE coverage differs')
    require(slews == ({p: float(case['stimulus']) for p in GPIOS} if case['stimulus'] is not None else {}), 'Runtime input slew differs')
    return dict(loads_verified=True, fixed_gpio_enables=len(fixed), explicit_input_slews=len(slews),
                constraint_change_accepted=False)

def slew_rows(path):
    result = []
    for line in Path(path).read_text().splitlines():
        if not line.strip(): continue
        m = re.fullmatch(r'(.+?)\s+\^\s+(\S+):(\S+)\s+v\s+(\S+):(\S+)\s*', line)
        require(m is not None, f'Unrecognized slew row in {path}: {line}')
        head = m[1].split(); require(len(head) in (1,2), 'Malformed vertex name')
        side = head[1] if len(head)==2 else 'single'
        require(side in ('single','load','driver'), 'Unknown vertex side')
        nums = []
        for value in m.groups()[1:]:
            try: n = float(value)
            except ValueError:
                require(value in ('INF','-INF','inf','-inf'), 'Unknown slew number')
                n = float(value)
            nums.append(n if math.isfinite(n) and abs(n)<1e20 else None)
        result.append(dict(pin=head[0], side=side, **dict(zip(
            ['rise_min_ns','rise_max_ns','fall_min_ns','fall_max_ns'], nums))))
    require(result and len({(r['pin'],r['side']) for r in result}) == len(result), 'Empty/duplicate slew evidence')
    return result

def compare_slews(current, prior, tolerance=1e-5):
    def keyed(data): return {(r['pin'],r['side']):r for r in data}
    c=keyed(current); p=keyed(prior); diffs=[]
    require(c.keys()==p.keys(), 'Slew vertex coverage changed')
    for key in p:
        for field in ('rise_min_ns','rise_max_ns','fall_min_ns','fall_max_ns'):
            a=c[key][field]; b=p[key][field]
            if a is None or b is None or abs(a-b)>tolerance:
                diffs.append(dict(pin=key[0],side=key[1],field=field,fresh=a,prior_sweep=b))
    return diffs

def snapshot(reports):
    met=metrics(reports/'case.tsv')
    require(set(met)==set(COUNTS+SLACKS+('stdcell_area_um2',)), 'Unexpected metric schema')
    erc=erc_rows(reports/'case-erc.rpt')
    for check,key in [('max slew','max_slew_violations'),('max capacitance','max_cap_violations'),('max fanout','max_fanout_violations')]:
        require(sum(r['check']==check for r in erc)==met[key], 'ERC count/report mismatch')
    pins=set((reports/'io-pins.txt').read_text().splitlines())
    return dict(metrics=met, internal_erc_findings=sum(r['pin'] not in pins for r in erc),
                check_setup=(reports/'case-check-setup.rpt').read_text().strip(),
                clock_check_findings='(VIOLATED)' in (reports/'clock-checks.rpt').read_text())

def input_control(fast, slow):
    """Both known stimuli must be observed; absent/disabled vertices never pass."""
    def index(data): return {(r['port'],r['corner'],r['pin'],r['side']):r for r in data}
    a=index(fast); b=index(slow); checks=[]
    for port in sorted(GPIOS):
        for corner in ('tt','ff'):
            key=(port,corner,f'pad_{port}/pad','load')
            x=a.get(key); y=b.get(key)
            ok=x is not None and y is not None
            if ok:
                ok=all(x[f] is not None and y[f] is not None and y[f]-x[f]>.1
                       for f in ('rise_max_ns','fall_max_ns'))
            checks.append(dict(port=port,corner=corner,responds_to_stimulus=ok))
    return dict(verified=all(r['responds_to_stimulus'] for r in checks),checks=checks,
                note='Synthetic .2 ns and 4 ns stimuli test analysis sensitivity, not a board specification.')
