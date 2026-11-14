"""Shared evidence parsing/checksum functions; no OpenROAD invocation."""
import hashlib
import math
import re
from pathlib import Path

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def verify_manifest(path):
    entries = []
    for line in Path(path).read_text().splitlines():
        digest, filename = line.split('  ', 1)
        require(re.fullmatch(r'[0-9a-f]{64}', digest), f'Invalid manifest: {path}')
        require(sha256(filename) == digest, f'Checksum mismatch: {filename}')
        entries.append((digest, filename))
    require(entries, f'Empty manifest: {path}')
    return entries

def parse_metrics(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        key, value = line.split('\t')
        require(key not in result, f'Duplicate metric: {key}')
        result[key] = float(value)
        require(math.isfinite(result[key]), f'Nonfinite metric: {key}')
    return result

def classify_report(path):
    rows = []
    kind = None
    for line in Path(path).read_text().splitlines():
        if line.strip() in ('max slew', 'max fanout', 'max capacitance'):
            kind = line.strip()
        if '(VIOLATED)' not in line or kind is None:
            continue
        parts = line.split()
        require(len(parts) == 5, f'Unrecognized ERC report row: {line}')
        pin, limit, actual, slack, _ = parts
        if pin.startswith(('gpio', 'pad_')):
            category = 'I/O'
        elif 'gen_sram_bank' in pin and '/A_DOUT[' in pin:
            category = 'SRAM output'
        elif 'gen_sram_bank' in pin:
            category = 'SRAM input'
        elif pin.startswith('clkbuf'):
            category = 'Clock tree'
        else:
            category = 'Other'
        rows.append((kind, category, pin, limit, actual, slack))
    return rows
