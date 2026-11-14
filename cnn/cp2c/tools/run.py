#!/usr/bin/env python3
import argparse,json,os,platform,re,shutil,subprocess,sys,tarfile,tempfile
from pathlib import Path
from datetime import datetime
from common import command,git_state,manifest,put,require,sha
from firmware import prepare,parse_run,hex_image
from integrate import expected,ADDED
ROOT=Path(__file__).resolve().parents[1]

def tool(name):
    p=shutil.which(name);require(p,'Missing installed tool: '+name);return p

def compiler():
    candidates=[os.environ.get('CP2C_CC'),os.environ.get('RISCV_CC')]
    if os.environ.get('RISCV_PREFIX'):candidates.append(os.environ['RISCV_PREFIX']+'gcc')
    candidates += ['riscv64-unknown-elf-gcc','riscv32-unknown-elf-gcc','riscv64-elf-gcc','riscv-none-elf-gcc']
    installed=Path.home()/'tools'
    for pattern in ['*/bin/riscv*gcc','*/*/bin/riscv*gcc']:
        candidates += [str(x) for x in sorted(installed.glob(pattern))]
    for name in candidates:
        if not name:continue
        p=shutil.which(name)
        if p:
            prefix=p[:-3];other={n:prefix+n for n in ['nm','objcopy','objdump','readelf']}
            if all(Path(x).is_file() for x in other.values()):
                machine=subprocess.check_output([p,'-dumpmachine'],text=True).strip()
                if machine.startswith('riscv'):return p,other
    raise RuntimeError('RISC-V GCC/binutils not found in native/env.sh PATH. Set CP2C_CC to the existing riscv*-unknown-elf-gcc executable.')

def source_list(repo,out,bender):
    command([bender,'script','flist-plus','-t','rtl','-t','verilator','-t','synthesis','-t','ihp13','-t','asic',
        '-D','FUNCTIONAL=1','-D','COMMON_CELLS_ASSERTS_OFF=1'],out/'work/bender-original.f',cwd=repo)
    lines=(out/'work/bender-original.f').read_text().splitlines()
    for name in ADDED+['rtl/user_domain.sv']:
        require(lines.count(str(repo/name))==1,'Integrated Bender source missing/duplicated: '+name)
    # Clock-gating functional model only: no standard-cell timing is simulated.
    old=str(repo/'ihp13/tc_clk.sv');require(lines.count(old)==1,'Expected one IHP clock wrapper')
    lines=[str(repo/'rtl/tech_cells_generic/tc_clk.sv') if x==old else x for x in lines
           if x!=str(repo/'rtl/test/tb_croc_soc.sv')]
    lines += [str(repo/'ihp13/pdk/verilog'/n) for n in ['RM_IHPSG13_1P_core_behavioral_bm_bist.v','RM_IHPSG13_1P_512x32_c2_bm_bist.v']]
    lines += [str(ROOT/'tests/soc_tb.sv')]
    (out/'work/simulation.f').write_text('\n'.join(lines)+'\n')
    return out/'work/simulation.f'

def build(repo,out,verilator,bender):
    flist=source_list(repo,out,bender);directory=out/'build-soc'
    # Same inherited warning policy as frozen Croc's Verilator flow. New-source
    # diagnostics are checked below, and all inherited diagnostics are retained.
    args=[verilator,'--binary','--timing','--assert','-j','2','-Wno-fatal','-Wno-style','-Wno-BLKANDNBLK',
        '-Wno-WIDTHEXPAND','-Wno-WIDTHTRUNC','-Wno-WIDTHCONCAT','-Wno-ASCRANGE','-Wno-TIMESCALEMOD',
        '--unroll-count','1','--unroll-stmts','1',
        '--top-module','cp2c_soc_tb','--Mdir',directory,'-f',flist]
    if platform.system()=='Darwin':
        sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
        cxx=subprocess.check_output(['xcrun','--find','clang++'],text=True).strip()
        args+=['--compiler','clang','-CFLAGS','-isysroot '+sdk,'-LDFLAGS','-isysroot '+sdk,
               '-MAKEFLAGS','CXX='+cxx+' LINK='+cxx]
    else:
        # Local Linux verification avoids host GCC precompiled-header issues.
        # This changes compilation mechanics, not generated simulation logic.
        args+=['-MAKEFLAGS','VK_PCH_I_FAST= VK_PCH_I_SLOW=']
    record=command(args,out/'logs/build-soc.log',timeout=1800)
    log=(out/'logs/build-soc.log').read_text()
    warnings=[x for x in log.splitlines() if x.startswith('%Warning')]
    require(not any('/cnn/' in x or '/soc_tb.sv:' in x for x in warnings),'New CNN source warning; review build-soc.log')
    put(out/'reports/inherited-warnings.json',warnings)
    exe=directory/'Vcp2c_soc_tb';require(exe.is_file(),'Simulator missing')
    fmt=subprocess.check_output(['file',str(exe)],text=True).strip();record['format']=fmt
    if platform.system()=='Darwin':require('Mach-O' in fmt and 'arm64' in fmt,'Expected native arm64 simulator')
    put(out/'reports/build.json',record)
    return exe

