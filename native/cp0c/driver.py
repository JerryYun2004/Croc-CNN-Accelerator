#!/usr/bin/env python3
"""Stage preparation and evidence gates; uses only Python's standard library."""
import hashlib
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

ORDER = ['preflight', 'floorplan', 'place', 'cts', 'route', 'finish']
SCRIPTS = dict(zip(ORDER[1:], ['01_floorplan.tcl', '02_placement.tcl',
    '03_cts.tcl', '04_routing.tcl', '05_finishing.tcl']))
CHECKPOINTS = dict(zip(ORDER[1:], ['01_croc.floorplan', '02_croc.placed',
    '03_croc.cts', '04_croc.routed', '05_croc.final']))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def manifest(files, target):
    files = sorted(set(Path(p).resolve() for p in files))
    require(bool(files), f'Empty manifest: {target}')
    lines = []
    for p in files:
        require(p.is_file(), f'Missing file: {p}')
        lines.append(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p}\n')
    target.write_text(''.join(lines))


def prepare(root, flow, pin, threads):
    files = subprocess.check_output(['git', '-C', str(root), 'ls-tree', '-r',
        '--name-only', pin, '--', 'openroad/scripts', 'openroad/src'], text=True).splitlines()
    require(files, 'No pinned OpenROAD flow sources found.')
    for rel in files:
        dest = flow / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dest)
    shutil.copytree(root / 'native/cp0c', flow / 'helpers')
    dest = flow / 'yosys/out/croc_yosys.v'
    dest.parent.mkdir(parents=True)
    shutil.copy2(root / 'yosys/out/croc_yosys.v', dest)
    for rel in ['openroad/reports', 'openroad/save', 'openroad/out',
                'openroad/logs', 'stamps']:
        (flow / rel).mkdir(parents=True, exist_ok=True)
    (flow / 'threads.txt').write_text(threads + '\n')
    inputs = [flow / rel for rel in files] + [dest, flow / 'threads.txt']
    inputs += list((flow / 'helpers').glob('*'))
    inputs += [root / 'native/env.sh', root / 'env.sh', root / 'Bender.lock',
               root / 'yosys/out/croc_yosys.v']
    # Hash the actual technology inputs, including the resolved technology
    # symlink target. Git pins alone do not cover an external technology view.
    technology = []
    for key in sorted(os.environ):
        if key.startswith(('PDK_DIR_LEF_', 'PDK_DIR_LIB_')):
            directory = Path(os.environ[key]).resolve()
            require(directory.is_dir(), f'Missing technology directory: {key}={directory}')
            technology.append(f'{key}={directory}\n')
            candidates = list(directory.glob('*.lef')) + list(directory.glob('*.lib'))
            require(candidates, f'No LEF/Liberty files in {directory}')
            inputs.extend(candidates)
    require(technology, 'Technology environment was not initialized.')
    (flow / 'technology.txt').write_text(''.join(technology))
    inputs.append(flow / 'technology.txt')
    manifest([p for p in inputs if p.is_file()], flow / 'inputs.sha256')


def stage(flow, name, run):
    require(name in ORDER, f'Unknown stage: {name}')
    # Do not accept stale downstream PASS files after rerunning an earlier stage.
    for later in ORDER[ORDER.index(name):]:
        for suffix in ['pass', 'sha256', 'command']:
            (flow / f'stamps/{later}.{suffix}').unlink(missing_ok=True)
    (flow / 'summary.txt').unlink(missing_ok=True)
    if name == 'preflight':
        text = 'source scripts/startup.tcl\nsource $::env(CP0C_HELPERS)/adapter.tcl\n'
        text += 'read_verilog $netlist\nlink_design $top_design\nread_sdc src/constraints.sdc\n'
    else:
        text = (flow / 'openroad/scripts' / SCRIPTS[name]).read_text()
        require(text.count('source scripts/startup.tcl') == 1,
                'Expected exactly one upstream startup call; stop to review upstream changes.')
        text = text.replace('source scripts/startup.tcl',
            'source scripts/startup.tcl\nsource $::env(CP0C_HELPERS)/adapter.tcl')
    (run / 'stage.tcl').write_text(text)


def stamp(flow, name):
    outputs = [flow / f'openroad/reports/cp0c-{name}.tsv']
    if name == 'preflight':
        outputs.append(flow / 'openroad/reports/cp0c-units.rpt')
    if name != 'preflight':
        outputs.append(flow / f'openroad/save/{CHECKPOINTS[name]}.zip')
    if name == 'route':
        outputs += [flow / 'openroad/reports/cp0c-timing.rpt',
                    flow / 'openroad/reports/cp0c-check-setup.rpt']
    if name == 'finish':
        outputs += [flow / f'openroad/out/{f}' for f in
            ['croc.def', 'croc.odb', 'croc.sdc', 'croc.v', 'croc_lvs.v']]
    for p in outputs:
        # A clean check_setup report is allowed to be empty.
        require(p.is_file() and (p.stat().st_size > 0 or p.name == 'cp0c-check-setup.rpt'),
                f'Missing/empty output: {p}')
    manifest(outputs, flow / f'stamps/{name}.sha256')
    (flow / f'stamps/{name}.pass').write_text('EXECUTION_COMPLETE\n')


