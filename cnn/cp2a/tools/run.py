#!/usr/bin/env python3
"""Standalone RTL simulation, logic synthesis and provisional TT setup estimate."""
import argparse,json,os,platform,re,shutil,subprocess,sys,tarfile,tempfile
from pathlib import Path
from datetime import datetime
from common import command,git_state,manifest,put,require,sha
from checks import prerequisite,synthesis_result,timing_result,macro_area,metadata_cleanup
from fixtures import generate
ROOT=Path(__file__).resolve().parents[1]

def tool(name,preferred=None):
    if preferred and Path(preferred).is_file():return str(preferred)
    found=shutil.which(name);require(found,'Required existing tool not found: '+name);return found

def build(verilator,top,sources,cpp,out):
    builddir=out/('build-'+top)
    args=[verilator,'--cc','--exe','--build','-j','2','-Wall','--top-module',top,'--Mdir',builddir]
    flags='-O2 -std=c++17'
    if platform.system()=='Darwin':
        sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
        cxx=subprocess.check_output(['xcrun','--find','clang++'],text=True).strip()
        flags+=' -isysroot '+sdk
        args+=['--compiler','clang','-LDFLAGS','-isysroot '+sdk,'-MAKEFLAGS','CXX='+cxx+' LINK='+cxx]
    args+=['-CFLAGS',flags]+sources+[cpp]
    record=command(args,out/'logs'/('build-'+top+'.log'),timeout=900)
    exe=builddir/('V'+top)
    require(exe.is_file(),'Simulation executable missing')
    if platform.system()=='Darwin':
        info=subprocess.check_output(['file',str(exe)],text=True)
        require('Mach-O' in info and 'arm64' in info,'Simulation is not native arm64')
        record['executable_format']=info.strip()
    return exe,record

def synthesis_script(lib):
    # Relative output names avoid Yosys tee quoting differences across versions.
    require('"' not in str(lib) and '\n' not in str(lib),'Unsupported Liberty filename')
    script='read_verilog -sv work/cnn_mac.sv work/cnn_quant.sv work/cnn_engine.sv\n'
    script+='hierarchy -check -top cnn_engine\nsynth -top cnn_engine -flatten\ncheck -assert\n'
    script+=f'read_liberty -lib -ignore_miss_func "{lib}"\ndfflibmap -liberty "{lib}"\n'
    script+=f'abc -liberty "{lib}" -constr work/abc.constr -D 10000\nclean\ncheck -assert\n'
    script+=f'tee -o reports/synthesis-before-cleanup.json stat -json -liberty "{lib}"\n'
    # These zero-area hierarchy records are not circuit elements. Keep every
    # other type so a real generic cell still fails the strict mapping check.
    script+='delete t:$scopeinfo\nselect -assert-none t:$scopeinfo\ncheck -assert\n'
    script+=f'stat -liberty "{lib}"\nwrite_verilog -noattr -noexpr work/engine_mapped.v\n'
    script+=f'tee -o reports/synthesis-stat.json stat -json -liberty "{lib}"\n'
    return script

