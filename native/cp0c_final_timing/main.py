#!/usr/bin/env python3
"""Native final-database STA and diagnostic I/O-load characterization."""
import argparse
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from support import (COUNTS, SLACKS, SAMPLES, require, sha, write_json, rows, table, metrics,
                     checked_snapshot, compare_metrics, audit_annotation, sdc_semantics,
                     audit_original_sdc, pad_result, sampled_range, spef_stats)

VERSION = '1.0-final-db-load-sweep'

def bounded(argv, cwd, env, log, limit):
    start = time.monotonic(); peak = 0; missed = 0
    with log.open('w') as f:
        p = subprocess.Popen(list(map(str, argv)), cwd=cwd, env=env, stdout=f,
                             stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while True:
                remaining = limit - (time.monotonic() - start)
                if remaining <= 0:
                    raise TimeoutError(f'Worker exceeded {limit}s; see {log}')
                try:
                    rc = p.wait(timeout=min(15, remaining)); break
                except subprocess.TimeoutExpired:
                    rss = subprocess.run(['ps', '-o', 'rss=', '-p', str(p.pid)],
                                         capture_output=True, text=True, timeout=5)
                    if rss.returncode == 0 and rss.stdout.strip():
                        peak = max(peak, int(rss.stdout.strip())); missed = 0
                        require(peak < 10 * 1024**2, 'Worker exceeded 10 GiB RSS')
                    else:
                        missed += 1
                        require(missed < 3 or p.poll() is not None, 'Unable to sample worker RSS')
                    lines = log.read_text(errors='replace').splitlines()
                    phase = next((s for s in reversed(lines) if s.startswith('PHASE:')), 'starting')
                    print(f'  {int(time.monotonic()-start)}s; sampled peak {peak/1024:.0f} MiB; {phase}', flush=True)
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL); p.wait()
            raise
    require(rc == 0, f'OpenROAD exited {rc}; see {log}')
    return {'seconds': round(time.monotonic()-start, 3), 'sampled_peak_rss_kib': peak or None,
            'limit_seconds': limit}

