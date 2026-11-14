import os,platform,shutil,subprocess
from pathlib import Path

def compiler_command():
 if platform.system()=='Darwin':
  sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
  return ['xcrun','--sdk','macosx','clang','-isysroot',sdk]
 cc=shutil.which('cc')
 if not cc:raise RuntimeError('A native C compiler is required')
 return [cc]
def build(root,out,sanitize=False,main=None):
 from emit_header import emit
 root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=True)
 emit(root/'model/model.json',out/'model_data.h')
 args=compiler_command()+['-std=c11','-O2','-Wall','-Wextra','-Werror','-I',str(out),'-I',str(root/'reference')]
 if sanitize:args+=['-fsanitize=undefined','-fno-sanitize-recover=all']
 args += [str(root/'reference/reference.c'),str(main or root/'reference/host.c'),'-o',str(out/'cnn_reference')]
 p=subprocess.run(args,capture_output=True,text=True,timeout=120)
 (out/'compiler.log').write_text(p.stdout+p.stderr)
 if p.returncode:raise RuntimeError('Native C compilation failed; see '+str(out/'compiler.log'))
 return out/'cnn_reference',args
