"""Paired clean synthesis and geometric placement in isolated result directories."""
import json,os,shutil
from pathlib import Path
from common import command,put,require
from checks import mapped,geometry,compare
ROOT=Path(__file__).resolve().parents[1]
ADDED=['cnn/cp2a/rtl/cnn_mac.sv','cnn/cp2a/rtl/cnn_quant.sv','cnn/cp2a/rtl/cnn_engine.sv',
       'cnn/cp2b/rtl/cnn_obi.sv','cnn/cp2b/rtl/cnn_sram_4k.sv','cnn/cp2b/rtl/cnn_peripheral.sv','cnn/cp2b/integration/cnn_croc.sv']

def environment(repo):
    env=dict(os.environ,CROC_PDK='sg13cmos5l',PROJ_NAME='croc',TOP_DESIGN='croc_chip',OMP_NUM_THREADS='2')
    for n in ['CELLS','SRAMS','IOS']:env['PDK_DIR_LIB_'+n]=str(repo/'ihp13/pdk/lib')
    for n in ['TECH','CELLS','SRAMS','IOS','BOND']:env['PDK_DIR_LEF_'+n]=str(repo/'ihp13/pdk/lef')
    env.update(PDK_STDCELL_LIB_TT_FILE='sg13g2_stdcell_typ_1p20V_25C.lib',
        PDK_STDCELL_LIB_FF_FILE='sg13g2_stdcell_fast_1p32V_m40C.lib',
        PDK_IO_LIB_TT_FILE='sg13cmos5l_io_typ_1p2V_3p3V_25C.lib',PDK_IO_LIB_FF_FILE='sg13cmos5l_io_fast_1p32V_3p6V_m40C.lib',
        PDK_TECH_LEF_FILE='sg13cmos5l_tech.lef',PDK_STDCELL_LEF_FILE='sg13g2_stdcell.lef',
        PDK_IO_LEF_FILE='sg13cmos5l_io.lef',PDK_BONDPAD_LEF='bondpad5l_70x70.lef')
    return env

def prepare_synthesis(repo,out,variant,lines):
    syn=out/variant/'yosys'
    for n in ['scripts','src']:shutil.copytree(repo/'yosys'/n,syn/n)
    if variant=='baseline':
        lines=[x for x in lines if x not in [str(repo/n) for n in ADDED]]
        shutil.copyfile(repo/'cnn/cp2c-state/user_domain.sv.before',syn/'src/user_domain.sv')
        lines=[str(syn/'src/user_domain.sv') if x==str(repo/'rtl/user_domain.sv') else x for x in lines]
    (syn/'src/croc.flist').write_text('\n'.join(lines)+'\n')
    script=syn/'scripts/yosys_synthesis.tcl'
    script.write_text(script.read_text()+'''
# CP3A: preserve a structural audit around metadata-only cleanup.
yosys write_json out/mapped-with-metadata.json
yosys delete {t:$scopeinfo}
yosys hierarchy -check -top $top_design
yosys check -assert
yosys write_json out/mapped.json
yosys write_verilog -noattr -noexpr -nohex -nodec out/croc_yosys.v
''')
    return syn

def prepare_placement(repo,out,variant):
    place=out/variant/'openroad'
    for n in ['scripts','src']:shutil.copytree(repo/'openroad'/n,place/n)
    original=(repo/'openroad/scripts/01_floorplan.tcl').read_text()
    # Reuse exact die/core/pad/firmware placement and row-cut implementation.
    body=original[original.index('set chipH'):original.index('# Save an image before PDN insertion.')]
    require(body.count('cut_rows -halo_width_x')==1,'Unexpected floorplan structure')
    body=body.replace('cut_rows -halo_width_x',(ROOT/'flow/add-macros.tcl').read_text()+'\ncut_rows -halo_width_x')
    script='''source scripts/init_tech_sg13cmos5l.tcl
source scripts/floorplan_util.tcl
read_verilog $::env(CP3A_NETLIST)
link_design croc_chip
read_sdc src/constraints.sdc
source scripts/power_connect.tcl
'''+body+'\n'+(ROOT/'flow/placement.tcl').read_text()
    (place/'run.tcl').write_text(script)
    return place

def execute(repo,out,bender,yosys,openroad):
    command([bender,'script','flist-plus','-t','rtl','-t','synthesis','-t','ihp13','-t','asic','-D','IHP_SG13CMOS5L=1'],out/'bender.f',cwd=repo)
    lines=(out/'bender.f').read_text().splitlines()
    pinned=set(json.loads((ROOT/'pins.json').read_text())['source_files'])|set(ADDED)
    for line in lines:
        if not line.strip() or line.startswith('+'):continue
        path=Path(line)
        require(path.is_absolute() and path.is_relative_to(repo),'Source outside pinned repository: '+line)
        require(str(path.relative_to(repo)) in pinned,'Unpinned synthesis source: '+line)
    for n in ADDED+['rtl/user_domain.sv','rtl/croc_chip.sv','ihp13/tc_clk.sv']:
        require(lines.count(str(repo/n))==1,'Missing/duplicated synthesis source: '+n)
    require(not any(x.endswith('/rtl/tech_cells_generic/tc_clk.sv') or '/rtl/test/' in x for x in lines),'Simulation source in hardware synthesis list')
    results={};env=environment(repo)
    for variant in ['baseline','cnn']:
        print('STAGE: '+variant+' whole-chip technology mapping',flush=True)
        syn=prepare_synthesis(repo,out,variant,lines)
        stats=command([yosys,'-Q','-T','-c',syn/'scripts/yosys_synthesis.tcl'],syn/'synthesis.log',timeout=7200,cwd=syn,env=env)
        synthesis=mapped(syn,repo,variant);put(syn/'summary.json',synthesis)
        print('STAGE: '+variant+' fixed-die floorplan and legal cell placement',flush=True)
        place=prepare_placement(repo,out,variant)
        env.update(CP3A_NETLIST=str(syn/'out/croc_yosys.v'),CP3A_EXTRA_MACROS='2' if variant=='cnn' else '0')
        placement=command([openroad,'-no_init','-exit',place/'run.tcl'],place/'placement.log',timeout=3600,cwd=place,env=env)
        physical=geometry(place,synthesis);put(place/'summary.json',physical)
        results[variant]=dict(synthesis=synthesis,geometry=physical,runtime=dict(synthesis=stats,placement=placement))
    results['delta']=compare(results['baseline'],results['cnn'])
    results['scope']=dict(clean_paired_synthesis=True,historical_layout_reproduced=False,
        fixed_die_floorplan_and_placement=True,pdn_closed=False,routing_closed=False,
        timing_closed=False,electrical_closed=False,cp0c_electrical='PENDING')
    put(out/'comparison.json',results)
    return results
