#!/usr/bin/env python3
"""CP1 native software-reference check; no RTL, EDA, training or Git writes."""
import argparse,hashlib,json,os,platform,struct,subprocess,sys,tarfile,tempfile
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'reference'))
from reference import infer,load
from host_build import build

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def put(path,obj):path.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def git(repo,*args):return subprocess.check_output(['git','-C',str(repo),*args],env=dict(os.environ,GIT_OPTIONAL_LOCKS='0'),timeout=60)
def git_state(repo):return dict(head=git(repo,'rev-parse','HEAD').decode().strip(),branch=git(repo,'branch','--show-current').decode().strip(),status=git(repo,'status','--porcelain=v1','-z','--untracked-files=all').decode(),index=hashlib.sha256(git(repo,'ls-files','--stage','-z')).hexdigest(),diff=hashlib.sha256(git(repo,'diff','HEAD','--binary','--no-ext-diff','--no-textconv')).hexdigest())
def verify_package(root):
 for line in (root/'SHA256SUMS').read_text().splitlines():
  expected,name=line.split('  ',1);p=Path(name)
  require(not p.is_absolute() and '..' not in p.parts,'Unsafe manifest path')
  require(sha(root/p)==expected,'CP1 package changed: '+name)
def verify_baseline(repo,baseline):
 pin=json.loads((ROOT/'baseline-pin.json').read_text())
 require(sha(baseline/'SHA256SUMS')==pin['baseline_manifest_sha256'],'Wrong baseline manifest')
 require(sha(baseline/'baseline.json')==pin['baseline_json_sha256'],'Wrong baseline metadata')
 for name in ['snapshot.tar.gz','repository.bundle']:
  require((baseline/name).is_file() and (baseline/name).stat().st_size>0,'Full baseline missing: '+name)
 b=json.loads((baseline/'baseline.json').read_text());require(b['status']=='DEVELOPMENT_BASELINE_FROZEN','Baseline freeze incomplete')
 for name,digest in pin['source_files'].items():require(sha(repo/name)==digest,'Croc integration source differs: '+name)
 return dict(directory=str(baseline),baseline_json_sha256=pin['baseline_json_sha256'],integration_source_hashes=pin['source_files'],full_archive_rehashed_this_run=False,baseline_full_archive_native_readback='previous CP0D run')
def check_output(output,records):
 require(len(output)==185*len(records),'C output byte count differs')
 for i,r in enumerate(records):
  b=output[i*185:(i+1)*185]
  require(list(b[:144])==r['pooled'],'C pooled activation differs: '+r['name'])
  require(list(struct.unpack('<10i',b[144:184]))==r['scores'],'C score differs: '+r['name'])
  require(b[184]==r['prediction'],'C prediction differs: '+r['name'])
