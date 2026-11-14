"""Build 4 KiB initial images and expected outputs with the CP1 scalar oracle."""
import copy, hashlib, importlib.util, json, random, struct
from pathlib import Path

def generate(cnn, output):
    cnn, output = Path(cnn), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    spec=importlib.util.spec_from_file_location('cp1_oracle', cnn/'reference/reference.py')
    oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    model=oracle.load(cnn/'model/model.json')
    vectors=json.loads((cnn/'vectors/vectors.json').read_text())
    assert vectors['model_sha256']==hashlib.sha256((cnn/'model/model.json').read_bytes()).hexdigest()
    records=[]
    for v in vectors['records']:
        result=oracle.infer(v['input'],model)
        assert all(result[k]==v[k] for k in ('pooled','scores','prediction'))
        records.append((v['name'],v['input'],model,result))
    for shift in (0,1,8,10,30):
        m=copy.deepcopy(model);m['conv_shift']=shift
        inp=[(i*73+19)%256 for i in range(784)]
        records.append(('shift_'+str(shift),inp,m,oracle.infer(inp,m)))
    m=copy.deepcopy(model)
    m['conv_weights']=[[[0]*5 for _ in range(5)] for _ in range(4)]
    m['fc_weights']=[[0]*144 for _ in range(10)]
    m['conv_bias']=[-256,255,256,65024]
    m['fc_bias']=[-100]*10
    records.append(('negative_score_tie',[255]*784,m,oracle.infer([255]*784,m)))
    rng=random.Random(0xC02A)
    m=copy.deepcopy(model);m['conv_shift']=10
    m['conv_weights']=[[[rng.choice([-127,127]) for _ in range(5)] for _ in range(5)] for _ in range(4)]
    m['fc_weights']=[[rng.choice([-127,127]) for _ in range(144)] for _ in range(10)]
    m['conv_bias']=[-20000,20000,-30000,30000];m['fc_bias']=[-50000+10000*i for i in range(10)]
    inp=[rng.randrange(256) for _ in range(784)]
    records.append(('signed_extremes',inp,m,oracle.infer(inp,m)))
    index=[]
    for i,(name,inp,m,result) in enumerate(records):
        mem=bytearray([0xA5]*4096);mem[:784]=bytes(inp)
        mem[0x310:0x374]=struct.pack('<100b',*[v for c in m['conv_weights'] for r in c for v in r])
        mem[0x374:0x384]=struct.pack('<4i',*m['conv_bias'])
        mem[0x384:0x924]=struct.pack('<1440b',*[v for r in m['fc_weights'] for v in r])
        mem[0x924:0x94c]=struct.pack('<10i',*m['fc_bias'])
        blob=bytes(mem)+bytes(result['pooled'])+struct.pack('<10i',*result['scores'])+bytes([result['prediction'],m['conv_shift']])
        filename=f'{i:03d}.bin';(output/filename).write_bytes(blob)
        index.append(dict(file=filename,name=name,shift=m['conv_shift'],sha256=hashlib.sha256(blob).hexdigest()))
    (output/'index.json').write_text(json.dumps(index,indent=2)+'\n')
    (output/'count.txt').write_text(str(len(records))+'\n')
    return index

if __name__=='__main__':
    import sys
    print(len(generate(sys.argv[1],sys.argv[2])))
