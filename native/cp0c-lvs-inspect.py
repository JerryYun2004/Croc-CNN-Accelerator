#!/usr/bin/env python3
"""Read-only, per-circuit diagnosis of an existing CP0C LVS database."""
import argparse
from collections import Counter
import datetime
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

VERSION = '1.0-read-only-inspection'
PER_KIND_LIMIT = 30
INVENTORY_LIMIT = 500


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')


def net_info(net):
    if net is None:
        return None
    return {'name': net.name, 'expanded_name': net.expanded_name(), 'cluster_id': net.cluster_id}


def pin_info(pin, circuit):
    if pin is None:
        return None
    return {'id': pin.id(), 'name': pin.name(), 'net': net_info(circuit.net_for_pin(pin.id()))}


def device_info(device):
    if device is None:
        return None
    cls = device.device_class()
    return {'id': device.id(), 'name': device.name, 'expanded_name': device.expanded_name(),
            'model': cls.name,
            'parameters': {p.name: device.parameter(p.id()) for p in cls.parameter_definitions()},
            'terminals': {t.name: net_info(device.net_for_terminal(t.id())) for t in cls.terminal_definitions()}}


def subcircuit_info(inst):
    if inst is None:
        return None
    child = inst.circuit_ref()
    return {'id': inst.id(), 'name': inst.name, 'circuit': child.name,
            'pins': [{'name': p.name(), 'net': net_info(inst.net_for_pin(p.id()))} for p in child.each_pin()]}


def circuit_info(circuit, detailed=False):
    if circuit is None:
        return None
    models = Counter(d.device_class().name for d in circuit.each_device())
    children = Counter(s.circuit_ref().name for s in circuit.each_subcircuit())
    result = {'name': circuit.name, 'cell_index': circuit.cell_index,
              'pins': [pin_info(p, circuit) for p in circuit.each_pin()],
              'devices': sum(models.values()), 'devices_by_model': dict(sorted(models.items())),
              'subcircuits': sum(children.values()), 'children': dict(sorted(children.items()))}
    if detailed:
        result['device_inventory'] = [device_info(d) for d in itertools.islice(circuit.each_device(), INVENTORY_LIMIT)]
        result['subcircuit_inventory'] = [subcircuit_info(s) for s in itertools.islice(circuit.each_subcircuit(), INVENTORY_LIMIT)]
        result['device_inventory_truncated'] = result['devices'] > INVENTORY_LIMIT
        result['subcircuit_inventory_truncated'] = result['subcircuits'] > INVENTORY_LIMIT
    return result