def execute(repo,evidence,out,verilator,bender,gcc,binutils):
    for n in ['logs','reports','work']:(out/n).mkdir(parents=True,exist_ok=True)
    print('STAGE: RV32I firmware and enforced 4 KiB SRAM budget',flush=True)
    layout,index,blob=prepare(ROOT,repo,evidence,out,gcc,binutils)
    print('STAGE: build integrated Croc SoC with CVE2, JTAG, boot ROM and four IHP SRAM models',flush=True)
    exe=build(repo,out,verilator,bender)
    print('STAGE: 55 JTAG-loaded fixtures, cold and warm CPU-driven inference',flush=True)
    results=[];address=layout['symbols']['report']
    for i,row in enumerate(index):
        log=out/'logs'/('soc-'+str(i).zfill(3)+'.log')
        command([exe,'+binary='+str(out/'firmware'/(Path(row['file']).stem+'.hex')),'+report='+f'{address:x}'],log,timeout=180)
        pred=(evidence/'fixtures'/row['file']).read_bytes()[4280]
        result=parse_run(log,pred);result.update(fixture=row['name'],file=row['file']);results.append(result)
    print('STAGE: corrupt-output and stack-guard negative controls',flush=True)
    base=(out/'firmware/000.hex').read_text();raw=bytearray(int(x,16) for x in base.split()[1:])
    controls=[]
    for name,offset,code in [('pool',layout['symbols']['input']-0x10000000+2380,6),
                             ('score',layout['symbols']['input']-0x10000000+2524,7),
                             ('stack_guard',3840,8)]:
        damaged=bytearray(raw);damaged[offset]^=1;path=out/'firmware'/('negative-'+name+'.hex');hex_image(path,damaged)
        log=out/'logs'/('negative-'+name+'.log')
        # A negative run must fail with the exact firmware return code. Timeouts
        # and unrelated simulator failures are not accepted as a negative pass.
        failed=False
        try:command([exe,'+binary='+str(path),'+report='+f'{address:x}'],log,timeout=180)
        except RuntimeError as error:
            require(str(error).startswith('Process exited '),'Negative control timed out: '+name)
            failed=True
        text=log.read_text();require(failed and 'CP2C_FIRMWARE_FAIL status='+f'{2*code+1:08x}' in text and 'CP2C_SOC_PASS' not in text,'Negative control did not fail as intended: '+name)
        controls.append(dict(name=name,firmware_return_code=code,status='EXPECTED_FAILURE'))
    put(out/'reports/inferences.json',results);put(out/'reports/negative-controls.json',controls)
    summary=dict(status='CP2C_SOC_PASS',fixtures=55,inferences=110,pooled_values_checked=15840,scores_checked=1100,
        negative_controls=3,cve2_firmware_executed=True,model_copy='CPU OBI stores',dma_used=False,
        simulated_clock_mhz=20,clock_timing_closed=False,firmware=layout)
    for kind in ['cold','warm']:
        summary[kind]={key:dict(min=min(r[kind][key] for r in results),max=max(r[kind][key] for r in results))
                       for key in ['model_copy_cycles','image_copy_cycles','start_wait_cycles','verify_cycles','total_cycles','engine_cycles']}
    put(out/'reports/summary.json',summary)
    return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('--cp2b',required=True,type=Path);a=p.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Run on the existing native arm64 Mac')
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    repo=ROOT.parents[1];evidence=a.cp2b.expanduser().resolve();pins=json.loads((ROOT/'pins.json').read_text())
    before=git_state(repo);require(before['branch']=='cnn/bringup','Use cnn/bringup')
    resultroot=repo/'cnn/results';resultroot.mkdir(exist_ok=True)
    out=Path(tempfile.mkdtemp(prefix='cp2c-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=resultroot))
    print('CP2C evidence: '+str(out),flush=True);code=1;protected={}
    try:
        print('STAGE: verify accepted CP2B and integrated source pins',flush=True)
        for rel,key in [('cnn','cp1_package_manifest'),('cnn/cp2a','cp2a_package_manifest'),('cnn/cp2b','cp2b_package_manifest')]:
            require(sha(repo/rel/'SHA256SUMS')==pins[key],'Package differs: '+rel)
        require(sha(evidence/'SHA256SUMS')==pins['cp2b_evidence_manifest'],'Use accepted CP2B evidence cp2b-20261023-164403-su3wavxz')
        for base in [repo/'cnn',repo/'cnn/cp2a',repo/'cnn/cp2b',ROOT,evidence]:
            for n,h in manifest(base).items():protected[str(base/n)]=h
            protected[str(base/'SHA256SUMS')]=sha(base/'SHA256SUMS')
        c=json.loads((evidence/'conclusion.json').read_text());require(c['status']=='CP2B_PASS','CP2B not accepted')
        require(json.loads((evidence/'preservation.json').read_text())['unchanged'],'CP2B preservation failed')
        for n,data in expected(repo,pins).items():require((repo/n).read_bytes()==data,'Live integration differs: '+n)
        receipt=json.loads((repo/'cnn/cp2c-state/receipt.json').read_text());require(receipt['active'],'Integration inactive')
        for n,h in pins['original'].items():
            backup=repo/'cnn/cp2c-state'/(Path(n).name+'.before')
            require(sha(backup)==h,'Integration backup differs: '+n);protected[str(backup)]=h
        protected[str(repo/'cnn/cp2c-state/receipt.json')]=sha(repo/'cnn/cp2c-state/receipt.json')
        for n,h in pins['source_files'].items():
            h=receipt['installed'].get(n,h);require(sha(repo/n)==h,'Baseline source differs: '+n);protected[str(repo/n)]=h
        put(out/'prerequisite.json',dict(status='ACCEPTED_CP2B_VERIFIED',manifest_sha256=pins['cp2b_evidence_manifest'],integration=receipt))
        put(out/'protected-inputs.json',protected);shutil.copytree(ROOT,out/'source',ignore=shutil.ignore_patterns('__pycache__'))
        put(out/'host.json',dict(platform=platform.platform(),machine=platform.machine(),python=sys.version,
            native_arm64_required=True))
        gcc,binutils=compiler();verilator=tool('verilator');bender=tool('bender')
        names=dict(gcc=gcc,verilator=verilator,bender=bender,**binutils)
        put(out/'tools.json',{n:dict(path=str(x),sha256=sha(x),version=subprocess.check_output([x,'--version'],text=True).splitlines()[0]) for n,x in names.items()})
        result=execute(repo,evidence,out,verilator,bender,gcc,binutils)
        preserved=all(Path(n).is_file() and sha(n)==h for n,h in protected.items())
        after=git_state(repo);samegit=before==after
        put(out/'preservation.json',dict(inputs_unchanged=preserved,git_state_unchanged=samegit,unchanged=preserved and samegit))
        require(preserved and samegit,'Source or Git state changed during run')
        put(out/'conclusion.json',dict(status='CP2C_PASS',version=pins['version'],scope='Full Croc RTL CPU-driven CNN functional simulation',
            simulation=result,cp0c_electrical='PENDING',whole_chip_fit_verified=False,post_layout_timing=False,speedup_measured=False))
        (out/'status.txt').write_text('CP2C_PASS\nCP0C_ELECTRICAL_PENDING\n');code=0
    except Exception as e:
        (out/'status.txt').write_text('CP2C_FAILED\nCP0C_ELECTRICAL_PENDING\n');(out/'failure.txt').write_text(str(e)+'\n');print('STOPPED: '+str(e),flush=True)
    (out/'exit-status.txt').write_text(str(code)+'\n')
    files=[x for x in sorted(out.rglob('*')) if x.is_file() and 'build-soc' not in x.relative_to(out).parts and not x.name.startswith('core.')]
    (out/'SHA256SUMS').write_text(''.join(sha(x)+'  '+str(x.relative_to(out))+'\n' for x in files))
    archive=Path(str(out)+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tar:
        for x in files+[out/'SHA256SUMS']:tar.add(x,arcname=out.name+'/'+str(x.relative_to(out)))
    print((out/'status.txt').read_text(),end='');print('Upload: '+str(archive),flush=True);return code

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:print('ERROR: '+str(e),file=sys.stderr);sys.exit(1)
