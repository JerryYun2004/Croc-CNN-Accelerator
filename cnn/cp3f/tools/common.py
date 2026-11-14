import hashlib,json,os,subprocess,time
from pathlib import Path

def require(ok,message):
    if not ok: raise RuntimeError(message)
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def put(path,obj):Path(path).write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')
def manifest(root):
    root=Path(root);entries={}
    for line in (root/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ',1);p=Path(name)
        require(not p.is_absolute() and '..' not in p.parts and name not in entries,'Unsafe manifest entry')
        require(sha(root/p)==digest,'Checksum differs: '+str(root/p));entries[name]=digest
    require(entries,'Empty manifest')
    return entries
def command(args,log,timeout=600,env=None,cwd=None):
    args=[str(x) for x in args]
    print('RUN: '+Path(args[0]).name+'; log '+str(log),flush=True)
    start=time.monotonic()
    # Capture the complete pipe before writing the log, avoiding partial file
    # visibility while the tool is still emitting output.
    p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,cwd=cwd,env=env,start_new_session=True)
    try:
        output,_=p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        import signal
        os.killpg(p.pid,signal.SIGTERM)
        try:output,_=p.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);output,_=p.communicate()
        Path(log).write_bytes(output)
        raise RuntimeError('Timed out; see '+str(log))
    Path(log).write_bytes(output);code=p.returncode
    require(code==0,'Process exited '+str(code)+'; see '+str(log))
    return dict(command=args,seconds=round(time.monotonic()-start,3))
def git_state(repo):
    def run(*args):return subprocess.check_output(['git','-C',str(repo),*args],env=dict(os.environ,GIT_OPTIONAL_LOCKS='0'),timeout=60)
    return dict(head=run('rev-parse','HEAD').decode().strip(),branch=run('branch','--show-current').decode().strip(),
        status=run('status','--porcelain=v1','-z','--untracked-files=all').decode(),
        index=hashlib.sha256(run('ls-files','--stage','-z')).hexdigest(),
        diff=hashlib.sha256(run('diff','HEAD','--binary','--no-ext-diff','--no-textconv')).hexdigest())
