import json
from pathlib import Path
from common import manifest,require,sha

def sources(repo,root,pins):
    protected={}
    for rel,digest in pins['packages'].items():
        base=repo/rel;require(sha(base/'SHA256SUMS')==digest,'Prerequisite package differs: '+rel)
        protected[str(base/'SHA256SUMS')]=digest
        protected.update({str(base/n):h for n,h in manifest(base).items()})
    protected.update({str(root/n):h for n,h in manifest(root).items()})
    protected[str(root/'SHA256SUMS')]=sha(root/'SHA256SUMS')
    for n,h in pins['source_files'].items():
        require(sha(repo/n)==h,'Frozen/live source differs; preserved: '+n);protected[str(repo/n)]=h
    receipt=repo/'cnn/cp2c-state/receipt.json';r=json.loads(receipt.read_text())
    require(r['active'],'CP2C integration is inactive')
    for n,h in pins['original'].items():
        p=repo/'cnn/cp2c-state'/(Path(n).name+'.before')
        require(sha(p)==h,'CP2C original backup differs: '+n);protected[str(p)]=h
    require(r['installed']=={n:pins['source_files'][n] for n in pins['original']},'CP2C integration receipt differs')
    protected[str(receipt)]=sha(receipt)
    return protected

def accepted(evidence,pins):
    require(sha(evidence/'SHA256SUMS')==pins['cp3d_evidence_manifest'],'Use accepted CP3D evidence cp3d-20261025-095839-zvn2dk1y')
    entries=manifest(evidence)
    require(json.loads((evidence/'conclusion.json').read_text())['status']=='CP3D_TIMING_REPAIR_PASS','CP3D did not pass')
    require(json.loads((evidence/'preservation.json').read_text())['unchanged'],'CP3D preservation failed')
    entries['SHA256SUMS']=sha(evidence/'SHA256SUMS')
    return {str(evidence/n):h for n,h in entries.items()}
