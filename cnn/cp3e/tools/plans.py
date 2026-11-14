import re
from pathlib import Path
from common import require
from electrical import unescape

def local_targets(evidence):
    import json
    electrical=json.loads((evidence/'electrical-after.json').read_text())
    sram=sorted({r['pin'] for c in electrical.values() for r in c['violations'] if r['domain']=='sram' and r['type']=='max slew'})
    holds=set()
    for corner in ['tt','ff']:
        for s in re.split(r'(?=^Startpoint:)',(evidence/'reports'/f'final-{corner}-min.rpt').read_text(),flags=re.M):
            m=re.search(r'^\s*([-+\d.]+)\s+slack \(',s,re.M)
            if m and float(m[1])<0.1:
                end=re.search(r'^Endpoint: (\S+)',s,re.M);require(end is not None,'Hold endpoint absent')
                holds.add(unescape(end[1])+'/D')
    require(len(holds)<=16 and len(sram)<=32,'Unexpected local repair count')
    return [dict(type='hold',pin=p) for p in sorted(holds)]+[dict(type='sram',pin=p) for p in sram]

def critical_drivers(reports,lib,minimum_cap=0.015):
    rows={}
    for s in re.split(r'(?=^Startpoint:)',(Path(reports)/'final-tt-max.rpt').read_text(),flags=re.M):
        m=re.search(r'^\s*([-+\d.]+)\s+slack \(',s,re.M)
        if not m or float(m[1])>=0:continue
        for line in s.split('data arrival time')[0].splitlines():
            m=re.fullmatch(r'\s*(\d+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+[\^v]\s+(\S+)\s+\((\S+)\)',line)
            if not m:continue
            cap,slew,delay=map(float,[m[2],m[3],m[4]]);cell=lib.get(m[7]);pin=unescape(m[6])
            if not cell or cell['sequential'] or cell['kind']!='logic':continue
            if cap>=minimum_cap and slew>=0.16 and delay>=0.20:
                inst=pin.rsplit('/',1)[0];row=dict(instance=inst,pin=pin,master=m[7],cap_pf=cap,slew_ns=slew,delay_ns=delay)
                if inst not in rows or delay>rows[inst]['delay_ns']:rows[inst]=row
    return sorted(rows.values(),key=lambda x:(-x['delay_ns'],x['instance']))[:24]
