"""Negative controls use isolated temporary files; accepted results stay read-only."""
import json,re,shutil,tempfile
from pathlib import Path
from common import require,sha,manifest
from power import plan,audit,VIA,section
from checks import structure,routing,power_preserved

def controls(out,repo):
    results=[];source=out/'cnn'
    def rejects(name,fn):
        try:fn()
        except RuntimeError as e:results.append(dict(name=name,status='REJECTED_AS_REQUIRED',reason=str(e)));return
        raise RuntimeError('Corruption control unexpectedly passed: '+name)
    with tempfile.TemporaryDirectory(prefix='cp3b-controls-') as tmp:
        d=Path(tmp);p=json.loads((source/'via-plan.json').read_text())
        damaged=d/'damaged.def';original=(source/'pdn.def').read_text()
        damaged.write_text(re.sub(r'\s+NEW Metal4 0 \+ SHAPE STRIPE \( \d+ \d+ \) '+VIA,'',original,count=1))
        require(damaged.read_text()!=original,'Missing-via fixture did not mutate')
        rejects('missing_sram_via',lambda:audit(source/'via-before.def',damaged,p))
        damaged.write_text(original.replace('+ FIXED ( 350240 352640 ) FS','+ FIXED ( 350245 352640 ) FS',1))
        require(damaged.read_text()!=original,'Macro-movement fixture did not mutate')
        rejects('moved_mirrored_sram',lambda:structure(source/'before.def',damaged))
        grid=(source/'grid.def').read_text()
        # Remove all horizontal VDD core stripes; the supply planner must refuse.
        n=grid.index('- VDD ',section(grid,'SPECIALNETS').start());end=grid.index(';',n)
        body=grid[n:end]
        body=re.sub(r'\s+(?:\+ ROUTED|NEW) TopMetal1 3000 \+ SHAPE STRIPE \( \d+ \d+ \) \( \d+ \d+ \)','',body)
        damaged.write_text(grid[:n]+body+grid[end:])
        require(damaged.read_text()!=grid,'Missing-stripe fixture did not mutate')
        rejects('missing_sram_supply_stripes',lambda:plan(damaged,repo/'ihp13/pdk/lef/RM_IHPSG13_1P_512x32_c2_bm_bist.lef',4))
        # A native generator that starts adding its own SRAM vias must trigger
        # review, rather than silently stacking the CP3B repair onto them.
        row=p['placements'][0]
        via=re.search(r'^\s*- (\S+)\s+\+ VIARULE viaTop1Array',grid,re.M)[1]
        insertion=f"\n    NEW Metal4 0 + SHAPE STRIPE ( {row['x']} {row['y']} ) {via}"
        damaged.write_text(grid[:end]+insertion+grid[end:])
        rejects('preexisting_sram_supply_via',lambda:plan(damaged,repo/'ihp13/pdk/lef/RM_IHPSG13_1P_512x32_c2_bm_bist.lef',4))
        for name in ['global.def','global.guide','route.log']:shutil.copyfile(source/name,d/name)
        guide=(d/'global.guide').read_text();(d/'global.guide').write_text(guide[guide.index(')\n')+2:])
        rejects('missing_route_guide',lambda:routing(d));(d/'global.guide').write_text(guide)
        log=(d/'route.log').read_text();bad=re.sub(r'(Total\s+\d+\s+\d+\s+[\d.]+%\s+0 /\s*0 /\s*)0',r'\g<1>1',log)
        require(bad!=log,'Overflow fixture did not mutate');(d/'route.log').write_text(bad)
        rejects('nonzero_routing_overflow',lambda:routing(d))
        (d/'route.log').write_text(log.replace('CP3B_POWER_GRID_OK VSSIO\n',''))
        rejects('missing_io_ground_connectivity',lambda:routing(d))
        damaged.write_text(original.replace(VIA,'BROKEN_SUPPLY_VIA',1))
        rejects('changed_pdn_during_routing',lambda:power_preserved(source/'pdn.def',damaged))
        f=d/'input';f.write_text('accepted\n');(d/'SHA256SUMS').write_text(sha(f)+'  input\n');f.write_text('modified\n')
        rejects('changed_prerequisite_file',lambda:manifest(d))
    require(len(results)==9,'Incomplete controls')
    return dict(status='ALL_CORRUPTION_CONTROLS_PASSED',count=len(results),tests=results)
