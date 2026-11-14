# Shared report parsing and subprocess output from native-verified audit v1.1.
import subprocess
from cp0c_erc_support import require


def classify(path):
    """Preserve a blank slack when OpenSTA omits that column in a fanout row."""
    rows, kind = [], None
    for line in path.read_text().splitlines():
        if line.strip() in ('max slew', 'max capacitance', 'max fanout'):
            kind = line.strip()
        if '(VIOLATED)' not in line:
            continue
        parts = line.split()
        require(kind is not None and parts[-1] == '(VIOLATED)', f'Unrecognized ERC row: {line}')
        require(len(parts) == 5 or (kind == 'max fanout' and len(parts) == 4), f'Unrecognized columns: {line}')
        pin, limit, actual = parts[:3]
        slack = parts[3] if len(parts) == 5 else ''
        category = 'I/O' if pin.startswith(('gpio', 'pad_')) else 'Internal'
        rows.append((kind, category, pin, limit, actual, slack))
    return rows

def stream(command, cwd, env, logfile):
    with logfile.open('w') as f:
        p = subprocess.Popen(command, cwd=cwd, env=env, text=True,
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
