"""Guarded installation of the two CP2C integration edits, with exact restoration."""
import json, os, tempfile
from pathlib import Path
from common import require, sha, put

ADDED = ['cnn/cp2a/rtl/cnn_mac.sv','cnn/cp2a/rtl/cnn_quant.sv',
         'cnn/cp2a/rtl/cnn_engine.sv','cnn/cp2b/rtl/cnn_obi.sv',
         'cnn/cp2b/rtl/cnn_sram_4k.sv','cnn/cp2b/rtl/cnn_peripheral.sv',
         'cnn/cp2b/integration/cnn_croc.sv']

def write_atomic(path, data):
    fd, temp = tempfile.mkstemp(prefix='.cp2c-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(data)
        os.chmod(temp, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)

def expected(repo, pins):
    state=repo/'cnn/cp2c-state'
    original=state/'Bender.yml.before'
    base=original.read_bytes() if original.is_file() else (repo/'Bender.yml').read_bytes()
    import hashlib
    require(hashlib.sha256(base).hexdigest()==pins['original']['Bender.yml'],'Original Bender file differs')
    marker=b'      # add your design files with modules here\n'
    require(base.count(marker)==1,'Bender insertion point differs')
    added=('      # CP2C: accepted CNN engine and OBI/SRAM peripheral\n'+''.join('      - '+x+'\n' for x in ADDED)).encode()
    return {'Bender.yml':base.replace(marker,marker+added),
            'rtl/user_domain.sv':(repo/'cnn/cp2b/integration/user_domain.sv').read_bytes()}

def apply(repo,pins):
    repo=Path(repo); state=repo/'cnn/cp2c-state'; targets=expected(repo,pins)
    require(not state.is_symlink(),'Linked integration state refused')
    if (state/'receipt.json').exists():
        receipt=json.loads((state/'receipt.json').read_text())
        if receipt['active']:
            for name,data in targets.items():
                require(not (repo/name).is_symlink() and (repo/name).read_bytes()==data,'Edited integration file preserved: '+name)
                require(sha(state/(Path(name).name+'.before'))==pins['original'][name],'Backup differs: '+name)
            return receipt
    for name in targets:
        require(not (repo/name).is_symlink() and sha(repo/name)==pins['original'][name],'Baseline file differs; preserved: '+name)
    require(not (repo/'rtl').is_symlink(),'Linked RTL directory refused')
    state.mkdir(exist_ok=True)
    before={name:(repo/name).read_bytes() for name in targets}
    for name,data in before.items():
        backup=state/(Path(name).name+'.before')
        if backup.exists():require(backup.read_bytes()==data,'Existing backup differs')
        else:write_atomic(backup,data)
    try:
        for name,data in targets.items():write_atomic(repo/name,data)
    except BaseException:
        for name,data in before.items():write_atomic(repo/name,data)
        raise
    receipt=dict(active=True,original=pins['original'],installed={n:sha(repo/n) for n in targets})
    put(state/'receipt.json',receipt)
    return receipt

def restore(repo,pins):
    state=repo/'cnn/cp2c-state';receipt=json.loads((state/'receipt.json').read_text())
    require(receipt['active'],'Integration already restored')
    for name,digest in receipt['installed'].items():
        require(not (repo/name).is_symlink() and sha(repo/name)==digest,'Edited file preserved: '+name)
        require(sha(state/(Path(name).name+'.before'))==pins['original'][name],'Backup differs')
    for name in receipt['installed']:write_atomic(repo/name,(state/(Path(name).name+'.before')).read_bytes())
    receipt['active']=False;put(state/'receipt.json',receipt)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--restore',action='store_true',required=True);p.parse_args()
    root=Path(__file__).resolve().parents[1];restore(root.parents[1],json.loads((root/'pins.json').read_text()))
    print('Restored the exact original Bender.yml and rtl/user_domain.sv; CP1/CP2A/CP2B and evidence retained.')
