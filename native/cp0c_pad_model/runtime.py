import os, signal, subprocess, tarfile, time
from pathlib import Path
from base_support import require

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

