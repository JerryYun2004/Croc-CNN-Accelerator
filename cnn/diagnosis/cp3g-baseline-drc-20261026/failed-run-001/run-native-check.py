from pathlib import Path
import importlib.util,subprocess,json,time
b=Path(__file__).resolve().parent
repo=Path('/Users/jerryyun/projects/croc-cnn-native')
p=b/'draft-source/Croc_CP3G_Route/cnn/cp3g/tools/environment.py'
s=importlib.util.spec_from_file_location('cp3g_environment',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
tool=Path.home()/'tools/croc-openroad-c463e998615210dc322c999fc9a9f56885dbd664/install/bin/openroad'
start=time.monotonic()
with (b/'native-local-drc.log').open('xb') as log:
 try:
  result=subprocess.run([str(tool),'-no_init','-exit',str(b/'check-native.tcl')],cwd=b,env=m.environment(repo),stdout=log,stderr=subprocess.STDOUT,timeout=300)
  status={'exit_code':result.returncode,'timed_out':False}
 except subprocess.TimeoutExpired:
  status={'exit_code':None,'timed_out':True}
status['elapsed_seconds']=round(time.monotonic()-start,3)
(b/'native-local-status.json').write_text(json.dumps(status,indent=2)+'\n')
print(json.dumps(status));print('Evidence:',b)