def selftest(out):
 verify_package(ROOT)
 p=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(ROOT/'tests'),'-v'],capture_output=True,text=True,timeout=120,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
 (out/'unit-tests.log').write_text(p.stdout+p.stderr);require(p.returncode==0,'Arithmetic tests failed')
 primitive,_=build(ROOT,out/'c-arithmetic',main=ROOT/'tests/rounding.c')
 require(subprocess.run([str(primitive)],timeout=30).returncode==0,'Native C arithmetic controls failed')
 m=load(ROOT/'model/model.json');v=json.loads((ROOT/'vectors/vectors.json').read_text());records=v['records']
 require(v['model_sha256']==sha(ROOT/'model/model.json'),'Vector model identity differs')
 expected_blob=struct.pack('<100b',*[x for c in m['conv_weights'] for r in c for x in r])+struct.pack('<4i',*m['conv_bias'])+struct.pack('<1440b',*[x for r in m['fc_weights'] for x in r])+struct.pack('<10i',*m['fc_bias'])
 require((ROOT/'model/model.bin').read_bytes()==expected_blob,'MMIO parameter image differs from model.json')
 spec=json.loads((ROOT/'spec.json').read_text())
 require(spec['conv']['shift']==m['conv_shift']==9 and spec['parameter_bytes']==len(expected_blob)==1596,'Specification/model mismatch')
 for r in records:
  actual=infer(r['input'],m)
  require(all(actual[k]==r[k] for k in ('pooled','scores','prediction')),'Python vector mismatch: '+r['name'])
 exe,cmd=build(ROOT,out/'host')
 p=subprocess.run([str(exe)],input=b''.join(bytes(r['input']) for r in records),capture_output=True,timeout=120)
 (out/'host-stderr.log').write_bytes(p.stderr);require(p.returncode==0,'C reference process failed')
 check_output(p.stdout,records)
 # Test the checker at one intermediate, one score, and one decision byte.
 for index in [0,144,184]:
  bad=bytearray(p.stdout);bad[index]^=1
  try:check_output(bad,records)
  except RuntimeError:pass
  else:raise RuntimeError('Output corruption was not detected')
 a=json.loads((ROOT/'model/training-report.json').read_text());audit=json.loads((ROOT/'model/c-validation.json').read_text())
 require(a['model_sha256']==sha(ROOT/'model/model.json')==audit['model_sha256'],'Model evidence identity mismatch')
 require(a['test_examples']==10000 and a['integer_test_correct']==audit['correct']==9711,'Accuracy evidence differs')
 require(a['integer_test_accuracy']>=0.95 and not a['test_set_used_for_selection'],'Accuracy gate failed')
 put(out/'reference-check.json',dict(vectors=len(records),python_c_intermediate_and_score_match=True,output_corruption_controls=3,compiler_command=cmd,compiler_platform=platform.platform(),accuracy_evidence=a['integer_test_accuracy'],accuracy_measured_here=False,hardware_test=False))
 return dict(vectors=len(records),frozen_integer_test_accuracy=a['integer_test_accuracy'])
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--baseline',type=Path,required=True);args=ap.parse_args()
 repo=ROOT.parent;baseline=args.baseline.expanduser().resolve()
 before=git_state(repo);require(before['branch']=='cnn/bringup','Run on cnn/bringup')
 resultroot=ROOT/'results';resultroot.mkdir(exist_ok=True)
 out=Path(tempfile.mkdtemp(prefix='cp1-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=resultroot))
 print('CP1 evidence: '+str(out),flush=True);code=1
 try:
  print('STAGE: verify frozen baseline and integration sources',flush=True);put(out/'baseline-reference.json',verify_baseline(repo,baseline))
  print('STAGE: scalar Python, native C and arithmetic controls',flush=True);results=selftest(out)
  require(before==git_state(repo),'Repository state changed during CP1 run')
  put(out/'preservation.json',dict(unchanged=True,rtl_modified=False,pdk_modified=False,baseline_modified=False,git_state_unchanged=True))
  put(out/'conclusion.json',dict(status='CP1_PASS',scope='Fixed CNN specification and host integer reference',cp0c_electrical='PENDING',rtl_implemented=False,croc_execution_verified=False,area_fit_verified=False,training_run_here=False,**results))
  (out/'status.txt').write_text('CP1_PASS\nCP0C_ELECTRICAL_PENDING\n');code=0
 except Exception as e:
  (out/'status.txt').write_text('CP1_FAILED\n');(out/'failure.txt').write_text(str(e)+'\n');print('STOPPED: '+str(e),flush=True)
 (out/'exit-status.txt').write_text(str(code)+'\n')
 files=[p for p in sorted(out.rglob('*')) if p.is_file()]
 (out/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(out))+'\n' for p in files))
 archive=Path(str(out)+'.tar.gz')
 with tarfile.open(archive,'w:gz') as t:t.add(out,arcname=out.name)
 print((out/'status.txt').read_text());print('Upload: '+str(archive));return code
if __name__=='__main__':
 try:sys.exit(main())
 except Exception as e:print('ERROR: '+str(e),file=sys.stderr);sys.exit(1)
