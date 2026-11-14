"""SRAM supply-via planning on the pinned LEF abstracts, including MX banks.

Via dimensions follow the reviewed CP0C 3x3 TopVia1 construction. This checks
abstract geometry and contacts, not transistor-level supply extraction or EM.
"""
import re
from collections import Counter
from pathlib import Path
from common import require,put
MACRO='RM_IHPSG13_1P_512x32_c2_bm_bist'
VIA='CP3B_SRAM_TV1_3X3'
PIN_COUNTS={'VDD!':36,'VDDARRAY!':32,'VSS!':36}

def norm(s):return ' '.join(s.split())
def section(s,name):
    m=re.search(r'^'+re.escape(name)+r'\b[^\n]*\n(.*?)^END '+re.escape(name)+r'\b[^\n]*',s,re.M|re.S)
    require(m is not None,'Missing DEF section '+name);return m
def records(s,name):
    rows=re.findall(r'^\s*-\s+(\S+)\s(.*?);',section(s,name)[1],re.M|re.S)
    require(len(rows)==len({n for n,b in rows}),'Duplicate DEF records: '+name);return dict(rows)
def inside(a,b):return b[0]<=a[0]<a[2]<=b[2] and b[1]<=a[1]<a[3]<=b[3]
def overlap(a,b):return min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])
def moved(b,x,y):return [b[0]+x,b[1]+y,b[2]+x,b[3]+y]
def via_boxes():
    return [('Metal4',[-1150,-1150,1150,1150]),('TopMetal1',[-1470,-1470,1470,1470])]+[
        ('TopVia1',[x-210,y-210,x+210,y+210]) for x in [-840,0,840] for y in [-840,0,840]]
def routes(s):
    result={}
    for net,body in records(s,'SPECIALNETS').items():
        parts=re.split(r'\s*(?:\+\s+ROUTED|\bNEW)\s+',body)
        result[net]=(norm(parts[0]),Counter(norm(p) for p in parts[1:] if p.strip()))
    return result
def pins(lef):
    text=Path(lef).read_text();result={}
    for name,count in PIN_COUNTS.items():
        m=re.search(r'^  PIN '+re.escape(name)+r'\s*\n(.*?)^  END '+re.escape(name)+r'\s*$',text,re.M|re.S)
        require(m is not None,'Missing SRAM supply pin: '+name)
        rr=re.findall(r'LAYER (\S+)\s*;\s*RECT ([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+)\s*;',m[1])
        require(len(rr)==count and all(x[0]=='Metal4' for x in rr),'SRAM power abstract changed: '+name)
        result[name]=[[round(float(v)*1000) for v in row[1:]] for row in rr]
    return result
def macro_pins(text,lef,expected):
    local=pins(lef);result={}
    for name,body in records(text,'COMPONENTS').items():
        if body.split()[0]!=MACRO:continue
        m=re.search(r'\+ FIXED \( (\d+) (\d+) \) (N|FS)\b',body)
        require(m is not None,'SRAM must be fixed R0/MX: '+name)
        x,y=int(m[1]),int(m[2]);orient=m[3]
        result[name]={pin:[moved(b,x,y) if orient=='N' else [x+b[0],y+191340-b[3],x+b[2],y+191340-b[1]] for b in boxes] for pin,boxes in local.items()}
    require(len(result)==expected,'Wrong SRAM count in DEF');return result

