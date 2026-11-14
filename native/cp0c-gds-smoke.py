#!/usr/bin/env python3
"""Exercise real GDS I/O, Ruby DRC and marker parsing before a chip run."""
import json
from pathlib import Path
import platform
import subprocess
import sys
import klayout.db as db
import klayout.rdb as rdb
from cp0c_gds_runtime import klayout_environment

def verify_report(path):
    """Resolve category identities with KLayout, not XML path quoting."""
    report=rdb.ReportDatabase();report.load(str(path))
    if report.top_cell_name != 'SMOKE': raise RuntimeError('Smoke report top differs.')
    registered={c.name() for c in report.each_category()}
    if registered != {'EXPECTED_WIDTH','EXPECTED_CLEAN'}:
        raise RuntimeError(f'Smoke registered categories differ: {registered}')
    counts={}
    for item in report.each_item():
        category=report.category_by_id(item.category_id())
        if category is None: raise RuntimeError('Smoke marker has an unknown category.')
        name=category.name();counts[name]=counts.get(name,0)+1
    if not counts.get('EXPECTED_WIDTH') or set(counts) != {'EXPECTED_WIDTH'}:
        raise RuntimeError(f'Smoke marker counts differ: {counts}')
    return counts['EXPECTED_WIDTH']

def main(tool, destination):
    tool=Path(tool).resolve(strict=True)
    out=Path(destination).resolve(); out.mkdir(parents=True,exist_ok=True)
    if db.__version__ != '0.30.9': raise RuntimeError('Expected Python KLayout 0.30.9.')
    lay=db.Layout();lay.dbu=.001;top=lay.create_cell('SMOKE')
    top.shapes(lay.layer(8,0)).insert(db.Box(0,0,50,1000))
    lay.write(str(out/'smoke.gds'))
    script=out/'smoke.drc'
    script.write_text('source($input, "SMOKE")\nreport("CP0C smoke", $report)\n'
        'input(8,0).width(0.10).output("EXPECTED_WIDTH", "Deliberately narrow metal")\n'
        'input(8,0).width(0.01).output("EXPECTED_CLEAN", "Valid narrow threshold")\n'
        'puts "CP0C_KLAYOUT_SMOKE_COMPLETE"\n')
    cmd=[str(tool),'-v',f'input={out}/smoke.gds','-v',f'report={out}/smoke.lyrdb',str(script)]
    env=klayout_environment(tool)
    (out/'runtime.json').write_text(json.dumps({'executable':str(tool),
        'DYLD_LIBRARY_PATH':env['DYLD_LIBRARY_PATH'],'scope':'strmrun child only'},indent=2)+'\n')
    result=subprocess.run(cmd,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (out/'console.log').write_text(result.stdout);print(result.stdout,flush=True)
    if result.returncode: raise RuntimeError('KLayout Ruby smoke failed.')
    if 'CP0C_KLAYOUT_SMOKE_COMPLETE' not in result.stdout: raise RuntimeError('Smoke did not finish.')
    markers=verify_report(out/'smoke.lyrdb')
    (out/'result.json').write_text(json.dumps({'klayout':db.__version__,'platform':platform.platform(),
        'machine':platform.machine(),'expected_violation_markers':markers,'result':'PASS'},indent=2)+'\n')
    print('KLAYOUT_SMOKE_PASS',flush=True)

if __name__=='__main__': main(sys.argv[1],sys.argv[2])