def execute(out,cnn,verilator,yosys,sta,lib,lef,tech_lef,cell_lef):
    logs=out/'logs';reports=out/'reports';work=out/'work'
    for p in (logs,reports,work):p.mkdir(parents=True,exist_ok=True)
    for p in (ROOT/'rtl').glob('*.sv'):shutil.copy2(p,work/p.name)
    shutil.copy2(ROOT/'flow/abc.constr',work/'abc.constr')
    sources=[work/n for n in ('cnn_mac.sv','cnn_quant.sv','cnn_engine.sv')]
    print('STAGE: regenerate independent scalar reference fixtures',flush=True)
    fixtures=generate(cnn,out/'fixtures');put(reports/'fixtures.json',fixtures)
    print('STAGE: strict RTL lint; arithmetic and protocol simulations',flush=True)
    command([verilator,'--lint-only','-Wall','--top-module','cnn_engine',*sources],logs/'lint.log',timeout=180)
    primitive,primitive_build=build(verilator,'primitives',sources[:2]+[ROOT/'tests/primitives.sv'],ROOT/'tests/primitives.cpp',out)
    command([primitive],reports/'primitives.json',timeout=120)
    require(json.loads((reports/'primitives.json').read_text())['status']=='PRIMITIVES_PASS','Arithmetic test failed')
    engine,engine_build=build(verilator,'cnn_engine',sources,ROOT/'tests/engine.cpp',out)
    command([engine,out/'fixtures',str(len(fixtures))],reports/'engine.json',timeout=900)
    sim=json.loads((reports/'engine.json').read_text())
    require(sim['status']=='ENGINE_PASS' and sim['fixtures']==55 and sim['full_inferences']==165,'RTL suite incomplete')
    put(reports/'builds.json',dict(primitives=primitive_build,engine=engine_build))
    print('STAGE: TT standard-cell mapping; separate SRAM footprint estimate',flush=True)
    (work/'synthesis.ys').write_text(synthesis_script(lib))
    command([yosys,'-s',work/'synthesis.ys'],logs/'synthesis.log',timeout=900,cwd=out)
    put(reports/'metadata-cleanup.json',metadata_cleanup(reports/'synthesis-before-cleanup.json',reports/'synthesis-stat.json'))
    area=synthesis_result(reports/'synthesis-stat.json');area['candidate_sram']=macro_area(lef)
    area['engine_plus_two_candidate_macros_um2']=round(area['engine_cell_area_um2']+area['candidate_sram']['two_macro_area_um2'],6)
    area['total_chip_fit_verified']=False;put(reports/'area.json',area)
    print('STAGE: 100 MHz standalone TT setup estimate',flush=True)
    env=dict(os.environ,CP2A_LIB=str(lib),CP2A_NETLIST=str(work/'engine_mapped.v'),
             CP2A_TECH_LEF=str(tech_lef),CP2A_CELL_LEF=str(cell_lef))
    sta_args=[sta,'-no_init','-exit',ROOT/'flow/timing.tcl']
    command(sta_args,logs/'timing.log',timeout=180,env=env,cwd=out)
    require('CP2A_OPENROAD_LEF_LOADED' in (logs/'timing.log').read_text(),
            'OpenROAD did not initialize the pinned LEFs')
    timing=timing_result(logs/'timing.log');put(reports/'timing.json',timing)
    require(timing['setup_pass'],'100 MHz setup estimate failed; review timing.log')
    return dict(simulation=sim,area=area,timing=timing)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cp1',type=Path,required=True)
    args=parser.parse_args();cnn=ROOT.parent;repo=cnn.parent;evidence=args.cp1.expanduser().resolve()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','This runner targets the existing native arm64 Mac')
    before=git_state(repo);require(before['branch']=='cnn/bringup','Run on cnn/bringup')
    resultroot=cnn/'results';resultroot.mkdir(exist_ok=True)
    out=Path(tempfile.mkdtemp(prefix='cp2a-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=resultroot))
    print('CP2A evidence: '+str(out),flush=True)
    print('Version: 1.2-openroad-lef-init',flush=True);code=1
    try:
        pins=json.loads((ROOT/'pins.json').read_text());manifest(ROOT)
        print('STAGE: verify CP1, source preservation and pinned technology files',flush=True)
        put(out/'cp1-prerequisite.json',prerequisite(cnn,evidence,pins))
        baseline_pin=json.loads((cnn/'baseline-pin.json').read_text())
        for name,digest in baseline_pin['source_files'].items():require(sha(repo/name)==digest,'Croc source differs: '+name)
        lib=repo/pins['liberty']['path'];lef=repo/pins['sram_lef']['path']
        require(sha(lib)==pins['liberty']['sha256'],'Liberty pin differs')
        require(sha(lef)==pins['sram_lef']['sha256'],'SRAM LEF pin differs')
        tech_lef=repo/pins['technology_lef']['path'];cell_lef=repo/pins['standard_cell_lef']['path']
        require(sha(tech_lef)==pins['technology_lef']['sha256'],'Technology LEF pin differs')
        require(sha(cell_lef)==pins['standard_cell_lef']['sha256'],'Standard-cell LEF pin differs')
        protected={str(repo/name):digest for name,digest in baseline_pin['source_files'].items()}
        for base in (ROOT,cnn,evidence):
            for name,digest in manifest(base).items():protected[str(base/name)]=digest
            protected[str(base/'SHA256SUMS')]=sha(base/'SHA256SUMS')
        protected[str(lib)]=sha(lib);protected[str(lef)]=sha(lef)
        protected[str(tech_lef)]=sha(tech_lef);protected[str(cell_lef)]=sha(cell_lef)
        verilator=tool('verilator')
        yosys=tool('yosys',Path.home()/'tools/croc-oss-cad-suite-2025-12-30/oss-cad-suite/bin/yosys')
        sta=tool('openroad',Path.home()/'tools/croc-openroad-c463e998615210dc322c999fc9a9f56885dbd664/install/bin/openroad')
        versions={}
        for name,exe,arg in [('verilator',verilator,'--version'),('yosys',yosys,'-V'),('openroad',sta,'-version')]:
            p=subprocess.run([exe,arg],capture_output=True,text=True,timeout=60)
            require(p.returncode==0,'Tool version check failed: '+name)
            versions[name]=dict(path=exe,launcher_sha256=sha(exe),version=(p.stdout+p.stderr).strip())
        versions['platform']=platform.platform();put(out/'tools.json',versions)
        result=execute(out,cnn,verilator,yosys,sta,lib,lef,tech_lef,cell_lef)
        changed=[p for p,d in protected.items() if sha(p)!=d]
        require(not changed and git_state(repo)==before,'Source, prerequisite or Git state changed')
        put(out/'preservation.json',dict(unchanged=True,cp1_unchanged=True,croc_rtl_unchanged=True,pdk_unchanged=True,git_state_unchanged=True))
        put(out/'conclusion.json',dict(status='CP2A_PASS',version=pins['version'],scope='Standalone fixed-shape CNN RTL',cp0c_electrical='PENDING',
            integrated_into_croc=False,sram_wrapper_implemented=False,physical_fit_verified=False,post_layout_timing=False,**result))
        (out/'status.txt').write_text('CP2A_PASS\nCP0C_ELECTRICAL_PENDING\n');code=0
    except Exception as e:
        (out/'status.txt').write_text('CP2A_FAILED\nCP0C_ELECTRICAL_PENDING\n')
        (out/'failure.txt').write_text(str(e)+'\n');print('STOPPED: '+str(e),flush=True)
    (out/'exit-status.txt').write_text(str(code)+'\n')
    # Archive evidence and mapped netlist; omit reconstructible compiler objects.
    files=[p for p in sorted(out.rglob('*')) if p.is_file() and not any(x.startswith('build-') for x in p.relative_to(out).parts)]
    (out/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(out))+'\n' for p in files))
    archive=Path(str(out)+'.tar.gz')
    with tarfile.open(archive,'w:gz') as t:
        for p in files+[out/'SHA256SUMS']:t.add(p,arcname=out.name+'/'+str(p.relative_to(out)))
    print((out/'status.txt').read_text(),end='');print('Upload: '+str(archive),flush=True)
    return code

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:print('ERROR: '+str(e),file=sys.stderr);sys.exit(1)