def plan(defpath,lefpath,expected):
    text=Path(defpath).read_text();require('UNITS DISTANCE MICRONS 1000 ;' in text,'Wrong DEF units')
    require(VIA not in text,'SRAM via repair already applied')
    macros=macro_pins(text,lefpath,expected);rr=routes(text);stripes={};placements=[]
    for net in ['VDD','VSS']:
        stripes[net]=[]
        for route,count in rr[net][1].items():
            m=re.fullmatch(r'TopMetal1 (\d+) \+ SHAPE STRIPE \( (\d+) (\d+) \) \( (\d+) (\d+) \)',route)
            if not m:continue
            w,x1,y1,x2,y2=map(int,m.groups())
            if w!=3000:continue # Wide pad-to-ring connections are outside SRAM footprints.
            require(y1==y2,'Unexpected TM1 power stripe')
            stripes[net].extend([[min(x1,x2),y1-w//2,max(x1,x2),y1+w//2]]*count)
        require(len(stripes[net])>=12,'Incomplete TM1 grid')
    for a in stripes['VDD']:
        require(not any(overlap(a,b) for b in stripes['VSS']),'Opposite power stripes overlap')
    # A newer PDN generator may already contact macros. Do not silently add
    # duplicate arrays: require review of any generated TopVia1 inside an SRAM.
    definitions=records(text,'VIAS');bounds=[]
    for inst,body in records(text,'COMPONENTS').items():
        if inst not in macros:continue
        m=re.search(r'\+ FIXED \( (\d+) (\d+) \) (N|FS)\b',body)
        x,y=int(m[1]),int(m[2]);bounds.append([x,y,x+416640,y+191340])
    for net in ['VDD','VSS']:
        for route in rr[net][1]:
            m=re.fullmatch(r'Metal4 0 \+ SHAPE STRIPE \( (\d+) (\d+) \) (\S+)',route)
            if m and 'TopVia1' in definitions.get(m[3],''):
                x,y=int(m[1]),int(m[2])
                require(not any(b[0]<=x<=b[2] and b[1]<=y<=b[3] for b in bounds),'Pre-existing TopVia1 in SRAM; review generator behavior')
    coverage=[]
    for inst,pp in sorted(macros.items()):
        for pin,boxes in pp.items():
            net='VSS' if pin=='VSS!' else 'VDD';other='VDD' if net=='VSS' else 'VSS'
            for index,pr in enumerate(boxes):
                contacts=[]
                for stripe in stripes[net]:
                    if not overlap(pr,stripe):continue
                    x=(max(pr[0],stripe[0])+min(pr[2],stripe[2]))//2
                    y=(max(pr[1],stripe[1])+min(pr[3],stripe[3]))//2
                    lower=moved(via_boxes()[0][1],x,y);upper=moved(via_boxes()[1][1],x,y)
                    # Partial edge crossings are skipped; every pin rectangle
                    # must still receive at least one fully contained array.
                    if not (inside(lower,pr) and inside(upper,stripe)):continue
                    require(x%5==y%5==0,'Via center off 5 nm grid')
                    require(not any(overlap(upper,b) for b in stripes[other]),'Via shorts opposite power stripe')
                    require(not any(overlap(lower,b) for p,bb in pp.items() if ('VSS' if p=='VSS!' else 'VDD')==other for b in bb),'Via shorts opposite SRAM supply')
                    row=dict(instance=inst,pin=pin,pin_index=index,net=net,x=x,y=y,pin_rect=pr,stripe_rect=stripe)
                    placements.append(row);contacts.append([x,y])
                require(contacts,'Uncontacted SRAM supply rectangle: '+inst+'/'+pin+'/'+str(index))
                coverage.append(dict(instance=inst,pin=pin,index=index,contacts=contacts))
    require(len({(p['x'],p['y']) for p in placements})==len(placements),'Duplicate via array')
    require(expected*104<=len(placements)<=expected*400,'Unexpected via-array count')
    return dict(status='SRAM_VIA_PLAN_VERIFIED',sram_macros=expected,via_name=VIA,arrays=len(placements),cuts=9*len(placements),
        abstract_supply_rectangles=len(coverage),coverage=coverage,via_boxes=via_boxes(),placements=placements)

def emit(p,out):
    put(out/'via-plan.json',p)
    (out/'via-plan.tsv').write_text('net\tinstance\tpin\tx\ty\n'+''.join('\t'.join(str(r[k]) for k in ['net','instance','pin','x','y'])+'\n' for r in p['placements']))

def audit(before,after,plan):
    a=Path(before).read_text();b=Path(after).read_text()
    def rest(s):
        for n in ['SPECIALNETS','VIAS']:
            m=section(s,n);s=s[:m.start()]+s[m.end():]
        return norm(s)
    require(rest(a)==rest(b),'SRAM repair changed non-power content')
    av,bv=records(a,'VIAS'),records(b,'VIAS')
    require(set(bv)==set(av)|{VIA} and all(norm(av[n])==norm(bv[n]) for n in av),'Unexpected via-definition changes')
    boxes=re.findall(r'\+ RECT (\S+) \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)',norm(bv[VIA]))
    require(Counter((r[0],tuple(map(int,r[1:]))) for r in boxes)==Counter((n,tuple(b)) for n,b in via_boxes()),'Inserted via dimensions differ')
    ar,br=routes(a),routes(b);require(ar.keys()==br.keys(),'Power net set changed')
    for n in ar:
        require(ar[n][0]==br[n][0],'Power assignments changed')
        expected=Counter(ar[n][1]);expected.update(f'Metal4 0 + SHAPE STRIPE ( {r["x"]} {r["y"]} ) {VIA}' for r in plan['placements'] if r['net']==n)
        require(br[n][1]==expected,'Missing/extra power geometry on '+n)
    return dict(status='SRAM_VIA_DELTA_VERIFIED',arrays=plan['arrays'],cuts=plan['cuts'],supply_rectangles_contacted=plan['abstract_supply_rectangles'])
