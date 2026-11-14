import re,math,json
from collections import Counter
from pathlib import Path
from common import require,sha
from power import records,norm,routes
from logic import graph,library

def cell_supplies(cells,power,lib):
    count=0
    for inst,master in cells.items():
        if master not in lib:continue
        for supply in ['VDD','VSS']:
            assignments=[n for n,terms in power.items() if (inst,supply) in terms or ('*',supply) in terms]
            require(assignments==[supply],'Missing/misassigned standard-cell supply: '+inst+'/'+supply)
        count+=1
    return count

def pdn(before,after,libpath):
    a=Path(before).read_text();b=Path(after).read_text();ar,br=routes(a),routes(b)
    require(ar.keys()==br.keys(),'Special net set changed')
    for n in ar:require(ar[n][1]==br[n][1],'Special routing geometry changed: '+n)
    require({n:norm(v) for n,v in records(a,'VIAS').items()}=={n:norm(v) for n,v in records(b,'VIAS').items()},'PDN via definitions changed')
    lib=library(libpath);ag=graph(before,lib);bg=graph(after,lib);allpg=set();count=0
    for n,pins in bg['power'].items():require(not allpg.intersection(pins),'Power pin assigned twice');allpg.update(pins)
    for net,pins in ag['power'].items():
        retained={p for p in pins if p[0] in ['PIN','*'] or p[0] in bg['cells']}
        require(retained<=bg['power'][net],'Existing power assignment lost: '+net)
    count=cell_supplies(bg['cells'],bg['power'],lib)
    return dict(status='PDN_GEOMETRY_AND_CELL_SUPPLIES_VERIFIED',standard_cells_with_vdd_vss=count,all_special_geometry_unchanged=True)

def constraints(before,after):
    def lines(p):return [norm(l) for l in Path(p).read_text().splitlines() if l.strip() and not l.lstrip().startswith('#')]
    a,b=lines(before),lines(after)
    prop=[x for x in b if x.startswith('set_propagated_clock ')]
    require(len(prop)==3 and all(any('{'+n+'}' in x for x in prop) for n in ['clk_sys','clk_jtg','clk_rtc']),'All three clocks must be propagated')
    require(Counter(a)==Counter(x for x in b if not x.startswith('set_propagated_clock ')),'Serialized timing constraints changed')
    expected={'clk_sys':10.0,'clk_jtg':25.0,'clk_rtc':50.0}
    clocks={m[1]:float(m[2]) for line in a if (m:=re.match(r'create_clock -name (\S+) -period ([\d.]+)',line))}
    require(clocks==expected,'Original clock periods differ')
    loads=[x for x in a if x.startswith('set_load ')];require(loads and all(re.match(r'set_load -pin_load 15\.0+ \[get_ports ',x) for x in loads),'Original 15 pF loads differ')
    return dict(status='ORIGINAL_CONSTRAINTS_PRESERVED',clock_periods_ns=clocks,load_commands_15pf=len(loads),propagated_clocks=sorted(expected))

def timing(out):
    result={}
    for stage in ['prects','postcts','prerepair','final']:
        row={}
        for kind,pattern in [('worst-max',r'worst slack max\s+([-+\d.eE]+)'),('worst-min',r'worst slack min\s+([-+\d.eE]+)'),('tns-max',r'tns(?: max)?\s+([-+\d.eE]+)'),('tns-min',r'tns(?: min)?\s+([-+\d.eE]+)')]:
            text=(out/'reports'/f'{stage}-{kind}.rpt').read_text();m=re.search(pattern,text)
            require(m is not None,'Missing timing metric: '+stage+'/'+kind);x=float(m[1]);require(math.isfinite(x),'Nonfinite timing metric');row[kind.replace('-','_')+'_ns']=x
        for corner in ['tt','ff']:
            for kind in ['max','min']:
                s=(out/'reports'/f'{stage}-{corner}-{kind}.rpt').read_text()
                require('Startpoint:' in s and 'Endpoint:' in s and 'slack (' in s and 'Corner: '+corner in s,'Missing corner path evidence')
        result[stage]=row
    result['estimated_max_checks_nonnegative']=result['final']['worst_max_ns']>=0
    result['estimated_min_checks_nonnegative']=result['final']['worst_min_ns']>=0
    result['timing_closed']=False;result['scope']='Original TT/FF libraries with estimated parasitics; maximum checks include recovery and minimum checks include removal'
    return result