def inspect_database(dbpath, output_dir):
    import klayout.db as db
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    lvs = db.LayoutVsSchematic()
    lvs.read(str(dbpath))
    xref = lvs.xref()
    require(xref is not None, 'Saved database has no comparison cross-reference')
    layout_netlist, reference_netlist = lvs.netlist(), lvs.reference
    counts, index = Counter(), []
    for number, pair in enumerate(xref.each_circuit_pair()):
        # Resolve graph objects from the owning netlists. Cross-reference objects
        # supply status/identity; the owning graph supplies live connectivity.
        raw_a, raw_b = pair.first(), pair.second()
        a = layout_netlist.circuit_by_name(raw_a.name) if raw_a is not None else None
        b = reference_netlist.circuit_by_name(raw_b.name) if raw_b is not None else None
        status = str(pair.status())
        counts[status] += 1
        detail = pair.status() != db.NetlistCrossReference.Match
        row = {'status': status, 'layout': circuit_info(a, detail), 'reference': circuit_info(b, detail)}
        if detail:
            row['pair_status_counts'], row['mismatch_samples'] = {}, {}
            methods = {'net': xref.each_net_pair, 'pin': xref.each_pin_pair,
                       'device': xref.each_device_pair, 'subcircuit': xref.each_subcircuit_pair}
            for kind, method in methods.items():
                totals, samples = Counter(), []
                for item in method(pair):
                    state = str(item.status())
                    totals[state] += 1
                    if item.status() == db.NetlistCrossReference.Match or len(samples) >= PER_KIND_LIMIT:
                        continue
                    def describe(obj, circuit):
                        if obj is None:
                            return None
                        if kind == 'pin':
                            return pin_info(obj, circuit)
                        if kind == 'device':
                            actual = circuit.device_by_id(obj.id())
                            require(actual is not None, f'Cannot resolve device {obj.id()} in {circuit.name}')
                            return device_info(actual)
                        if kind == 'subcircuit':
                            actual = circuit.subcircuit_by_id(obj.id())
                            require(actual is not None, f'Cannot resolve instance {obj.id()} in {circuit.name}')
                            return subcircuit_info(actual)
                        return net_info(obj)
                    samples.append({'status': state, 'layout': describe(item.first(), a),
                                    'reference': describe(item.second(), b)})
                row['pair_status_counts'][kind] = dict(sorted(totals.items()))
                row['mismatch_samples'][kind] = samples
            filename = f'circuit-{number:03d}.json'
            write_json(out/filename, row)
        else:
            filename = None
        index.append({'layout': a.name if a else None, 'reference': b.name if b else None,
                      'status': status, 'detail_file': filename,
                      'layout_inventory': row['layout'] if not detail else {k:v for k,v in row['layout'].items() if not k.endswith('inventory')} if row['layout'] else None,
                      'reference_inventory': row['reference'] if not detail else {k:v for k,v in row['reference'].items() if not k.endswith('inventory')} if row['reference'] else None})
    statuses = {(r[side] or '').upper(): r['status'] for r in index for side in ('layout','reference') if r[side]}
    for row in index:
        row['nonmatching_children'] = {}
        for side in ('layout','reference'):
            inv = row[side+'_inventory']
            if inv:
                row['nonmatching_children'][side] = {name: statuses.get(name.upper(), 'unpaired')
                    for name in inv['children'] if statuses.get(name.upper()) != 'Match'}
    result = {'status': 'LVS_INSPECTION_COMPLETE', 'comparison_reexecuted': False,
              'circuit_status_counts': dict(counts), 'circuits': index,
              'extraction_errors': [str(e) for e in lvs.each_error()],
              'limits': {'samples_per_circuit_per_kind': PER_KIND_LIMIT, 'device_or_instance_inventory': INVENTORY_LIMIT}}
    write_json(out/'index.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lvs_run', type=Path)
    args = parser.parse_args()
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Use the existing native Apple Silicon environment')
    import klayout.db as db
    require(db.__version__ == '0.30.9', 'Expected existing CP0C KLayout 0.30.9')
    root = Path(subprocess.check_output(['git','rev-parse','--show-toplevel'], text=True).strip()).resolve()
    results = root/'native/results'
    source = args.lvs_run.resolve()
    require(source.parent == results and source.name.startswith('cp0c-lvs-'), 'Use an existing native/results/cp0c-lvs-* directory')
    required = ['full/layout.lvsdb','full/audit.json','full/verdict.json','full/switches.json',
                'provenance.json','lvs-status.txt','reference-read-status.txt','preservation.txt']
    for name in required:
        require((source/name).is_file(), f'Missing completed-run input: {source/name}')
    require((source/'lvs-status.txt').read_text().strip() == 'LVS_REVIEW_REQUIRED', 'Expected the completed mismatching LVS attempt')
    require((source/'reference-read-status.txt').read_text().strip() == 'LVS_REFERENCE_READ_PASS', 'Complete-reference gate must have passed')
    require((source/'preservation.txt').read_text().strip() == 'All recorded input/tool/PDK hashes still match.', 'Source preservation gate missing')
    lock = results/'cp0c.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f'Another CP0C command may be running: inspect {lock}/owner.txt') from None
    run, preserved, status = None, {}, 1
    try:
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=lvs-inspect\n')
        run = Path(tempfile.mkdtemp(prefix='cp0c-lvs-inspect-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-', dir=results))
        print(f'Evidence: {run}\nVersion: {VERSION}\nReading saved LVS database; no extraction or comparison will run.', flush=True)
        (run/'source').mkdir()
        (run/'helpers').mkdir()
        for name in required:
            p = source/name
            preserved[str(p)] = sha256(p)
            if p.suffix != '.lvsdb':
                dst = run/'source'/name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
        for p in Path(__file__).parent.glob('cp0c-lvs-inspect.*'):
            if p.is_file():
                shutil.copy2(p, run/'helpers'/p.name)
        write_json(run/'provenance.json', {'version': VERSION, 'source': str(source), 'klayout': db.__version__,
                   'input_sha256': preserved, 'comparison_reexecuted': False})
        result = inspect_database(source/'full/layout.lvsdb', run/'circuits')
        original = json.loads((source/'full/audit.json').read_text())
        require(result['circuit_status_counts'] == original['circuit_status_counts'], 'Database and saved audit disagree')
        (run/'summary.txt').write_text('LVS_INSPECTION_COMPLETE\nCP0C_PENDING\n'+
            json.dumps(result['circuit_status_counts'])+'\n'+
            'Detailed records cover every nonmatching or skipped circuit. No verification settings or chip files changed.\n')
        print((run/'summary.txt').read_text(), flush=True)
        status = 0
    except (Exception, KeyboardInterrupt) as exc:
        if run:
            (run/'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        print(f'STOPPED: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
    finally:
        try:
            if run:
                try:
                    for p, h in preserved.items():
                        require(sha256(p) == h, f'Input changed during inspection: {p}')
                    (run/'preservation.txt').write_text('All inspected source files and the LVS database are unchanged.\n')
                except Exception as exc:
                    status = 1
                    with (run/'failure.txt').open('a') as f:
                        f.write(str(exc)+'\n')
                (run/'exit-status.txt').write_text(str(status)+'\n')
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                archive = Path(str(run)+'.tar.gz')
                with tarfile.open(archive, 'w:gz') as tf:
                    for p in sorted(run.rglob('*')):
                        if p.is_file():
                            tf.add(p, arcname=str(p.relative_to(run)), recursive=False)
                print(f'Upload: {archive}', flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True)
            lock.rmdir()
    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
