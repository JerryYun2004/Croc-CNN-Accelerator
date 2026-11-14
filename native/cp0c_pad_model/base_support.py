"""Small dependency-free evidence helpers (Python 3.11+)."""
import csv
import hashlib
import json
from pathlib import Path

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')

def table(path, values):
    require(values, 'Cannot write an empty evidence table')
    with Path(path).open('w', newline='') as f:
        w=csv.DictWriter(f, fieldnames=list(values[0]), delimiter='\t', lineterminator='\n')
        w.writeheader(); w.writerows(values)