def routing(out):
    s=(out/'implementation.log').read_text()
    require(not re.search(r'\[ERROR\b|^Error:',s,re.M),'Implementation tool error')
    require('CP3C_CTS_GRT_COMPLETE' in s and 'CP3C_FINAL_PLACEMENT_OK' in s,'Implementation did not finish')
    powerlog=(out/'power.log').read_text()
    require('CP3C_POWER_COMPLETE' in powerlog and '[ERROR' not in powerlog,'Power verification failed')
    for net in ['VDD','VSS','VDDIO','VSSIO']:
        require('CP3C_POWER_GRID_OK '+net+'\n' in powerlog and 'All shapes on net '+net+' are connected.' in powerlog,'Supply connectivity failed: '+net)
    require(s.count('Final congestion report:')==2,'Expected initial and final congestion reports')
    report=s.rsplit('Final congestion report:',1)[1]
    rr=re.findall(r'^\s*(Metal[1-4]|TopMetal1|Total)\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+(\d+) /\s*(\d+) /\s*(\d+)\s*$',report,re.M)
    require([r[0] for r in rr]==['Metal1','Metal2','Metal3','Metal4','TopMetal1','Total'],'Unrecognized final congestion table')
    require(all(int(x)==0 for r in rr for x in r[4:]),'Final routing overflow is nonzero')
    nets=records((out/'global.def').read_text(),'NETS');eligible={n for n,v in nets.items() if len(re.findall(r'\( [^()]+ \)',v))>=2}
    guide=(out/'global.guide').read_text();pos=0;guided={};boxes=0
    for m in re.finditer(r'(\S+)\n\(\n([^)]*)\)\n',guide):
        require(not guide[pos:m.start()].strip(),'Malformed guide');pos=m.end();require(m[1] not in guided,'Duplicate guide net');count=0
        for line in m[2].splitlines():
            mm=re.fullmatch(r'(\d+) (\d+) (\d+) (\d+) (Metal[1-4]|TopMetal1)',line);require(mm is not None,'Malformed guide box')
            x1,y1,x2,y2=map(int,mm.groups()[:4]);require(x1<x2 and y1<y2,'Empty guide box');count+=1
        require(count,'Empty guide net');guided[m[1]]=count;boxes+=count
    require(not guide[pos:].strip(),'Unparsed guide data');require(set(guided)==eligible,'Missing/extra route guides')
    m=re.search(r'Routed nets: (\d+)',report);require(m is not None and int(m[1])==len(eligible),'Routed-net count mismatch')
    m=re.search(r'Total wirelength: (\d+) um',report);require(m is not None,'Missing global wirelength')
    return dict(status='POST_CTS_GLOBAL_ROUTE_PASS',guided_nets=len(guided),guide_rectangles=boxes,overflow=0,
        global_wirelength_um=int(m[1]),power_networks_connected=['VDD','VSS','VDDIO','VSSIO'],
        congestion=[dict(layer=r[0],resource=int(r[1]),demand=int(r[2]),usage_percent=float(r[3])) for r in rr])

def cts(out):
    s=(out/'cts.log').read_text();require('CP3C_CTS_COMPLETE' in s and 'CP3C_CTS_PLACEMENT_OK' in s,'CTS did not finish')
    require(not re.search(r'\[ERROR\b|^Error:',s,re.M),'CTS tool error')
    report=(out/'cts.rpt').read_text();res={}
    for label,key in [('Clock Roots','roots'),('Buffers Inserted','buffers_inserted'),('Clock Subnets','subnets'),('Sinks','sinks')]:
        m=re.search(r'Total number of '+label+r': (\d+)\.',report);require(m is not None,'Missing CTS metric');res[key]=int(m[1]);require(res[key]>0,'Empty clock tree')
    require(res['roots']==5,'Clock root count changed; review coverage')
    return res

def independent(out,route,times):
    s=(out/'independent-route.log').read_text()
    require('CP3C_INDEPENDENT_ROUTE_COMPLETE' in s and 'CP3C_INDEPENDENT_PARASITICS_COMPLETE' in s,'Independent verification incomplete')
    require(not re.search(r'\[ERROR\b|^Error:|\[WARNING EST-',s,re.M),'Independent routing/parasitics warning or error')
    require(sha(out/'independent.guide')==sha(out/'global.guide'),'Fresh-process routing guides differ')
    m=re.search(r'Total wirelength: (\d+) um',s);require(m is not None and int(m[1])==route['global_wirelength_um'],'Independent wirelength differs')
    for key,pattern in [('worst_max_ns',r'worst slack max\s+([-+\d.eE]+)'),('worst_min_ns',r'worst slack min\s+([-+\d.eE]+)'),('tns_max_ns',r'tns(?: max)?\s+([-+\d.eE]+)'),('tns_min_ns',r'tns(?: min)?\s+([-+\d.eE]+)')]:
        m=re.search(pattern,s);require(m is not None and math.isfinite(float(m[1])) and abs(float(m[1])-times['final'][key])<=0.000001,'Independent timing metric differs: '+key)
    return dict(status='FRESH_PROCESS_ROUTE_AND_TIMING_VERIFIED',identical_guides=True,identical_timing_metrics_to_1e_6_ns=True,parasitics_warnings=0)

def warnings(out):
    allrows=[]
    for n in ['cts.log','implementation.log','power.log','independent-route.log']:
        allrows += [dict(log=n,code=c,message=m) for c,m in re.findall(r'^\[WARNING ([^]]+)\] (.*)$',(out/n).read_text(),re.M)]
    require(not any(x['code'].startswith('STA-') and any(w in x['message'].lower() for w in ['not found','unknown','unrecognized','ignored']) for x in allrows),'STA object/constraint warning requires review')
    return dict(count=len(allrows),by_code=dict(Counter(x['code'] for x in allrows)),entries=allrows,review_required=bool(allrows))
