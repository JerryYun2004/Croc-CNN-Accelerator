#!/usr/bin/env python3
import argparse,json,os,platform,shutil,subprocess,sys,tarfile,tempfile
from pathlib import Path
from datetime import datetime
from common import command,git_state,manifest,put,require,sha
from checks import prerequisite,synthesis,timing
from fixtures import generate
ROOT=Path(__file__).resolve().parents[1]

def tool(name,preferred=None):
    if preferred and Path(preferred).is_file():return str(preferred)
    p=shutil.which(name);require(p,'Missing installed tool: '+name);return p

def sources(cnn,typed=False):
    third=ROOT/'third_party'
    opts=[ROOT/'flow/upstream.vlt','+define+FUNCTIONAL']
    common=[cnn/'cp2a/rtl'/n for n in ['cnn_mac.sv','cnn_quant.sv','cnn_engine.sv']]
    common += [ROOT/'rtl'/n for n in ['cnn_obi.sv','cnn_sram_4k.sv','cnn_peripheral.sv']]
    common += [third/n for n in ['RM_IHPSG13_1P_512x32_c2_bm_bist.v','RM_IHPSG13_1P_core_behavioral_bm_bist.v']]
    if not typed:return opts+common
    opts += ['+incdir+'+str(third/'rtl/obi/include'),'+incdir+'+str(third/'rtl/common_cells/include')]
    packages=[third/n for n in ['rtl/common_cells/cf_math_pkg.sv','rtl/obi/obi_pkg.sv','croc_pkg.sv','user_pkg.sv']]
    deps=[third/'rtl/common_cells'/n for n in ['addr_decode.sv','addr_decode_dync.sv','delta_counter.sv','fifo_v3.sv']]
    deps += [third/'rtl/obi'/n for n in ['obi_demux.sv','obi_err_sbr.sv']]
    return opts+packages+common+deps+[ROOT/'integration/cnn_croc.sv',ROOT/'integration/user_domain.sv']

def build(verilator,top,sv,cpp,out):
    directory=out/('build-'+top)
    args=[verilator,'--cc','--exe','--build','--assert','-j','2','-Wall','--top-module',top,'--Mdir',directory]
    flags='-O2 -std=c++17'
    if platform.system()=='Darwin':
        sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
        cxx=subprocess.check_output(['xcrun','--find','clang++'],text=True).strip()
        flags+=' -isysroot '+sdk
        args+=['--compiler','clang','-LDFLAGS','-isysroot '+sdk,'-MAKEFLAGS','CXX='+cxx+' LINK='+cxx]
    record=command(args+['-CFLAGS',flags]+sv+[cpp],out/'logs'/('build-'+top+'.log'),timeout=900)
    exe=directory/('V'+top);require(exe.is_file(),'Simulation executable missing')
    if platform.system()=='Darwin':
        fmt=subprocess.check_output(['file',str(exe)],text=True).strip()
        require('Mach-O' in fmt and 'arm64' in fmt,'Expected native arm64 simulator');record['format']=fmt
    return exe,record

def synth_script(lib):
    require('"' not in str(lib) and '\n' not in str(lib),'Unsupported Liberty filename')
    s='read_verilog -D FUNCTIONAL -lib work/RM_IHPSG13_1P_512x32_c2_bm_bist.v\n'
    s+='read_verilog -sv work/cnn_mac.sv work/cnn_quant.sv work/cnn_engine.sv work/cnn_obi.sv work/cnn_sram_4k.sv work/cnn_peripheral.sv\n'
    s+='hierarchy -check -top cnn_peripheral\nsynth -top cnn_peripheral -flatten\ncheck -assert\n'
    s+=f'read_liberty -lib -ignore_miss_func "{lib}"\ndfflibmap -liberty "{lib}"\nabc -liberty "{lib}" -constr work/abc.constr -D 10000\nclean\ncheck -assert\n'
    s+=f'tee -o reports/synthesis-before.json stat -json -liberty "{lib}"\n'
    s+='delete t:$scopeinfo\nselect -assert-none t:$scopeinfo\ncheck -assert\n'
    s+=f'tee -o reports/synthesis-stat.json stat -json -liberty "{lib}"\n'
    s+='write_verilog -noattr -noexpr work/peripheral_mapped.v\nwrite_json work/mapped.json\n'
    return s

