#!/usr/bin/env python3
"""Two isolated native extraction runs, using the existing CP0C installation."""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

from cp0c_lvs_probe_support import (CELL, require, sha256, write_json,
    prepare_geometry, dummy_probe, extraction_inventory)

VERSION = '1.0-isolated-library-probes'
IO_GDS_SHA = '711e22c0e86239fde11973e6c965b57742b04c94fee33b4b17bb60f1b190f6b3'
CHIP_GDS_SHA = '4808589ebcb00c10d331854db001a7cfd7cb0cac2efe3847a83cad7a745a59fa'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lvs_run',type=Path)
    args = parser.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64', 'Use the existing native Apple Silicon environment')
    import klayout.db as db
    require(db.__version__=='0.30.9', 'Expected the existing CP0C KLayout 0.30.9')
    root = Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve()
    results = root/'native/results'; source = args.lvs_run.resolve()
    require(source.parent==results and source.name=='cp0c-lvs-20261020-163415-ekczadv6',
            'This experiment targets the reviewed October 20 16:34 LVS run')
    here = Path(__file__).resolve().parent
    lock = results/'cp0c.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f'Another CP0C command may be running; inspect {lock}/owner.txt') from None
    run = None; status = 1; preserve = {}
    try:
        (lock/'owner.txt').write_text(f'PID={os.getpid()}\nstage=lvs-probe\n')
        run = Path(tempfile.mkdtemp(prefix='cp0c-lvs-probe-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=results))
        for name in ('helpers','input','deck','reports','sram-dummy','io-original','io-marker-only'):
            (run/name).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nTwo small cell extractions; no full-chip rerun or installation.',flush=True)
        require(shutil.disk_usage(results).free >= 1024**3, 'At least 1 GiB free space required')
        for name in ('cp0c-lvs-probe.py','cp0c-lvs-probe.sh','cp0c_lvs_probe_support.py','cp0c-lvs-dummy-snapshot.json'):
            shutil.copy2(here/name,run/'helpers'/name)
            preserve[str(here/name)] = sha256(here/name)
        for name,expected in [('lvs-status.txt','LVS_REVIEW_REQUIRED'),
                             ('reference-read-status.txt','LVS_REFERENCE_READ_PASS'),
                             ('preservation.txt','All recorded input/tool/PDK hashes still match.')]:
            require((source/name).read_text().strip()==expected, f'Unexpected source gate: {name}')
        provenance = json.loads((source/'provenance.json').read_text())
        require(provenance['version']=='1.2-legacy-cdl' and provenance['gds_sha256']==CHIP_GDS_SHA,
                'Source version/candidate differs')
        # Validate before importing any saved runner modules. All are the exact
        # helper snapshots whose original bytes were recorded by the source run.
        manifest = {}
        for line in (source/'source.sha256').read_text().splitlines():
            digest,name = line.split('  ',1)
            manifest[name]=digest
            require(sha256(name)==digest, f'Source manifest changed: {name}')
            preserve[name]=digest
        source_hashes = set(manifest.values())
        for name in ('cp0c-lvs.py','cp0c_lvs_support.py','cp0c_lvs_runtime.py','cp0c_lvs_cdl.py'):
            path = source/'helpers'/name
            require(sha256(path) in source_hashes, f'Saved helper is not the recorded version: {name}')
            preserve[str(path)] = sha256(path)
        sys.path.insert(0,str(source/'helpers'))
        spec = importlib.util.spec_from_file_location('saved_lvs_runner',source/'helpers/cp0c-lvs.py')
        runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        require(runner.VERSION=='1.2-legacy-cdl', 'Unexpected saved runner')
        from cp0c_lvs_runtime import native_binary
        from cp0c_lvs_support import read_cdl
        toolbase = Path.home()/'tools/croc-klayout-0.30.9'
        tool = toolbase/'install/strmrun'
        runtime = native_binary(tool)
        runner.manifest(toolbase/'runtime.sha256',preserve,toolbase)
        write_json(run/'reports/runtime.json',runtime)
        preserve[runtime['resolved_path']]=runtime['sha256']
        for path in sorted((source/'deck').rglob('*')):
            if not path.is_file() or path.name=='cp0c.lvs':
                continue
            require(sha256(path) in source_hashes, f'Saved deck differs from pinned sources: {path}')
            preserve[str(path)] = sha256(path)
            dest = run/'deck'/path.relative_to(source/'deck'); dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,dest)
        # Original pinned deck and strict comparison options remain unchanged.
        runner.make_wrapper(run/'deck/cp0c.lvs')
        gds_run = Path(provenance['gds_run']).resolve()
        require(gds_run.parent==results and gds_run.name.startswith('cp0c-gds-drc-'), 'Unexpected GDS source')
        io_gds = gds_run/'input/sg13cmos5l_io.gds'; chip_gds = Path(provenance['gds'])
        for path,digest in [(io_gds,IO_GDS_SHA),(chip_gds,CHIP_GDS_SHA)]:
            require(sha256(path)==digest, f'GDS identity differs: {path}')
            preserve[str(path)]=digest
        geometry = prepare_geometry(io_gds,chip_gds,run/'input')
        print('GEOMETRY_CONFIRMED: vendor and assembled cell match; one 1 x 2 um PolyRes marker in disposable copy.',flush=True)
        cells = read_cdl([source/'input/io-reader.cdl']); cell = cells[CELL.upper()]
        require(len(cell['body'])==4 and all(not s.upper().startswith('X') for s in cell['body']), 'Unexpected isolated reference')
        reference = run/'input/secondary.cdl'
        reference.write_text('* Isolated reference, unchanged translated cards.\n.SUBCKT '+cell['name']+' '+' '.join(cell['pins'])+'\n'+'\n'.join(cell['body'])+'\n.ENDS\n')
        dummy = dummy_probe(json.loads((here/'cp0c-lvs-dummy-snapshot.json').read_text()),run/'sram-dummy')
        write_json(run/'provenance.json',{'version':VERSION,'source':str(source),'source_provenance':provenance,
            'scope':'Isolated diagnostic experiments only','source_chip_modified':False,
            'full_chip_comparison_performed':False,'original_io_load_pf':15,
            'geometry_experiment':geometry,'sram_experiment':'Inspector JSON graph reconstruction'})
        reports = {}
        for label,gds in [('io-original','original.gds'),('io-marker-only','marker-only.gds')]:
            print(f'PROBE: {label}',flush=True)
            result = runner.run_lvs(tool,run/'deck/cp0c.lvs',run/'input'/gds,reference,CELL,
                                  run/label,os.environ.copy(),source/'helpers/cp0c_lvs_support.py',taps=True)
            inventory = extraction_inventory(run/label/'layout.lvsdb')
            inventory['isolated_lvs_status']=result['status']
            write_json(run/label/'inventory.json',inventory); reports[label]=inventory
        a,b = reports['io-original'],reports['io-marker-only']
        resistors = [d for d in b['devices'] if d['model'].lower()=='rppd']
        marker_confirmed = (a['models'].get('rppd',0)==0 and a['core_pad_merged']
            and b['models'].get('rppd',0)==1 and b['core_pad_distinct']
            and len(resistors)==1 and abs(resistors[0]['parameters'].get('w',0)-1.0)<1e-9
            and abs(resistors[0]['parameters'].get('l',0)-2.0)<1e-9
            and not a['extraction_errors'] and not b['extraction_errors'])
        outcome = 'LVS_LIBRARY_PROBES_COMPLETE' if marker_confirmed and dummy['status']=='SRAM_DUMMY_MODEL_PROBE_CONFIRMED' else 'LVS_LIBRARY_PROBES_REVIEW_REQUIRED'
        write_json(run/'reports/conclusions.json',{'outcome':outcome,
            'resistor_marker_hypothesis_confirmed':marker_confirmed,'dummy_probe':dummy,
            'full_chip_lvs':'LVS_REVIEW_REQUIRED','checkpoint':'CP0C_PENDING',
            'remaining_io_issues':['Tap perimeter convention mismatch','Substrate interface and hierarchical split pins'],
            'next_decision':'Review the isolated results before promoting any compatibility change.'})
        (run/'summary.txt').write_text(outcome+'\nCP0C_PENDING\n'
            'Original full-chip LVS result is unchanged: LVS_REVIEW_REQUIRED.\n'
            f'PolyRes marker hypothesis confirmed: {marker_confirmed}\n'
            f'SRAM dummy graph probe: {dummy["status"]}\n'
            'Isolated cell comparisons may still mismatch on taps/substrate. This is not a chip LVS pass.\n')
        print((run/'summary.txt').read_text(),flush=True)
        status = 0 if outcome=='LVS_LIBRARY_PROBES_COMPLETE' else 2
    except (Exception,KeyboardInterrupt) as exc:
        if run:
            (run/'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        print(f'STOPPED: {type(exc).__name__}: {exc}',file=sys.stderr,flush=True)
    finally:
        try:
            if run:
                changed = [p for p,h in preserve.items() if not Path(p).is_file() or sha256(p)!=h]
                if changed:
                    status=1; write_json(run/'preservation-failure.json',changed)
                else:
                    (run/'preservation.txt').write_text('All recorded source files, chip data, PDK and tools are unchanged.\n')
                (run/'candidate-status.txt').write_text('CP0C_PENDING\n')
                (run/'exit-status.txt').write_text(str(status)+'\n')
                (run/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted(preserve.items())))
                archive=run.with_suffix('.tar.gz')
                with tarfile.open(archive,'w:gz') as tar:
                    for p in sorted(run.rglob('*')):
                        rel=p.relative_to(run)
                        if p.is_file() and rel.parts[0]!='deck' and p.suffix!='.pyc':
                            tar.add(p,arcname=str(rel),recursive=False)
                print(f'Upload: {archive}',flush=True)
        finally:
            (lock/'owner.txt').unlink(missing_ok=True); lock.rmdir()
    return status

if __name__=='__main__':
    try:
        sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr)
        sys.exit(1)
