"""Fail-closed structural, power-connectivity and routing-evidence checks."""
import re
from pathlib import Path
from collections import Counter
from common import require
from power import records,norm,routes

def structure(before,after):
    a=Path(before).read_text();b=Path(after).read_text();result={}
    for kind in ['COMPONENTS','PINS']:
        aa,bb=records(a,kind),records(b,kind)
        require({n:norm(v) for n,v in aa.items()}=={n:norm(v) for n,v in bb.items()},kind+' changed')
        result[kind.lower()]=len(aa)
    aa,bb=records(a,'NETS'),records(b,'NETS');require(aa.keys()==bb.keys(),'Signal net set changed')
    changed=[]
    for n in aa:
        # GRT marks propagated clock nets. Allow only SIGNAL -> CLOCK labels;
        # preserve all endpoint connections and every other DEF attribute.
        av,bv=norm(aa[n]),norm(bb[n])
        if av!=bv:
            require('+ USE SIGNAL' in av and av.replace('+ USE SIGNAL','+ USE CLOCK')==bv,'Signal connectivity/attributes changed: '+n)
            changed.append(n)
    for kind,pat in [('die',r'^DIEAREA[^;]*;'),('rows',r'^ROW[^;]*;'),('tracks',r'^TRACKS[^;]*;')]:
        aa1=Counter(norm(s) for s in re.findall(pat,a,re.M));bb1=Counter(norm(s) for s in re.findall(pat,b,re.M))
        require(aa1 and aa1==bb1,'Floorplan '+kind+' changed')
    result.update(status='PLACEMENT_AND_LOGIC_PRESERVED',signal_nets=len(aa),clock_use_labels_added=changed)
    return result

def power_preserved(before,after):
    a=Path(before).read_text();b=Path(after).read_text()
    ar,br=routes(a),routes(b)
    require(ar.keys()==br.keys(),'Routing changed special-net set')
    for n in ar:
        ah,bh=ar[n][0],br[n][0]
        require(ah==bh or ('+ USE SIGNAL' in ah and ah.replace('+ USE SIGNAL','+ USE CLOCK')==bh),'Routing changed special-net assignments: '+n)
        require(ar[n][1]==br[n][1],'Routing changed special-net geometry: '+n)
    for sec in ['VIAS']:
        require({n:norm(v) for n,v in records(a,sec).items()}=={n:norm(v) for n,v in records(b,sec).items()},'Routing changed via definitions')
    return True

def logs(path):
    s=Path(path).read_text()
    require(not re.search(r'\[ERROR\b|^Error:|CP3B_\S*_FAILED',s,re.M),'Tool log contains errors: '+str(path))
    return s

def routing(out):
    s=logs(out/'route.log')
    for net in ['VDD','VSS','VDDIO','VSSIO']:
        require(s.count('CP3B_POWER_GRID_OK '+net+'\n')==1,'Missing power-grid result: '+net)
        require('All shapes on net '+net+' are connected.' in s,'Missing OpenROAD connectivity proof: '+net)
    require(s.count('CP3B_GLOBAL_ROUTE_COMPLETE')==1,'Global routing did not finish')
    require(s.count('Final congestion report:')==1,'Missing/ambiguous final congestion report')
    report=s.split('Final congestion report:')[1]
    rr=re.findall(r'^\s*(Metal[1-4]|TopMetal1|Total)\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+(\d+) /\s*(\d+) /\s*(\d+)\s*$',report,re.M)
    require([r[0] for r in rr]==['Metal1','Metal2','Metal3','Metal4','TopMetal1','Total'],'Unrecognized congestion table')
    require(all(int(x)==0 for r in rr for x in r[4:]),'Global routing overflow is nonzero')
    nets=records((out/'global.def').read_text(),'NETS')
    eligible={n for n,v in nets.items() if len(re.findall(r'\( [^()]+ \)',v))>=2}
    # Strict guide parser also rejects empty/duplicate nets and malformed boxes.
    guide=(out/'global.guide').read_text();pos=0;guided={};boxes=0
    for m in re.finditer(r'(\S+)\n\(\n([^)]*)\)\n',guide):
        require(not guide[pos:m.start()].strip(),'Malformed route-guide header');pos=m.end()
        name=m[1];require(name not in guided,'Duplicate route-guide net');count=0
        for line in m[2].splitlines():
            mm=re.fullmatch(r'(\d+) (\d+) (\d+) (\d+) (Metal[1-4]|TopMetal1)',line)
            require(mm is not None,'Malformed route-guide rectangle')
            x1,y1,x2,y2=map(int,mm.groups()[:4]);require(x1<x2 and y1<y2,'Empty route-guide rectangle');count+=1
        require(count>0,'Empty route-guide net');guided[name]=count;boxes+=count
    require(not guide[pos:].strip(),'Unparsed route-guide data')
    require(set(guided)==eligible,'Missing/extra routed nets: '+str(len(eligible-set(guided)))+'/'+str(len(set(guided)-eligible)))
    m=re.search(r'Routed nets: (\d+)',s);require(m is not None and int(m[1])==len(eligible),'Routed-net total differs')
    w=re.search(r'Total wirelength: (\d+) um',s);require(w is not None,'Missing wirelength result')
    warnings=re.findall(r'^\[WARNING ([^]]+)\] (.*)$',s,re.M)
    require(all(code=='GRT-0281' and 'has a large fanout' in msg for code,msg in warnings),'Unexpected routing warning; review required')
    return dict(status='PDN_CONNECTIVITY_AND_GLOBAL_ROUTE_PASS',power_nets_connected=['VDD','VSS','VDDIO','VSSIO'],
        eligible_nets=len(eligible),guided_nets=len(guided),guide_rectangles=boxes,
        single_or_zero_terminal_nets=len(nets)-len(eligible),overflow=0,global_wirelength_um=int(w[1]),
        congestion=[dict(layer=r[0],resource=int(r[1]),demand=int(r[2]),usage_percent=float(r[3])) for r in rr],
        warnings=[dict(code=c,message=m) for c,m in warnings],clock_tree_synthesized=False,
        detailed_routing_complete=False,timing_closed=False,electrical_closed=False,cp0c_electrical='PENDING')