def execute(out,cnn,evidence,verilator,yosys,openroad,tech):
    out=Path(out);cnn=Path(cnn)
    for n in ['logs','reports','work']:(out/n).mkdir(parents=True,exist_ok=True)
    logs=out/'logs';reports=out/'reports';work=out/'work'
    print('STAGE: regenerate and compare all accepted scalar fixtures',flush=True)
    fixtures=generate(cnn,out/'fixtures');require(len(fixtures)==55,'Fixture count differs')
    for p in (out/'fixtures').glob('*.bin'):require(sha(p)==sha(evidence/'fixtures'/p.name),'Reference fixture differs: '+p.name)
    put(reports/'fixtures.json',fixtures)
    print('STAGE: peripheral and Croc user-domain lint',flush=True)
    command([verilator,'--lint-only','--assert','-Wall','--top-module','cnn_peripheral',*sources(cnn)],logs/'lint-peripheral.log',timeout=120)
    command([verilator,'--lint-only','--assert','-Wall','--top-module','user_domain',*sources(cnn,True)],logs/'lint-user-domain.log',timeout=120)
    print('STAGE: SRAM/OBI protocol, inference, reset, fault and C-driver tests',flush=True)
    exe,build1=build(verilator,'cp2b_tb',sources(cnn)+[ROOT/'tests/cp2b_tb.sv'],ROOT/'tests/peripheral.cpp',out)
    command([exe,out/'fixtures','55'],reports/'peripheral.json',timeout=900)
    sim=json.loads((reports/'peripheral.json').read_text())
    require(sim['status']=='CP2B_SIM_PASS' and sim['full_inferences_checked']==120 and sim['protocol_checks']==4336 and sim['driver_checks']==10,'Peripheral suite incomplete')
    require(sim['obi_requests']-sim['obi_responses']==sim['reset_cancelled_requests']==2,'OBI transaction accounting differs')
    require(sim['direct_sram_cycles']==302451,'Fixed-latency inference count differs')
    print('STAGE: staged Croc user-domain integration and C driver through real demux',flush=True)
    exe,build2=build(verilator,'user_tb',sources(cnn,True)+[ROOT/'tests/user_tb.sv'],ROOT/'tests/user.cpp',out)
    command([exe,out/'fixtures','55'],reports/'user-domain.json',timeout=900)
    user=json.loads((reports/'user-domain.json').read_text())
    require(user['status']=='USER_DOMAIN_PASS' and user['inferences']==55,'User-domain suite incomplete')
    cc=tool('cc');args=[cc,'-std=c11','-Wall','-Wextra','-Werror','-ffreestanding','-c',ROOT/'tests/driver_compile.c','-o',work/'driver.o']
    if platform.system()=='Darwin':args += ['-isysroot',subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()]
    command(args,logs/'driver-c11.log',timeout=120);put(reports/'builds.json',dict(peripheral=build1,user_domain=build2))
    print('STAGE: map peripheral logic and verify exactly two physical SRAM instances',flush=True)
    for base in [cnn/'cp2a/rtl',ROOT/'rtl']:
        for p in base.glob('*.sv'):shutil.copyfile(p,work/p.name)
    shutil.copyfile(ROOT/'third_party/RM_IHPSG13_1P_512x32_c2_bm_bist.v',work/'RM_IHPSG13_1P_512x32_c2_bm_bist.v')
    shutil.copyfile(ROOT/'flow/abc.constr',work/'abc.constr')
    (work/'synthesis.ys').write_text(synth_script(tech['liberty']))
    command([yosys,'-s',work/'synthesis.ys'],logs/'synthesis.log',cwd=out,timeout=900)
    area=synthesis(reports/'synthesis-before.json',reports/'synthesis-stat.json',work/'mapped.json',tech['sram_lef']);put(reports/'area.json',area)
    print('STAGE: preliminary 100 MHz TT timing including SRAM Liberty',flush=True)
    env=dict(os.environ,CP2B_LIB=str(tech['liberty']),CP2B_SRAM_LIB=str(tech['sram_liberty']),CP2B_NETLIST=str(work/'peripheral_mapped.v'),CP2B_TECH_LEF=str(tech['technology_lef']),CP2B_CELL_LEF=str(tech['standard_cell_lef']),CP2B_SRAM_LEF=str(tech['sram_lef']))
    command([openroad,'-no_init','-exit',ROOT/'flow/timing.tcl'],logs/'timing.log',env=env,timeout=180)
    require('CP2B_OPENROAD_LEF_LOADED' in (logs/'timing.log').read_text(),'OpenROAD LEF initialization missing')
    sta=timing(logs/'timing.log',work/'mapped.json');put(reports/'timing.json',sta)
    require(sta['setup_pass'],'Preliminary setup budget failed; review timing.log')
    return dict(simulation=sim,user_domain=user,area=area,timing=sta)

