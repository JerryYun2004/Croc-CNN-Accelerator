#!/usr/bin/env python3
"""Read-only CP0C native LVS attempt using the reviewed GDS and powered netlist."""
import argparse
import collections
import datetime
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from cp0c_lvs_support import (require, sha256, read_cdl, make_stubs, expected_masters,
                              convert_json, smoke_layout)
from cp0c_lvs_runtime import klayout_environment, native_binary, yosys_runtime
from cp0c_lvs_cdl import translate_io, reference_expectations

VERSION = '1.2-legacy-cdl'
CROC = '29b2e748e72c592a5a895c2b11fa85bc9080f67a'
PDK5 = '43ac6216df29c043f97d462706f98a91fa70e3ea'
PDK7 = 'c1d6f49ea902a88e328dda022805da8887ea26e3'
NETLIST_SHA = '4cb924d046da4001d311607caaf6614d3c773d8d177e21046aeb9c3458d3f93d'
GDS_SHA = '4808589ebcb00c10d331854db001a7cfd7cb0cac2efe3847a83cad7a745a59fa'
SEALED_SHA = '7376b12941f284b6406f2e9729082c7e3c1532f76f08196e4767e55a3bae24cc'
CDL = [
    ('stdcell.cdl', 'IHP-GmbH/IHP-Open-PDK', 'ee974c3adc69d0f36adbf20577079f0df419d702',
     'ihp-sg13g2/libs.ref/sg13g2_stdcell/cdl/sg13g2_stdcell.cdl',
     '6f629dc6b0d21df83fba7deb462a11217dd4b0737e99cd18a0a05ab876667c9a'),
    ('sram.cdl', 'IHP-GmbH/IHP-Open-PDK', 'ee974c3adc69d0f36adbf20577079f0df419d702',
     'ihp-sg13g2/libs.ref/sg13g2_sram/cdl/RM_IHPSG13_1P_512x32_c2_bm_bist.cdl',
     '8c12371fc9839e797753ac9c228fca30ae03d3c0c7252b33dd4bc882f341f204'),
    ('io.cdl', 'IHP-GmbH/ihp-sg13cmos5l', '5958eb38973d5c56e7a26f65fc23d9f3a740ccd2',
     'libs.ref/sg13cmos5l_io/cdl/sg13cmos5l_io.cdl',
     '55368473d268802e83b7c2e4dca0420048c2f2e0dfd20572019f448e0c8f7938')]

def output(argv, cwd=None):
    return subprocess.check_output([str(x) for x in argv], cwd=cwd, text=True).strip()