def archive(run):
    dest = Path(str(run) + '.tar.gz'); partial = Path(str(dest) + '.partial')
    with tarfile.open(partial, 'w:gz') as t:
        for p in sorted(run.rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts or p.suffix in ('.odb', '.spef'):
                continue
            t.add(p, arcname=str(Path(run.name) / p.relative_to(run)), recursive=False)
    os.replace(partial, dest)
    return dest

def tcl_quote(value):
    s = str(value)
    require('\x00' not in s and '\n' not in s and '\r' not in s, 'Invalid Tcl path')
    return '"' + ''.join('\\'+c if c in '\\"$[]' else c for c in s) + '"'

def summarize_sweep(run, baseline, old):
    reports = run / 'sweep/reports'
    io_pins = set((reports / 'io-pins.txt').read_text().splitlines())
    labels = [('original_15pf', 15), *SAMPLES]
    summaries = []; pad_rows = []
    ports = rows(reports / 'io-ports.tsv')
    require(len(ports) == 39 and len({p['port'] for p in ports}) == 39, 'Pad inventory differs')
    for label, load in labels:
        snap = checked_snapshot(reports, label, io_pins)
        write_json(reports / (label + '-parsed.json'), snap)
        summaries.append(dict(case=label, load_pf=load, internal_erc_findings=snap['internal_erc_findings'], **snap['metrics']))
        for port in ports:
            corners = {c: pad_result(reports / (label + '-pads') / (port['port'] + '-' + c + '.rpt'))
                       for c in ('tt', 'ff')}
            states = {x['status'] for x in corners.values()}
            state = 'UNCHARACTERIZED' if 'UNCHARACTERIZED' in states else ('FAIL' if 'FAIL' in states else 'PASS')
            pad_rows.append(dict(case=label, load_pf=load, port=port['port'], master=port['pad_master'], status=state,
                                 tt_slew_margin_ns=corners['tt']['slew_margin_ns'], ff_slew_margin_ns=corners['ff']['slew_margin_ns'],
                                 tt_cap_margin_pf=corners['tt']['cap_margin_pf'], ff_cap_margin_pf=corners['ff']['cap_margin_pf']))
    table(reports / 'sweep.tsv', list(summaries[0]), summaries)
    table(reports / 'pad-sweep.tsv', list(pad_rows[0]), pad_rows)
    limits = [dict(port=p['port'], master=p['pad_master'],
                   **sampled_range([r for r in pad_rows if r['port'] == p['port']])) for p in ports]
    table(reports / 'pad-sampled-envelope.tsv', list(limits[0]), limits)
    restored = checked_snapshot(reports, 'restored_15pf', io_pins)
    require(compare_metrics(restored['metrics'], baseline['metrics'], tolerance=1e-6), '15 pF restoration changed timing/ERC')
    require(restored['erc'] == baseline['erc'], '15 pF restoration changed detailed ERC')
    require(sdc_semantics(run / 'sweep/out/original-runtime.sdc') ==
            sdc_semantics(run / 'sweep/out/restored-runtime.sdc'), 'Load sweep did not restore original runtime constraints')
    delta = {k: baseline['metrics'][k] - old[k] for k in COUNTS + SLACKS}
    write_json(run / 'reports/final-vs-prior.json', {'deltas': delta,
                'reproduces_within_5ps': compare_metrics(baseline['metrics'], old),
                'note': 'Old timing is a control; final repaired-database timing is the new observation.'})
    return summaries, limits

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('replay_run', type=Path)
    args = ap.parse_args()
    require(sys.version_info >= (3, 11), 'Use Python 3.11 or newer')
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Use the existing native Apple Silicon Mac')
    here = Path(__file__).resolve().parent
    pins = json.loads((here / 'pins.json').read_text())
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()).resolve()
    results = root / 'native/results'; source = args.replay_run.resolve()
    require(source.parent == results and source.is_dir(), 'Input must be a completed replay under this repository native/results')
    lock = results / 'cp0c.lock'
    try: lock.mkdir()
    except FileExistsError:
        owner = (lock / 'owner.txt').read_text() if (lock / 'owner.txt').is_file() else '(no owner record)'
        raise RuntimeError(f'Another CP0C stage may be active; inspect {lock}/owner.txt\n{owner}') from None
    run = None; preserve = {}; code = 1
    try:
        (lock / 'owner.txt').write_text(f'pid={os.getpid()} stage=final-timing\n')
        run = Path(tempfile.mkdtemp(prefix='cp0c-final-timing-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-', dir=results))
        for folder in ('helpers', 'reports', 'reference', 'logs'): (run / folder).mkdir()
        print(f'Evidence: {run}\nVersion: {VERSION}\nThree sequential OpenROAD workers; two threads; progress every 15 seconds.', flush=True)
        require(shutil.disk_usage(run).free >= 3 * 1024**3, 'At least 3 GiB free disk is required')
        def checked(path, digest=None):
            path = Path(path)
            require(path.is_file(), f'Missing input: {path}')
            h = sha(path)
            require(digest is None or h == digest, f'Input checksum differs: {path}')
            preserve[str(path.resolve())] = h
            return path
        for p in sorted(here.iterdir()):
            if p.is_file(): shutil.copy2(checked(p), run / 'helpers' / p.name)
        for rel, digest in pins['replay_files'].items(): checked(source / rel, digest)
        prior_replay = json.loads((source / 'reports/conclusion.json').read_text())
        require(prior_replay['status'] == 'FULL_CHIP_DIAGNOSTIC_MATCH_VERIFIED' and
                prior_replay['fault_controls_complete'], 'Prerequisite diagnostic replay is incomplete')
        physical = results / pins['physical_run']; internal = results / pins['internal_run']
        provenance = json.loads((source / 'provenance.json').read_text())
        require(Path(provenance['physical_source']).resolve() == physical, 'Replay physical lineage differs')
        odb = checked(physical / 'out/croc_sram_power.odb', pins['physical_odb'])
        control_odb = checked(internal / 'eco/out/croc_internal.odb', pins['control_odb'])
        sdc = checked(internal / 'eco/out/croc_internal.sdc', pins['sdc'])
        control_spef = checked(internal / 'extract/out/routed-lef.spef', pins['control_spef'])
        for rel, digest in pins['prior_files'].items(): checked(internal / rel, digest)
        known = internal / 'replay/reports/unannotated.tsv'
        old = metrics(internal / 'replay/reports/final.tsv')
        tool = checked(Path.home() / ('tools/croc-openroad-'+pins['openroad_pin']) / 'install/bin/openroad', pins['openroad_sha256'])
        description = subprocess.check_output(['file', '-b', '-L', str(tool)], text=True).strip()
        require('Mach-O' in description and 'arm64' in description, 'OpenROAD is not a native arm64 executable')
        lib_paths = {c: [] for c in ('tt', 'ff')}
        for name, digest in pins['libraries'].items():
            lib = checked(root / 'ihp13/pdk/lib' / name, digest)
            lib_paths['tt' if '_typ_' in name else 'ff'].append(lib)
        require(all(len(v) == 3 for v in lib_paths.values()), 'Expected standard-cell, I/O and SRAM libraries at TT and FF')
        # Preserve the original default-library precedence: standard cells first.
        for paths in lib_paths.values():
            paths.sort(key=lambda p: 0 if p.name.startswith('sg13g2_stdcell_') else
                       (1 if p.name.startswith('sg13cmos5l_io_') else 2))
        config = run / 'helpers/config.tcl'
        config.write_text(''.join('set ::ft_libs('+c+') [list '+ ' '.join(tcl_quote(p) for p in paths) + ']\n'
                                 for c, paths in lib_paths.items()) +
                          'set ::ft_samples {'+' '.join(f'{name} {load}' for name, load in SAMPLES)+'}\n')
        shutil.copy2(sdc, run / 'reference/original.sdc')
        for p in [known, internal / 'replay/reports/final.tsv', internal / 'replay/reports/final-erc.rpt']:
            shutil.copy2(p, run / 'reference' / p.name)
        write_json(run / 'reports/original-load-contract.json', audit_original_sdc(sdc))
        write_json(run / 'provenance.json', dict(version=VERSION, replay_source=str(source), physical_source=str(physical),
            control_source=str(internal), openroad_pin=pins['openroad_pin'], openroad_native=description,
            physical_odb_sha256=pins['physical_odb'], sdc_sha256=pins['sdc'], original_load_pf=15,
            gpio_enable='dynamic', cell_corners=['tt', 'ff'], interconnect_model='One approximate LEF-RC model at both cell corners',
            loads_pf=[15]+[v for _, v in SAMPLES], library_paths={k: list(map(str, v)) for k, v in lib_paths.items()},
            source_geometry_changed=False, candidate_promoted=False, cp0c='PENDING'))
        times = {}; annotations = {}; snapshots = {}; warnings = []
        new_spef = run / 'extract/out/final-lef.spef'
        for mode, time_limit in [('control', 600), ('extract', 1200), ('sweep', 1800)]:
            case = run / mode; case.mkdir()
            env = dict(os.environ, OMP_NUM_THREADS='2', FT_CONFIG=str(config), FT_HELPERS=str(run / 'helpers'),
                       FT_MODE=mode, FT_ODB=str(control_odb if mode == 'control' else odb),
                       FT_SDC=str(sdc), FT_SPEF=str(control_spef if mode == 'control' else new_spef))
            print('STAGE: '+mode, flush=True)
            log = run / 'logs' / (mode+'.log')
            times[mode] = bounded([tool, '-no_init', '-exit', run / 'helpers/worker.tcl'], case, env, log, time_limit)
            text = log.read_text(errors='replace')
            require('CP0C_FINAL_TIMING_WORKER_COMPLETE '+mode in text.splitlines(), 'Native completion marker missing')
            warnings += [{'stage': mode, 'message': s} for s in text.splitlines() if re.search(r'\[(WARNING|ERROR)\b', s)]
            units = (case / 'reports/units.rpt').read_text()
            require(re.search(r'\btime\s+1ns\b', units) and re.search(r'\bcapacitance\s+1pF\b', units), 'Unexpected report units')
            require(metrics(case / 'reports/physical.tsv') == {'signal_signature_unchanged': 1, 'saved_design_written': 0}, 'Unexpected physical change')
            if mode == 'extract':
                write_json(run / 'reports/extracted-spef.json', dict(sha256=sha(new_spef), **spef_stats(new_spef)))
            else:
                reports = case / 'reports'
                annotations[mode] = audit_annotation(reports, known)
                snapshots[mode] = checked_snapshot(reports, 'original_15pf', set((reports / 'io-pins.txt').read_text().splitlines()))
                audit_original_sdc(case / 'out/original-runtime.sdc')
                if mode == 'control':
                    require(compare_metrics(snapshots[mode]['metrics'], old), 'Old 15 pF control failed to reproduce within 5 ps')
                    require(not warnings, 'Control produced new OpenROAD warnings/errors')
        write_json(run / 'reports/annotation.json', annotations)
        write_json(run / 'reports/warnings.json', warnings)
        summaries, envelopes = summarize_sweep(run, snapshots['sweep'], old)
        baseline = snapshots['sweep']; m = baseline['metrics']
        clocks_bad = '(VIOLATED)' in (run / 'sweep/reports/clock-checks.rpt').read_text()
        internal_ok = (m['setup_violations'] == m['hold_violations'] == baseline['internal_erc_findings'] == 0
                       and min(m[k] for k in SLACKS) >= 0 and not warnings and not clocks_bad)
        complete = internal_ok and all(r['all_samples_characterized'] and r['monotonic_samples'] for r in envelopes)
        status = 'FINAL_TIMING_LOAD_CHARACTERIZATION_COMPLETE' if complete else 'FINAL_TIMING_REVIEW_REQUIRED'
        write_json(run / 'reports/conclusion.json', dict(status=status, workers=times, final_15pf=m,
            final_internal_timing='VERIFIED_WITH_DOCUMENTED_RC_SCOPE' if internal_ok else 'REVIEW_REQUIRED',
            io_15pf='REVIEW_REQUIRED' if m['max_slew_violations'] or m['max_cap_violations'] else 'NO_REPORTED_ERC_FINDINGS',
            clock_check_findings=clocks_bad, warning_count=len(warnings), source_geometry_changed=False,
            original_constraints_restored=True, approved_external_load_pf=None, candidate_promoted=False, cp0c='PENDING',
            remaining=['I/O external load specification and any required implementation changes',
                       '80 loaded pad-driver annotation exceptions and package RC scope',
                       'FEOL/density and final baseline manifest review'],
            model_limitations=['LEF RC is approximate, not foundry-calibrated extraction',
                               'TT/FF are cell corners sharing one RC model; slow cell corner is not included',
                               'Sampled pad envelopes concern reported slew/capacitance, not all interface timing requirements']))
        (run / 'status.txt').write_text(status+'\nCP0C_PENDING\n')
        code = 0 if complete else 2
    except (Exception, KeyboardInterrupt) as exc:
        message = f'{type(exc).__name__}: {exc}'
        if run:
            (run / 'failure.txt').write_text(message+'\n')
            (run / 'status.txt').write_text('FINAL_TIMING_STAGE_FAILED\nCP0C_PENDING\n')
        print('STOPPED: '+message, file=sys.stderr, flush=True)
    finally:
        try:
            if run:
                changed = [p for p, digest in preserve.items() if not Path(p).is_file() or sha(p) != digest]
                write_json(run / 'reports/preservation.json', {'unchanged': not changed, 'changed': changed})
                if changed:
                    code = 1; (run / 'status.txt').write_text('FINAL_TIMING_PRESERVATION_FAILED\nCP0C_PENDING\n')
                (run / 'source.sha256').write_text(''.join(f'{h}  {p}\n' for p, h in sorted(preserve.items())))
                (run / 'exit-status.txt').write_text(str(code)+'\n')
                print((run / 'status.txt').read_text(), flush=True)
                print(f'Upload: {archive(run)}\nReports: {run}/sweep/reports', flush=True)
        finally:
            (lock / 'owner.txt').unlink(missing_ok=True); lock.rmdir()
    return code

if __name__ == '__main__':
    try: sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr); sys.exit(1)