def main():
    p=argparse.ArgumentParser();p.add_argument('--cp2a',required=True,type=Path);a=p.parse_args()
    require(platform.system()=='Darwin' and platform.machine()=='arm64','Use the existing native arm64 Mac')
    cnn=ROOT.parent;repo=cnn.parent;evidence=a.cp2a.expanduser().resolve()
    before=git_state(repo);require(before['branch']=='cnn/bringup','Run on cnn/bringup')
    resultroot=cnn/'results';resultroot.mkdir(exist_ok=True)
    out=Path(tempfile.mkdtemp(prefix='cp2b-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=resultroot))
    print('CP2B evidence: '+str(out),flush=True);print('Version: 1.0-sram-obi',flush=True);code=1
    try:
        pins=json.loads((ROOT/'pins.json').read_text());manifest(ROOT)
        print('STAGE: verify accepted CP2A, baseline source and technology pins',flush=True)
        put(out/'prerequisite.json',prerequisite(cnn,evidence,pins));protected={}
        for base in [cnn,cnn/'cp2a',ROOT,evidence]:
            for name,digest in manifest(base).items():protected[str(base/name)]=digest
            protected[str(base/'SHA256SUMS')]=sha(base/'SHA256SUMS')
        for name in list(manifest(ROOT))+['SHA256SUMS']:
            dest=out/'source'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
        for name,digest in json.loads((cnn/'baseline-pin.json').read_text())['source_files'].items():
            require(sha(repo/name)==digest,'Baseline source differs: '+name);protected[str(repo/name)]=digest
        for name,rec in pins['upstream_files'].items():
            require(sha(repo/name)==rec['sha256'],'Pinned upstream source differs: '+name);protected[str(repo/name)]=rec['sha256']
        tech={}
        for name in ['liberty','sram_liberty','sram_lef','technology_lef','standard_cell_lef']:
            tech[name]=(repo/pins[name]['path']).resolve();require(sha(tech[name])==pins[name]['sha256'],'Technology file differs: '+name);protected[str(tech[name])]=pins[name]['sha256']
        verilator=tool('verilator');yosys=tool('yosys',Path.home()/'tools/croc-oss-cad-suite-2025-12-30/oss-cad-suite/bin/yosys')
        openroad=tool('openroad',Path.home()/'tools/croc-openroad-c463e998615210dc322c999fc9a9f56885dbd664/install/bin/openroad')
        versions={'platform':platform.platform()}
        for n,exe,arg in [('verilator',verilator,'--version'),('yosys',yosys,'-V'),('openroad',openroad,'-version')]:
            q=subprocess.run([exe,arg],capture_output=True,text=True,timeout=60);require(q.returncode==0,'Tool version failed: '+n)
            versions[n]=dict(path=exe,launcher_sha256=sha(exe),version=(q.stdout+q.stderr).strip())
        put(out/'tools.json',versions)
        accepted_tools=json.loads((evidence/'tools.json').read_text())
        require(versions['openroad']['launcher_sha256']==accepted_tools['openroad']['launcher_sha256'],
                'Use the exact OpenROAD binary from accepted CP2A; older STA versions mis-handle mixed-library SRAM slew')
        put(out/'protected-inputs.json',protected)
        result=execute(out,cnn,evidence,verilator,yosys,openroad,tech)
        require(all(sha(p)==d for p,d in protected.items()) and git_state(repo)==before,'Source, evidence or Git state changed')
        put(out/'preservation.json',dict(unchanged=True,cp1_cp2a_unchanged=True,croc_rtl_pdk_unchanged=True,git_state_unchanged=True))
        put(out/'conclusion.json',dict(status='CP2B_PASS',version=pins['version'],cp0c_electrical='PENDING',
            scope='SRAM/OBI peripheral and staged Croc user-domain simulation',cve2_firmware_executed=False,
            live_croc_source_modified=False,whole_chip_fit_verified=False,post_layout_timing=False,**result))
        (out/'status.txt').write_text('CP2B_PASS\nCP0C_ELECTRICAL_PENDING\n');code=0
    except Exception as e:
        (out/'status.txt').write_text('CP2B_FAILED\nCP0C_ELECTRICAL_PENDING\n');(out/'failure.txt').write_text(str(e)+'\n');print('STOPPED: '+str(e),flush=True)
    (out/'exit-status.txt').write_text(str(code)+'\n')
    files=[p for p in sorted(out.rglob('*')) if p.is_file() and not any(x.startswith('build-') for x in p.relative_to(out).parts)]
    (out/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(out))+'\n' for p in files))
    archive=Path(str(out)+'.tar.gz')
    with tarfile.open(archive,'w:gz') as t:
        for p in files+[out/'SHA256SUMS']:t.add(p,arcname=out.name+'/'+str(p.relative_to(out)))
    print((out/'status.txt').read_text(),end='');print('Upload: '+str(archive),flush=True);return code

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:print('ERROR: '+str(e),file=sys.stderr);sys.exit(1)
