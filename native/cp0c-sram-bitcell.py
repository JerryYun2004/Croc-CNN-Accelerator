#!/usr/bin/env python3
"""Run three bounded native extractions and isolated reference controls."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

from cp0c_sram_bitcell_support import prepare, evaluate, audit_databases, require, sha, write_json

VERSION = '1.1-active-device-classes'
FILES = ('cp0c-sram-bitcell.py','cp0c-sram-bitcell.sh','cp0c-sram-bitcell.lvs',
         'cp0c-sram-bitcell-pins.json','cp0c_sram_bitcell_support.py')

def native_environment(tool):
    require(tool.is_file() and os.access(tool,os.X_OK), 'Completed native strmrun is missing')
    description = subprocess.check_output(['file','-b','-L',str(tool)],text=True).strip()
    require('Mach-O' in description and 'arm64' in description, 'Expected native arm64 strmrun')
    require(':' not in str(tool.parent), 'Unsupported colon in install directory')
    for name in ('bd','db','pex','tl','gsi','lib','rdb','lym','rba','drc','lvs','pya'):
        require((tool.parent/f'libklayout_{name}.0.dylib').is_file(),f'Missing KLayout library: {name}')
    env = dict(os.environ, DYLD_LIBRARY_PATH=str(tool.parent), OMP_NUM_THREADS='2')
    return env, description

def bounded_command(argv, cwd, env, log, timeout=120):
    """Kill the entire child process group on timeout/interruption."""
    started = time.monotonic()
    with Path(log).open('w') as output:
        p = subprocess.Popen(list(map(str,argv)),cwd=cwd,env=env,
                             stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            while True:
                remaining = timeout-(time.monotonic()-started)
                if remaining <= 0:
                    raise TimeoutError(f'Native extraction exceeded {timeout}s; see {log}')
                try:
                    rc = p.wait(timeout=min(20,remaining)); break
                except subprocess.TimeoutExpired:
                    print(f'  Native probe running ({int(time.monotonic()-started)}s)...',flush=True)
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGKILL); p.wait()
            raise
    require(rc == 0, f'Native process exited {rc}; see {log}')
    return time.monotonic()-started

def archive(run):
    path = run.with_suffix('.tar.gz')
    with tarfile.open(path,'w:gz') as tar:
        for p in sorted(run.rglob('*')):
            rel = p.relative_to(run)
            if p.is_file() and rel.parts[0] != 'deck' and '__pycache__' not in rel.parts:
                tar.add(p,arcname=str(Path(run.name)/rel),recursive=False)
    return path

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('repaired_run',type=Path); args = ap.parse_args()
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64',
            'Run in the existing native Apple Silicon environment')
    import klayout.db as db
    require(db.__version__ == '0.30.9', 'Expected the existing KLayout 0.30.9 Python environment')
    root = Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results = root/'native/results'; source = args.repaired_run.resolve()
    here = Path(__file__).resolve().parent
    pins = json.loads((here/'cp0c-sram-bitcell-pins.json').read_text())
    require(source.parent == results and source.name == pins['source_run'],
            'Use the reviewed repaired SRAM power run from October 20 at 23:39')
    lock = results/'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f'Another CP0C command may be running; inspect {lock}/owner.txt') from None
    run = None; preserve = {}; status = 1
    try:
        (lock/'owner.txt').write_text(f'pid={os.getpid()} stage=sram-bitcell\n')
        run = Path(tempfile.mkdtemp(prefix='cp0c-sram-bitcell-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for name in ('helpers','reports','deck','input','probes'):(run/name).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nThree isolated extractions; two threads; 120 seconds maximum per extraction.',flush=True)
        require(shutil.disk_usage(results).free >= 1024**3, 'At least 1 GiB free space required')
        def checked(path, expected=None):
            p = Path(path); require(p.is_file(),f'Missing reviewed input: {p}')
            actual = sha(p)
            require(expected is None or actual == expected,f'Checksum differs: {p}')
            preserve[str(p.resolve())] = actual
            return p
        for name in FILES:
            shutil.copy2(checked(here/name),run/'helpers'/name)
        prov = json.loads(checked(source/'provenance.json').read_text())
        result = json.loads(checked(source/'reports/result.json').read_text())
        require(prov.get('version') == '1.1-exact-instance-names' and prov.get('original_io_load_pf') == 15,
                'Unexpected repaired-run provenance')
        require(result.get('sram_power_repair') == 'SRAM_PG_REPAIR_VERIFIED', 'SRAM power repair has not passed')
        checked(source/'full/layout.lvsdb',pins['source_database'])
        original = Path(prov['original_lvs']).resolve(); gds_run = Path(prov['original_gds']).resolve()
        require(original.parent == results and gds_run.parent == results,'Referenced inputs are outside this repository')
        cdl = checked(original/'input/sram.cdl',pins['sram_cdl'])
        gds = checked(gds_run/'input/RM_IHPSG13_1P_512x32_c2_bm_bist.gds',pins['sram_gds'])
        for rel, digest in pins['lvs_deck'].items():
            src = checked(source/'deck/lvs'/rel,digest)
            dst = run/'deck'/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(src,dst)
        shutil.copyfile(here/'cp0c-sram-bitcell.lvs',run/'deck/cp0c-sram-bitcell.lvs')
        toolbase = Path.home()/'tools/croc-klayout-0.30.9'
        tool = checked(toolbase/'install/strmrun',pins['strmrun'])
        manifest = checked(toolbase/'runtime.sha256')
        rows = manifest.read_text().splitlines(); require(rows,'Empty native runtime manifest')
        for line in rows:
            digest,name = line.split('  ',1); p = Path(name)
            checked(p if p.is_absolute() else toolbase/p,digest)
        env,description = native_environment(tool)
        fixtures = prepare(gds,cdl,run/'input')
        write_json(run/'provenance.json',{
            'version':VERSION,'source_run':str(source),'sram_gds':str(gds),'sram_cdl':str(cdl),
            'sram_gds_sha256':pins['sram_gds'],'sram_cdl_sha256':pins['sram_cdl'],
            'klayout':db.__version__,'tool':str(tool),'tool_description':description,
            'source_geometry_modified':False,'copied_bitcell_geometry_modified':False,
            'leaf_fixture_changes':'Only A/B text labels added to two isolated resistor copies',
            'reference_translation':'Only isolated bitcell R0/R1/R2 models and W; MOS cards and pin order preserved',
            'implicit_connections':[],'blackboxes':[],'original_io_load_pf':15,
            'full_sram_lvs_performed':False,'full_chip_lvs_performed':False,'cp0c':'PENDING'})
        reports = {}
        for fixture in fixtures:
            name = fixture['name']; probe = run/'probes'/name; probe.mkdir()
            config = probe/'config.json'; write_json(config,fixture)
            switches = {
                'input':fixture['layout'],'topcell':fixture['top'],
                'report':str(probe/'last-case.lvsdb'),'log':str(probe/'deck.log'),
                'target_netlist':str(probe/'extracted.cir'),
                'cp0b_config':str(config),'cp0b_result':str(probe/'result.json'),
                'run_mode':'flat','net_only':'true','no_simplify':'true','top_lvl_pins':'true',
                'disable_tap_extraction':'false','ignore_top_ports_mismatch':'false',
                'implicit_nets':'','combine_devices':'false','purge':'false','purge_nets':'false',
                'no_series_res':'true','no_parallel_res':'true'}
            write_json(probe/'switches.json',switches)
            argv = [tool]
            for key,value in switches.items(): argv += ['-v',f'{key}={value}']
            argv.append(run/'deck/cp0c-sram-bitcell.lvs')
            print(f'PROBE: {name}',flush=True)
            elapsed = bounded_command(argv,probe,env,probe/'console.log')
            require('CP0B_NATIVE_BITCELL_PROBE_COMPLETE' in (probe/'console.log').read_text(),
                    f'Native probe did not complete: {probe}')
            report = json.loads((probe/'result.json').read_text())
            write_json(probe/'database-audit.json',audit_databases(probe,report,fixture))
            report['elapsed_seconds'] = elapsed; reports[name] = report
            write_json(probe/'result.json',report)
            print(f'  {name}: '+', '.join(f"{c['variant']}={c['strict_match']}" for c in report['cases']),flush=True)
        conclusion = evaluate(reports)
        write_json(run/'reports/conclusion.json',conclusion)
        (run/'status.txt').write_text(conclusion['status']+'\nCP0C_PENDING\n')
        print((run/'status.txt').read_text(),flush=True)
        status = 0 if conclusion['status'] in ('SRAM_BITCELL_COMPATIBILITY_VERIFIED',
                    'SRAM_BITCELL_BOUNDARY_REVIEW_REQUIRED') else 2
    except (Exception,KeyboardInterrupt) as exc:
        message = f'{type(exc).__name__}: {exc}'
        if run: (run/'failure.txt').write_text(message+'\n')
        print('STOPPED: '+message,file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed = [p for p,h in preserve.items() if not Path(p).is_file() or sha(p) != h]
                write_json(run/'reports/preservation.json',{'unchanged':not changed,'changed':changed})
                if changed: status = 1
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                (run/'exit-status.txt').write_text(str(status)+'\n')
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                print(f'Upload: {archive(run)}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True); lock.rmdir()
    return status

if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr); sys.exit(1)