def metrics(path):
    data = {}
    for line in path.read_text().splitlines():
        key, value = line.split('\t')
        require(key not in data, f'Duplicate metric {key} in {path}')
        number = float(value)
        require(math.isfinite(number), f'Nonfinite metric {key}')
        data[key] = number
    return data


def report(flow):
    require(all((flow / f'stamps/{name}.pass').is_file() for name in ORDER),
            'At least one stage is incomplete; run stages in order.')
    route = metrics(flow / 'openroad/reports/cp0c-route.tsv')
    final = metrics(flow / 'openroad/reports/cp0c-finish.tsv')
    required_route = ['router_drvs', 'setup_violations', 'hold_violations',
        'check_setup_clean', 'setup_tt', 'setup_ff', 'hold_tt', 'hold_ff',
        'max_slew_violations', 'max_cap_violations', 'max_fanout_violations']
    for key in required_route:
        require(key in route, f'Missing routing metric: {key}')
    for key in ['die_width_um', 'die_height_um', 'core_area_um2',
                'stdcell_area_um2', 'macro_area_um2', 'macro_count']:
        require(key in final, f'Missing physical metric: {key}')
    problems = []
    for key in ['router_drvs', 'setup_violations', 'hold_violations',
                'max_slew_violations', 'max_cap_violations', 'max_fanout_violations']:
        if route[key] != 0:
            problems.append(f'{key} = {route[key]:g}')
    if route['check_setup_clean'] != 1:
        problems.append('check_setup reported issues; inspect cp0c-check-setup.rpt')
    for key in ['setup_tt', 'setup_ff', 'hold_tt', 'hold_ff']:
        if route[key] < 0 or abs(route[key]) > 1e6:
            problems.append(f'{key} slack = {route[key]:g} (library time units)')
    if final['macro_count'] != 2:
        problems.append('Expected two SRAM macros')
    for key in ['die_width_um', 'die_height_um']:
        if abs(final[key] - 1916) > 0.001:
            problems.append(f'Unexpected {key}: {final[key]:g}')
    active = final['stdcell_area_um2'] + final['macro_area_um2']
    require(final['core_area_um2'] > 0, 'Core area must be positive.')
    status = 'CP0C REVIEW REQUIRED' if problems else 'CP0C PASS: implementation baseline with estimated RC'
    lines = [status, f'Flow directory: {flow}',
        'Physical outputs: ' + str(flow / 'openroad/out'),
        f"Die before seal ring: {final['die_width_um']:g} x {final['die_height_um']:g} um",
        'Planned sealed dimensions: 2000 x 2000 um (GDS not generated in CP0C)',
        f"Actual database core area: {final['core_area_um2']:.3f} um^2",
        f"Standard-cell area excluding filler: {final['stdcell_area_um2']:.3f} um^2",
        f"Macro area: {final['macro_area_um2']:.3f} um^2",
        f"Active/core area ratio: {active/final['core_area_um2']:.4f}",
        'Targets from unchanged SDC: sys 100 MHz, JTAG 40 MHz, ref 20 MHz.',
        'Slack values below use the Liberty time unit recorded in cp0c-units.rpt.',
        'Timing uses global-route RC estimates in the routing process.',
        'Corners: tt and ff only; no slow-corner signoff, extracted SPEF, or measured Fmax.',
        'Router DRVs are not foundry DRC. GDS, full DRC, LVS, IR drop and EM remain pending.']
    lines += [f'{key}: {route[key]:.9g}' for key in required_route]
    if problems:
        lines += ['Review items:'] + ['- ' + p for p in problems]
        lines += ['Do not mark timing closure or tag CP0C as passed. Retain the evidence.']
    text = '\n'.join(lines) + '\n'
    (flow / 'summary.txt').write_text(text)
    print(text, end='')
    return 2 if problems else 0


def main():
    action, *args = sys.argv[1:]
    if action == 'prepare':
        root, flow, pin, threads = args
        prepare(Path(root), Path(flow), pin, threads)
    elif action == 'stage':
        flow, name, run = args
        stage(Path(flow), name, Path(run))
    elif action == 'stamp':
        flow, name = args
        stamp(Path(flow), name)
    elif action == 'report':
        return report(Path(args[0]))
    else:
        raise RuntimeError(f'Unknown action: {action}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
