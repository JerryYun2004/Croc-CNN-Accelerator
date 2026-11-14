"""Paired PDN/global routing from the exact accepted CP3A placements."""
import shutil
from pathlib import Path
from common import command,put,require
from environment import environment
from power import plan,emit,audit
from checks import structure,power_preserved,logs,routing
ROOT=Path(__file__).resolve().parents[1]

def execute(repo,evidence,out,openroad):
    env=environment(repo);results={}
    for variant in ['baseline','cnn']:
        print('STAGE: '+variant+' power grid and SRAM supply contacts',flush=True)
        d=out/variant;d.mkdir()
        source=evidence/variant/'openroad'
        for n in ['scripts','src']:shutil.copytree(source/n,d/n)
        env['CP3B_DEF']=str(source/'placed.def')
        times={}
        times['grid']=command([openroad,'-no_init','-exit',ROOT/'flow/grid.tcl'],d/'pdn.log',cwd=d,env=env,timeout=1200)
        require('CP3B_GRID_CREATED' in logs(d/'pdn.log'),'Grid creation incomplete')
        require(not (d/'failed-vias.rpt').exists() or (d/'failed-vias.rpt').stat().st_size==0,'PDN generation reported failed vias')
        put(d/'import-audit.json',structure(source/'placed.def',d/'before.def'))
        put(d/'grid-structure.json',structure(d/'before.def',d/'grid.def'))
        p=plan(d/'grid.def',repo/'ihp13/pdk/lef/RM_IHPSG13_1P_512x32_c2_bm_bist.lef',4 if variant=='cnn' else 2);emit(p,d)
        if variant=='cnn':
            times['connectivity_control']=command([openroad,'-no_init','-exit',ROOT/'flow/control-power.tcl'],d/'control-power.log',cwd=d,env=env)
            text=(d/'control-power.log').read_text()
            require('CP3B_EXPECTED_UNCONTACTED_SRAM_REJECTION' in text and '[ERROR PSM-0069]' in text,'Missing engine-level negative-control result')
            for inst in {r['instance'] for r in p['placements']}:
                for pin in ['VDD!','VDDARRAY!']:
                    require('Unconnected instance '+inst+'/'+pin+' at location' in text,'Negative control did not detect SRAM supply: '+inst+'/'+pin)
            put(d/'connectivity-control.json',dict(status='REJECTED_AS_REQUIRED',missing_sram_supply_terminals=8,
                expected_error='PSM-0069',scope='Uncontacted generated grid; final repaired grid must pass'))
        env.update(CP3B_ODB=str(d/'grid.odb'),CP3B_PLAN=str(d/'via-plan.tsv'),CP3B_BEFORE_DEF=str(d/'via-before.def'),
            CP3B_AFTER_DEF=str(d/'pdn.def'),CP3B_AFTER_ODB=str(d/'pdn.odb'),CP3B_ARRAYS=str(p['arrays']),CP3B_MACROS=str(p['sram_macros']))
        times['sram_vias']=command([openroad,'-no_init','-exit',ROOT/'flow/sram-vias.tcl'],d/'vias.log',cwd=d,env=env)
        logs(d/'vias.log');via=audit(d/'via-before.def',d/'pdn.def',p);put(d/'via-audit.json',via)
        print('STAGE: '+variant+' four-supply connectivity and global routing',flush=True)
        times['route']=command([openroad,'-no_init','-exit',ROOT/'flow/route.tcl'],d/'route.log',cwd=d,env=env,timeout=3600)
        physical=structure(source/'placed.def',d/'global.def');put(d/'preservation-audit.json',physical)
        power_preserved(d/'pdn.def',d/'global.def')
        result=routing(d);result.update(sram=via,preservation=physical,runtime=times);put(d/'summary.json',result);results[variant]=result
    results['delta']=dict(guided_nets=results['cnn']['guided_nets']-results['baseline']['guided_nets'],
        global_wirelength_um=results['cnn']['global_wirelength_um']-results['baseline']['global_wirelength_um'],
        sram_via_arrays=results['cnn']['sram']['arrays']-results['baseline']['sram']['arrays'])
    put(out/'comparison.json',results);return results