def command(argv, cwd, env, log):
    print('+ ' + ' '.join(map(str, argv)), flush=True)
    with Path(log).open('w') as f:
        p = subprocess.Popen(list(map(str, argv)), cwd=cwd, env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for line in p.stdout:
                print(line, end='', flush=True)
                f.write(line)
                f.flush()
            return p.wait()
        except BaseException:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            raise

def manifest(path, preserve, base=None):
    lines = Path(path).read_text().splitlines()
    require(lines, f'Empty hash manifest: {path}')
    for line in lines:
        h, name = line.split('  ', 1)
        p = Path(name)
        if not p.is_absolute():
            p = Path(base or Path(path).parent) / p
        require(sha256(p) == h, f'Checksum differs: {p}')
        preserve[str(p.resolve())] = h

def snapshot_deck(source, dest, preserve, seen=None):
    """Copy only reachable includes, resolving each symlink without editing the PDK."""
    seen = set() if seen is None else seen
    if str(dest) in seen:
        return
    seen.add(str(dest))
    require(source.is_file(), f'Missing LVS deck include: {source}')
    preserve[str(source.resolve())] = sha256(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    for rel in re.findall(r'^\s*#\s*%include\s+(\S+)', source.read_text(), re.M):
        require(not Path(rel).is_absolute() and '..' not in Path(rel).parts, f'Unsafe include: {rel}')
        snapshot_deck(source.parent/rel, dest.parent/rel, preserve, seen)

def make_wrapper(path):
    Path(path).write_text('''# Native entry point: the included pinned PDK deck is unchanged.
threads(2)
# %include sg13cmos5l.lvs
require 'json'
cp0_counts = lambda do |nl|
  result = {circuits: 0, devices: 0, subcircuits: 0}
  nl.each_circuit do |c|
    result[:circuits] += 1
    c.each_device { |_d| result[:devices] += 1 }
    c.each_subcircuit { |_s| result[:subcircuits] += 1 }
  end
  result
end
File.write($cp0_verdict, JSON.pretty_generate({
  success: !!success,
  layout: cp0_counts.call(netlist),
  reference: cp0_counts.call(schematic),
  top: $topcell,
  comparison_performed: true,
  strict_top_ports: true,
  tap_device_extraction: !DISABLE_TAP_EXTRACTION,
  circuit_blackboxes: []
}) + "\\n")
puts 'CP0C_LVS_DECK_COMPLETE'
''')

def run_lvs(tool, deck, layout, reference, top, run, env, worker, taps=False):
    run.mkdir(parents=True, exist_ok=True)
    switches = {'input': str(layout), 'schematic': str(reference), 'topcell': top,
                'report': str(run/'layout.lvsdb'), 'log': str(run/'deck.log'),
                'target_netlist': str(run/'extracted.cir'), 'cp0_verdict': str(run/'verdict.json'),
                'run_mode': 'deep', 'net_only': 'false', 'no_simplify': 'false',
                'top_lvl_pins': 'true', 'disable_tap_extraction': 'false' if taps else 'true',
                'ignore_top_ports_mismatch': 'false', 'implicit_nets': '',
                'combine_devices': 'false', 'purge': 'false', 'purge_nets': 'false'}
    (run/'switches.json').write_text(json.dumps(switches, indent=2) + '\n')
    argv = [tool]
    for k, v in switches.items():
        argv += ['-v', f'{k}={v}']
    argv.append(deck)
    rc = command(argv, run, klayout_environment(tool, env), run/'console.log')
    (run/'process-exit.txt').write_text(str(rc) + '\n')
    require(rc == 0, f'LVS process failed; inspect {run}/console.log')
    require((run/'verdict.json').is_file() and (run/'layout.lvsdb').is_file(), 'LVS did not complete comparison')
    require(command([sys.executable, worker, 'audit', run/'layout.lvsdb', run/'verdict.json',
                     run/'audit.json', top], run, env, run/'audit.log') == 0, 'LVS database audit failed')
    return json.loads((run/'audit.json').read_text())

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('gds_run', type=Path)
    args = ap.parse_args()
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Native Apple Silicon macOS is required')
    import klayout.db as db
    require(db.__version__ == '0.30.9', 'Use the completed CP0C KLayout Python environment')
    root = Path(output(['git', 'rev-parse', '--show-toplevel'])).resolve()
    results = root/'native/results'
    source = args.gds_run.resolve()
    require(source.parent == results and source.name.startswith('cp0c-gds-drc-'), 'Use the reviewed GDS/DRC directory')
    here = Path(__file__).resolve().parent
    toolbase = Path.home()/'tools/croc-klayout-0.30.9'
    tool = toolbase/'install/strmrun'
    yosys = Path.home()/'tools/croc-oss-cad-suite-2025-12-30/oss-cad-suite/bin/yosys'
    require((toolbase/'READY').is_file() and tool.is_file(), 'Completed CP0C KLayout installation required')
    require(yosys.is_file(), 'The CP0B native Yosys installation is missing')
    lock = results/'cp0c.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f'Another CP0C command may be running; inspect {lock}/owner.txt') from None
    run = None
    preserve = {}
    status = 1
    try:
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=lvs\n')
        run = Path(tempfile.mkdtemp(prefix='cp0c-lvs-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-', dir=results))
        for name in ('input', 'reports', 'logs', 'helpers', 'reference', 'deck', 'smoke', 'full'):
            (run/name).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nOne LVS process at a time; two extraction threads.', flush=True)
        for pattern in ('cp0c-lvs*', 'cp0c_lvs*'):
            for p in here.glob(pattern):
                if p.is_file():
                    shutil.copy2(p, run/'helpers'/p.name)
                    preserve[str(p)] = sha256(p)
        require(shutil.disk_usage(results).free >= 6*1024**3, 'At least 6 GiB free disk space required')
        for filename, expected in [('exit-status.txt', '0'), ('export-status.txt', 'GDS_EXPORT_VALIDATED'),
                                   ('drc-status.txt', 'INTERCONNECT_DRC_CLEAN')]:
            require((source/filename).read_text().strip() == expected, f'GDS checkpoint gate failed: {filename}')
        require((source/'preservation.txt').read_text().strip() == 'All recorded candidate/tool/PDK hashes still match.', 'GDS preservation audit missing')
        manifest(source/'source.sha256', preserve)
        manifest(source/'output.sha256', preserve)
        manifest(toolbase/'runtime.sha256', preserve, toolbase)
        gds = source/'out/croc.gds.gz'
        require(sha256(gds) == GDS_SHA and sha256(source/'out/croc.sealed.gds.gz') == SEALED_SHA,
                'This package targets the reviewed October 19 GDS exports')
        provenance = json.loads((source/'provenance.json').read_text())
        require(provenance['croc_pin'] == CROC and provenance['pdk5_pin'] == PDK5
                and provenance['pdk7_pin'] == PDK7, 'GDS checkpoint source pins differ')
        internal = Path(provenance['source']).resolve()
        require(internal.parent == results and internal.name.startswith('cp0c-internal-close-'), 'Unexpected implementation source')
        netlist = internal/'eco/out/croc_internal_lvs.v'
        require(sha256(netlist) == NETLIST_SHA, 'Saved powered netlist differs from the reviewed candidate')
        preserve[str(netlist)] = NETLIST_SHA
        preserve[str(yosys)] = sha256(yosys)
        # Keep the launcher's bytes even if runtime validation stops here.
        if yosys.read_bytes().startswith(b'#!'):
            shutil.copy2(yosys, run/'reports/yosys-launcher.sh')
        runtime = {'klayout': native_binary(tool), 'yosys': yosys_runtime(yosys)}
        (run/'reports/tool-runtime.json').write_text(json.dumps(runtime, indent=2)+'\n')
        for entry in [runtime['klayout'], *runtime['yosys']['files']]:
            preserve[entry['resolved_path']] = entry['sha256']
        print('NATIVE_YOSYS_RUNTIME_PASS: launcher and compiled payload verified.', flush=True)
        shutil.copy2(netlist, run/'input/croc_internal_lvs.v')
        for f in ('summary.md', 'provenance.json', 'preservation.txt', 'output.sha256'):
            shutil.copy2(source/f, run/'reference'/f)
        assembly = json.loads((source/'reports/assembly.json').read_text())
        require(assembly['components'] == 113138 and sum(assembly['masters'].values()) == 113138, 'Assembly inventory differs')
        require(assembly['gds_sha256'] == GDS_SHA, 'Assembly report/GDS identity mismatch')
        masters = expected_masters(assembly)
        for repo, pin in [(root/'ihp13/sg13cmos5l', PDK5), (root/'ihp13/sg13g2', PDK7)]:
            require(output(['git', 'rev-parse', 'HEAD'], repo) == pin, f'PDK pin differs: {repo}')
            require(not output(['git', 'status', '--porcelain', '--untracked-files=no'], repo), f'Modified PDK: {repo}')
            require(not any(s[:1] in '-+U' for s in output(['git', 'submodule', 'status', '--recursive'], repo).splitlines()), 'PDK nested submodule differs')
        env = os.environ.copy()
        env.update(CP0L_PY=sys.executable, CP0L_ENV=str(run/'reports/environment.json'))
        shell = '''set -euo pipefail
source native/env.sh
export CROC_PDK=sg13cmos5l PROJ_NAME=croc TOP_DESIGN=croc_chip
unset CROC_SKIP_TECH_SETUP
source env.sh
"$CP0L_PY" - <<'PY'
import json,os
names=['CROC_ROOT','CROC_PDK','PDK_ROOT','KLAYOUT_PATH','CROC_TECHNOLOGY_VIEW','PATH']
with open(os.environ['CP0L_ENV'],'w') as f: json.dump({k:os.environ[k] for k in names},f,indent=2)
PY
'''
        require(command(['/bin/bash', '-c', shell], root, env, run/'logs/environment.log') == 0, 'Environment initialization failed')
        env.update(json.loads((run/'reports/environment.json').read_text()))
        require(env['CROC_TECHNOLOGY_VIEW'] == 'public mirror', 'Expected reviewed public technology mirror')
        (run/'preflight-status.txt').write_text('LVS_PREFLIGHT_PASS\n')
        native_deck = Path(env['KLAYOUT_PATH'])/'tech/lvs/sg13cmos5l.lvs'
        snapshot_deck(native_deck, run/'deck/sg13cmos5l.lvs', preserve)
        make_wrapper(run/'deck/cp0c.lvs')
        paths = []
        for name, repo, ref, rel, digest in CDL:
            dst = run/'input'/name
            url = f'https://raw.githubusercontent.com/{repo}/{ref}/{rel}'
            require(command(['curl', '--fail', '--location', '--retry', '2', '--silent', '--show-error',
                             '--connect-timeout', '20', '--max-time', '180', url, '--output', dst],
                            root, env, run/'logs'/f'{name}.download.log') == 0, f'CDL download failed: {name}')
            require(sha256(dst) == digest, f'CDL checksum mismatch: {name}')
            paths.append(dst)
        cells = read_cdl(paths)
        make_stubs(cells, masters, run/'input/interfaces.v')
        script = run/'input/convert.ys'
        # Fixed relative paths avoid interpolation into Yosys command syntax.
        script.write_text('read_verilog -lib input/interfaces.v\nread_verilog input/croc_internal_lvs.v\nhierarchy -check -top croc_chip\nwrite_json input/reference.json\n')
        require(command([yosys, '-Q', '-T', '-s', 'input/convert.ys'], run, env, run/'logs/yosys-convert.log') == 0,
                'Powered Verilog parsing failed')
        convert_json(run/'input/reference.json', cells, masters, run/'input/top.cdl', run/'reports')
        (run/'reference-status.txt').write_text('LVS_REFERENCE_VALIDATED\n')
        reference = run/'input/reference.cdl'
        translation = translate_io(run/'input/io.cdl', run/'input/io-reader.cdl', run/'reports/cdl-translation.json')
        reader_paths = [run/'input/stdcell.cdl', run/'input/sram.cdl', run/'input/io-reader.cdl']
        reference.write_text('* Pinned library models followed by the converted top.\n' +
                             ''.join(p.read_text()+'\n' for p in reader_paths) + (run/'input/top.cdl').read_text())
        reference_expectations([*reader_paths, run/'input/top.cdl'], run/'reports/reference-expectations.json')
        # Confirm the library GDS used in the completed export is still identical.
        stdgds = source/'input/sg13g2_stdcell.gds'
        stdentry = next(x for x in assembly['libraries'] if Path(x['file']).name == stdgds.name)
        require(sha256(stdgds) == stdentry['sha256'], 'Saved standard-cell GDS changed')
        preserve[str(stdgds)] = stdentry['sha256']
        for p in (run/'input').iterdir():
            if p.is_file():
                preserve[str(p)] = sha256(p)
        (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
        (run/'provenance.json').write_text(json.dumps({'version': VERSION, 'gds_run': str(source),
            'internal_run': str(internal), 'gds': str(gds), 'gds_sha256': GDS_SHA,
            'netlist_sha256': NETLIST_SHA, 'pdk5': PDK5, 'pdk7': PDK7,
            'cdl_sources': CDL, 'yosys': output([yosys, '-V']), 'klayout': db.__version__,
            'seal_excluded': True, 'tap_device_extraction': True, 'smoke_tap_device_extraction': False,
            'cdl_translation': translation['counts'], 'circuit_blackboxes': [],
            'global_implicit_connections_added': [], 'original_io_load_pf': 15}, indent=2)+'\n')
        smoke_layout(stdgds, run/'smoke/inverter.gds')
        good = run/'smoke/good.cdl'
        bad = run/'smoke/bad.cdl'
        require(command([tool, '-v', f'reader_dir={run}/deck/rule_decks',
                         '-v', f'schematic={reference}', '-v', f'expectations={run}/reports/reference-expectations.json',
                         '-v', f'report={run}/reports/reference-read.json', here/'cp0c_lvs_read_reference.rb'],
                        run, klayout_environment(tool, env), run/'logs/reference-read.log') == 0,
                'Complete-reference read failed; inspect logs/reference-read.log')
        read_report = json.loads((run/'reports/reference-read.json').read_text())
        require(read_report['status'] == 'LVS_REFERENCE_READ_PASS'
                and len(read_report['translated_device_checks']) == 93
                and read_report['devices_per_definition'] == 1616,
                'Complete-reference inventory differs from the pinned libraries')
        (run/'reference-read-status.txt').write_text('LVS_REFERENCE_READ_PASS\n')
        inv = cells['SG13G2_INV_1']
        require(len(inv['body']) == 2 and all(x[0].upper() == 'M' for x in inv['body']), 'Inverter smoke model changed')
        good.write_text('* Positive real-layout LVS smoke\n.SUBCKT '+inv['name']+' '+' '.join(inv['pins'])+'\n'+'\n'.join(inv['body'])+'\n.ENDS\n')
        # Remove the PMOS only in the negative-test reference; chip data is untouched.
        bad.write_text('* Deliberately wrong negative smoke\n.SUBCKT '+inv['name']+' '+' '.join(inv['pins'])+'\n'+inv['body'][0]+'\n.ENDS\n')
        worker = here/'cp0c_lvs_support.py'
        good_result = run_lvs(tool, run/'deck/cp0c.lvs', run/'smoke/inverter.gds', good, inv['name'], run/'smoke/good', env, worker)
        require(good_result['status'] == 'LVS_MATCH', 'Positive real-layout LVS smoke did not match; full-chip run was not started')
        bad_result = run_lvs(tool, run/'deck/cp0c.lvs', run/'smoke/inverter.gds', bad, inv['name'], run/'smoke/bad', env, worker)
        require(bad_result['deck_verdict']['success'] is False and bad_result['layout_devices_per_definition'] > 0,
                'Negative LVS smoke was not rejected; full-chip run was not started')
        (run/'smoke/status.txt').write_text('LVS_SMOKE_PASS\n')
        print('LVS_SMOKE_PASS: real inverter matched; missing-PMOS reference rejected.', flush=True)
        result = run_lvs(tool, run/'deck/cp0c.lvs', gds, reference, 'croc_chip', run/'full', env, worker, taps=True)
        outcome = result['status']
        (run/'lvs-status.txt').write_text(outcome+'\n')
        (run/'summary.md').write_text('\n'.join([
            'CP0C LVS ATTEMPT COMPLETE', outcome, 'CP0C_PENDING', '',
            f'Compared top: croc_chip; {sum(masters.values())} reference cell instances.',
            'Real-layout positive and negative LVS smoke checks passed.',
            'Original GDS, netlists, PDK, tools and 15 pF constraints preserved.',
            'Pinned CMOS5L transistor extraction; standard cells, I/O and SRAM models included.',
            'No circuit blackboxes or added global implicit supply connections.',
            'Full-chip tap-device extraction enabled; all 64 explicit I/O tap cards retained.',
            '29 legacy res_rppd cards translated to the pinned reader rppd syntax; 93 device mappings audited.',
            'Seal ring is outside this chip-netlist comparison. Remaining DRC and timing items stay open.',
            f'Circuit comparison statuses: {result["circuit_status_counts"]}',
            'See full/audit.json for circuit results and bounded mismatch samples.', ''])+'\n')
        print((run/'summary.md').read_text(), flush=True)
        status = 0 if outcome == 'LVS_MATCH' else 2
    except (Exception, KeyboardInterrupt) as exc:
        if run:
            (run/'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        print(f'STOPPED: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
    finally:
        try:
            if run:
                try:
                    for p,h in preserve.items():
                        require(sha256(p) == h, f'Preservation check failed: {p}')
                    (run/'preservation.txt').write_text('All recorded input/tool/PDK hashes still match.\n')
                except Exception as exc:
                    status = 1
                    with (run/'failure.txt').open('a') as f:
                        f.write(str(exc)+'\n')
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                (run/'exit-status.txt').write_text(str(status)+'\n')
                if not (run/'lvs-status.txt').exists():
                    (run/'lvs-status.txt').write_text('LVS_INCOMPLETE\n')
                (run/'warnings.txt').write_text('\n'.join(
                    f'{p.relative_to(run)}: {line}' for p in run.rglob('*.log')
                    for line in p.read_text(errors='replace').splitlines()
                    if re.search(r'\b(warning|error|fatal|exception)\b', line, re.I))+'\n')
                evidence = run.with_suffix('.tar.gz')
                with tarfile.open(evidence, 'w:gz') as tar:
                    for p in sorted(run.rglob('*')):
                        rel = p.relative_to(run)
                        if p.is_file() and rel.parts[0] not in ('input', 'deck') and p.suffix not in ('.lvsdb', '.pyc'):
                            # Include extracted SPICE and audits for diagnosis; no full-chip GDS or raw database.
                            tar.add(p, arcname=str(rel), recursive=False)
                print(f'Upload: {evidence}\nFull LVS database: {run}/full/layout.lvsdb', flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True)
            lock.rmdir()
    return status

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
