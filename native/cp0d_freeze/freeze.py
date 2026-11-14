#!/usr/bin/env python3
"""Freeze a local development snapshot. No EDA jobs, commits or constraint edits."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime

VERSION = '1.1-optional-pdk-links'
SKIP_NAMES = {'.git', '__pycache__', '.DS_Store', '.venv', 'venv', 'node_modules'}
SKIP_ROOTS = ('native/results', 'yosys/out', 'openroad/out', 'openroad/save',
              'openroad/reports', 'build', 'obj_dir', '.cache')

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')

def git(repo, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    p = subprocess.run(['git', '-C', str(repo), *args], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    require(p.returncode == 0, 'Git failed: ' + ' '.join(args) + '\n' + p.stderr.decode(errors='replace'))
    return p.stdout

def safe_relative(s):
    p = Path(s)
    require(s and not p.is_absolute() and '..' not in p.parts and '\n' not in s and '\r' not in s,
            'Unsafe snapshot path: ' + repr(s))
    return p

def git_state(repo):
    require(Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve() == repo,
            'Run against the Croc repository root')
    index = git(repo, 'ls-files', '--stage', '-z')
    require(not git(repo, 'ls-files', '--unmerged'), 'Resolve the existing Git merge conflict before freezing')
    modules = git(repo, 'submodule', 'status', '--recursive').decode()
    require(not any(s.startswith(('-', 'U')) for s in modules.splitlines()),
            'A submodule is uninitialized or conflicted; its source cannot be frozen')
    return dict(head=git(repo, 'rev-parse', 'HEAD').decode().strip(),
                branch=git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').decode().strip(),
                index_sha256=hashlib.sha256(index).hexdigest(),
                status=git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=all').decode(),
                submodules=modules)

def verify_inputs(repo, pins):
    checked = {}
    for rel, expected in {**pins['evidence_files'], **pins['artifact_files']}.items():
        p = repo / safe_relative(rel)
        require(p.is_file(), 'Required baseline input is missing: ' + str(p))
        require(digest(p) == expected, 'Baseline input differs: ' + str(p))
        checked[rel] = p
    report = lambda folder: json.loads((repo / folder / 'reports/conclusion.json').read_text())
    audit = report(pins['audit_dir'])
    require(audit['status'] == 'IO_CONTRACT_AUDIT_COMPLETE' and audit['cp0c'] == 'PENDING',
            'Unexpected I/O audit scope')
    require(audit['electrical_closure'] == 'BLOCKED_BY_MODEL_AND_INTERFACE_REQUIREMENTS',
            'Unexpected I/O audit conclusion')
    timing = report(pins['final_timing_dir'])
    require(timing['final_internal_timing'] == 'VERIFIED_WITH_DOCUMENTED_RC_SCOPE',
            'Expected internal timing evidence is missing')
    lvs = report(pins['lvs_dir'])
    require(lvs['status'] == 'FULL_CHIP_DIAGNOSTIC_MATCH_VERIFIED' and lvs['fault_controls_complete'],
            'Expected diagnostic LVS evidence is missing')
    matches = [p for p in sorted((repo / pins['final_timing_dir']).rglob('*.spef'))
               if p.is_file() and digest(p) == pins['final_spef_sha256']]
    require(matches, 'Final timing SPEF is missing or changed under ' + pins['final_timing_dir'])
    for p in matches:
        checked[str(p.relative_to(repo))] = p
    netlist = repo / 'yosys/out/croc_yosys.v'
    require(netlist.is_file(), 'Historical synthesis netlist is missing: ' + str(netlist))
    checked['yosys/out/croc_yosys.v'] = netlist
    return checked, timing, audit, lvs

def inventory(repo):
    """Capture working bytes, including ignored native helpers and dependency trees."""
    optional = json.loads((Path(__file__).parent / 'optional-links.json').read_text())['links']
    files, links, excluded, missing, unresolved = {}, {}, [], [], {}
    def visit(path, rel, ancestors):
        safe_relative(rel)
        if path.name in SKIP_NAMES or any(rel == s or rel.startswith(s + '/') for s in SKIP_ROOTS):
            excluded.append(rel)
            return
        if path.is_symlink():
            links[rel] = os.readlink(path)
            if not path.exists():
                if rel in optional and links[rel] == optional[rel]['target']:
                    unresolved[rel] = links[rel]
                else:
                    missing.append(rel)
                return
        target = path.resolve()
        if path.is_dir():
            require(target == repo or repo in target.parents,
                    'External directory link needs an explicit local copy before freezing: ' + str(path))
            require(target not in ancestors, 'Directory link cycle: ' + str(path))
            for child in sorted(path.iterdir()):
                visit(child, rel + '/' + child.name, ancestors | {target})
        else:
            require(path.is_file(), 'Unsupported special file: ' + str(path))
            files[rel] = path
    for child in sorted(repo.iterdir()):
        visit(child, child.name, {repo})
    require(not missing, 'Unexpected broken source links: ' + ', '.join(missing[:10]))
    return files, links, excluded, unresolved

def link_metadata(unresolved):
    return (json.dumps(dict(format_version=1, unresolved_optional_links=unresolved),
                       indent=2, sort_keys=True) + '\n').encode()

def describe(files):
    rows = []
    for i, (rel, p) in enumerate(sorted(files.items())):
        st = p.stat()
        rows.append(dict(path=rel, bytes=st.st_size, sha256=digest(p),
                         mode=stat.S_IMODE(st.st_mode)))
        if i and i % 2000 == 0:
            print(f'  Hashed {i} files', flush=True)
    return rows

def unchanged(files, rows):
    return [r['path'] for r in rows if not files[r['path']].is_file()
            or digest(files[r['path']]) != r['sha256']
            or stat.S_IMODE(files[r['path']].stat().st_mode) != r['mode']]

def make_snapshot(path, files, rows, unresolved=None):
    with tarfile.open(path, 'w:gz', compresslevel=1) as t:
        for i, row in enumerate(rows):
            p = files[row['path']]
            info = tarfile.TarInfo('repo/' + row['path'])
            info.size = row['bytes']; info.mode = row['mode']; info.mtime = int(p.stat().st_mtime)
            with p.open('rb') as f:
                t.addfile(info, f)
            if i and i % 2000 == 0:
                print(f'  Archived {i} files', flush=True)
        if unresolved:
            data = link_metadata(unresolved)
            info = tarfile.TarInfo('snapshot-metadata/unresolved-links.json')
            info.size = len(data); info.mode = 0o644
            t.addfile(info, io.BytesIO(data))

def verify_snapshot(path, rows, unresolved=None):
    expected = {'repo/' + r['path']: r for r in rows}
    if unresolved:
        data = link_metadata(unresolved)
        expected['snapshot-metadata/unresolved-links.json'] = dict(
            bytes=len(data), mode=0o644, sha256=hashlib.sha256(data).hexdigest())
    seen = set()
    with tarfile.open(path, 'r|gz') as t:
        for m in t:
            require(m.isfile() and m.name in expected and m.name not in seen, 'Unexpected archive member: ' + m.name)
            row = expected[m.name]; require(m.size == row['bytes'] and m.mode == row['mode'], 'Archive metadata differs')
            h = hashlib.sha256()
            f = t.extractfile(m)
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
            require(h.hexdigest() == row['sha256'], 'Archive content differs: ' + m.name)
            seen.add(m.name)
    require(seen == set(expected), 'Archive is incomplete')

def tool_records(home, pins):
    paths = {
        'openroad': home / ('tools/croc-openroad-' + pins['source_pins']['openroad'] + '/install/bin/openroad'),
        'klayout': home / 'tools/croc-klayout-0.30.9/install/strmrun',
        'python': Path(sys.executable),
        'yosys_launcher': home / 'tools/croc-oss-cad-suite-2025-12-30/oss-cad-suite/bin/yosys',
    }
    records = {}
    for name, p in paths.items():
        require(p.is_file(), 'Expected installed tool is missing: ' + str(p))
        h = digest(p)
        if name in pins['tool_sha256']:
            require(h == pins['tool_sha256'][name], 'Pinned tool changed: ' + name)
        records[name] = dict(path=str(p), resolved=str(p.resolve()), sha256=h,
                             executable=os.access(p, os.X_OK))
    records['note'] = 'Installed paths/hashes only. Yosys launcher can be a shell script. Tool runtimes are not embedded.'
    return records

def check_current_inventory(repo, source_rels, old_links, old_unresolved=None):
    current, links, _, unresolved = inventory(repo)
    require(set(current) == source_rels, 'Source file list changed while freezing')
    require(links == old_links, 'Source links changed while freezing')
    require(unresolved == (old_unresolved or {}), 'Source link resolution changed while freezing')

def freeze(repo, out, pins, max_bytes, here, check_tools=True):
    before = git_state(repo)
    print('STAGE: verify reviewed evidence and physical artifacts', flush=True)
    required, timing, audit, lvs = verify_inputs(repo, pins)
    print('STAGE: inventory source and dependencies', flush=True)
    files, links, excluded, unresolved = inventory(repo); source_rels = set(files)
    files.update(required)
    total = sum(p.stat().st_size for p in files.values())
    require(total <= max_bytes, f'Snapshot needs {total / 1024**3:.2f} GiB uncompressed; exceeds --max-gib. No source was changed.')
    require(shutil.disk_usage(out).free > total * 1.15 + 256 * 1024**2, 'Not enough free space for the snapshot')
    print(f'Snapshot: {len(files)} files, {total / 1024**2:.1f} MiB before compression', flush=True)
    if unresolved:
        print(f'OPTIONAL_PDK_LINKS_RECORDED: {len(unresolved)} exact upstream links; target bytes unavailable.', flush=True)
    rows = describe(files)
    write_json(out / 'files.json', rows)
    (out / 'unresolved-links.json').write_bytes(link_metadata(unresolved))
    write_json(out / 'source-selection.json', dict(excluded=excluded,
        materialized_links={k:v for k,v in links.items() if k not in unresolved},
        unresolved_optional_links=unresolved,
        unresolved_link_policy='Exact path/target matches only; stored as JSON records, never followed or fabricated',
        policy='All on-disk repository files except listed caches/output directories, plus pinned evidence and physical artifacts',
        external_directory_links='rejected', clean_rebuild_verified=False))
    write_json(out / 'git.json', before)
    (out / 'tracked-files.zlist').write_bytes(git(repo, 'ls-files', '-z'))
    (out / 'worktree.patch').write_bytes(git(repo, 'diff', '--binary', '--no-ext-diff', '--no-textconv', 'HEAD', '--'))
    (out / 'index.patch').write_bytes(git(repo, 'diff', '--cached', '--binary', '--no-ext-diff', '--no-textconv', 'HEAD', '--'))
    print('STAGE: preserve Git history and archive working files', flush=True)
    git(repo, 'bundle', 'create', str(out / 'repository.bundle'), 'HEAD')
    git(repo, 'bundle', 'verify', str(out / 'repository.bundle'))
    make_snapshot(out / 'snapshot.tar.gz', files, rows, unresolved)
    print('STAGE: read back archive and check input preservation', flush=True)
    verify_snapshot(out / 'snapshot.tar.gz', rows, unresolved)
    changed = unchanged(files, rows)
    check_current_inventory(repo, source_rels, links, unresolved)
    after = git_state(repo)
    require(before == after and not changed, 'Repository changed during snapshot; freeze is incomplete')
    tools = tool_records(Path.home(), pins) if check_tools else {'test_fixture': True}
    write_json(out / 'tools.json', tools)
    for name in ('DEVELOPMENT_SCOPE.md', 'RESTORE.md', 'CNN_NEXT.md'):
        shutil.copy2(here / name, out / name)
    for name, source in [('timing.json', timing), ('io-audit.json', audit), ('diagnostic-lvs.json', lvs)]:
        write_json(out / name, source)
    write_json(out / 'baseline.json', dict(status='DEVELOPMENT_BASELINE_FROZEN', version=VERSION,
        accepted_scope='CNN development and controlled comparisons with open signoff items',
        cp0c_electrical='PENDING', fabrication_ready=False, clean_rebuild_verified=False,
        source_to_layout_rebuild_verified=False,
        source_files=len(rows), source_bytes=total, git_head=before['head'],
        unresolved_optional_links=len(unresolved), optional_link_targets_included=False,
        working_copy_changes_preserved=True, source_pins=pins['source_pins'],
        physical_artifact_hashes=pins['artifact_files'], final_spef_sha256=pins['final_spef_sha256'],
        full_chip_metrics=timing['final_15pf'], new_eda_runs=0,
        diagnostic_load_approved=False, models_modified=False, constraints_modified=False,
        git_branch_changed=False, commits_created=0, restored_build_tools_included=False,
        note='The snapshot is the frozen working state; Git HEAD alone may omit uncommitted files.'))
    write_json(out / 'preservation.json', dict(unchanged=True, changed=[], git_state_unchanged=True))
    (out / 'status.txt').write_text('DEVELOPMENT_BASELINE_FROZEN\nCP0C_ELECTRICAL_PENDING\n')

def seal(out):
    files = [p for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS']
    (out / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + str(p.relative_to(out)) + '\n' for p in files))
    summary = Path(str(out) + '-summary.tar.gz')
    with tarfile.open(summary, 'w:gz') as t:
        for p in sorted(out.iterdir()):
            if p.is_file() and p.name not in ('snapshot.tar.gz', 'repository.bundle', 'worktree.patch', 'index.patch'):
                t.add(p, arcname=out.name + '/' + p.name, recursive=False)
    return summary

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--destination', type=Path)
    ap.add_argument('--max-gib', type=float, default=4)
    args = ap.parse_args()
    require(sys.version_info >= (3, 11), 'Python 3.11+ required')
    require(args.max_gib > 0, '--max-gib must be positive')
    repo = args.repo.resolve()
    require((repo / 'rtl/croc_pkg.sv').is_file(), 'Croc root not found')
    destination = (args.destination or repo.parent / 'croc-cnn-baselines').resolve()
    require(destination != repo and repo not in destination.parents, 'Destination must be outside the source repository')
    destination.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix='cp0d-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-', dir=destination))
    here = Path(__file__).resolve().parent
    print(f'Baseline: {out}\nVersion: {VERSION}\nNo compilation or physical-design runs.', flush=True)
    code = 1
    try:
        freeze(repo, out, json.loads((here / 'pins.json').read_text()), args.max_gib * 1024**3, here)
        code = 0
    except (Exception, KeyboardInterrupt) as exc:
        (out / 'failure.txt').write_text(type(exc).__name__ + ': ' + str(exc) + '\n')
        (out / 'status.txt').write_text('DEVELOPMENT_FREEZE_FAILED\nCP0C_ELECTRICAL_PENDING\n')
        print('STOPPED: ' + str(exc), file=sys.stderr)
    (out / 'exit-status.txt').write_text(str(code) + '\n')
    summary = seal(out)
    print((out / 'status.txt').read_text(), flush=True)
    print(f'Keep the full baseline directory: {out}\nUpload summary only if needed: {summary}', flush=True)
    return code

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
