"""Narrow, fail-closed Liberty reader and independent bilinear table oracle.

This is deliberately not a general Liberty implementation. It accepts the
specific two-dimensional NLDM tables in the pinned IHP GPIO model.
"""
import bisect
import math
import re
from pathlib import Path
from base_support import require

CELL='sg13cmos5l_IOPadInOut30mA'
KINDS={'combinational':'data', 'three_state_enable':'enable', 'three_state_disable':'disable'}
TABLES=('cell_rise','cell_fall','rise_transition','fall_transition')
NUMBER=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'

def groups(text, kind):
    # Quotes are consumed before comments so comment-like strings survive.
    clean=re.sub(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*',
                 lambda m: m[0] if m[0].startswith('"') else ' '*len(m[0]), text)
    masked=re.sub(r'"(?:\\.|[^"\\])*"',lambda m:' '*len(m[0]),clean)
    result=[]
    for m in re.finditer(r'\b'+re.escape(kind)+r'\s*\(([^)]*)\)\s*\{',clean):
        # A match must start outside a string/comment.
        if not masked[m.start():m.start()+len(kind)].strip(): continue
        depth=1; i=m.end()
        while depth and i<len(clean):
            depth += (masked[i]=='{')-(masked[i]=='}'); i+=1
        require(depth==0, 'Unterminated Liberty group '+kind)
        result.append((m[1].strip().strip('"'),clean[m.end():i-1]))
    return result

def one_group(text,kind,name=None):
    candidates=[body for key,body in groups(text,kind) if name is None or key==name]
    require(len(candidates)==1,f'Expected exactly one {kind}({name}), got {len(candidates)}')
    return candidates[0]

def attr(text,name):
    m=re.findall(r'\b'+re.escape(name)+r'\s*:\s*([^;]+);',text)
    require(len(m)==1,'Missing or duplicate Liberty attribute '+name)
    return m[0].strip().strip('"')

def numeric_list(text,name):
    m=re.findall(r'\b'+re.escape(name)+r'\s*\((.*?)\)\s*;',text,re.S)
    require(len(m)==1,'Missing or duplicate table field '+name)
    residual=re.sub(NUMBER,'',m[0])
    require(not residual.translate(str.maketrans('','',',"\\ \t\r\n')), 'Non-numeric table field')
    values=[float(x) for x in re.findall(NUMBER,m[0])]
    require(values and all(math.isfinite(x) for x in values),'Non-finite or empty table')
    return values

def read_table(text,kind,templates):
    candidates=groups(text,kind); require(len(candidates)==1,'Missing or duplicate '+kind)
    template,body=candidates[0]
    require(template in templates,'Unknown table template')
    x=numeric_list(body,'index_1'); y=numeric_list(body,'index_2'); values=numeric_list(body,'values')
    require(len(x)>=2 and len(y)>=2 and len(values)==len(x)*len(y),'Table shape differs')
    require(all(b>a for a,b in zip(x,x[1:])) and all(b>a for a,b in zip(y,y[1:])), 'Axes not strictly increasing')
    require(all(v>0 for v in x+y+values),'Expected positive characterized values')
    return dict(x=x,y=y,values=[values[i*len(y):(i+1)*len(y)] for i in range(len(x))])

def read_model(path):
    text=Path(path).read_text(); lib=one_group(text,'library')
    require(attr(lib,'time_unit')=='1ns','Time unit differs')
    require(bool(re.search(r'capacitive_load_unit\s*\(\s*1\s*,\s*pf\s*\)',lib)), 'Capacitance unit differs')
    templates={}
    for name,body in groups(lib,'lu_table_template'):
        require(attr(body,'variable_1')=='input_net_transition' and
                attr(body,'variable_2')=='total_output_net_capacitance','Table axis meaning differs')
        templates[name]=body
    cell=one_group(lib,'cell',CELL); pad=one_group(cell,'pin','pad')
    require(attr(pad,'function')=='c2p' and attr(pad,'three_state')=="c2p_en'",'Pad polarity differs')
    arcs={}
    for _,body in groups(pad,'timing'):
        typ=attr(body,'timing_type'); require(typ in KINDS,'Unknown pad arc type')
        kind=KINDS[typ]; require(kind not in arcs,'Duplicate arc type')
        require(attr(body,'related_pin')==('c2p' if kind=='data' else 'c2p_en'),'Related pin differs')
        require(attr(body,'timing_sense')==('negative_unate' if kind=='disable' else 'positive_unate'),'Arc sense differs')
        arcs[kind]={name:read_table(body,name,templates) for name in TABLES}
    require(set(arcs)=={'data','enable','disable'},'Missing pad arc')
    thresholds={name:float(attr(lib,name)) for name in
        ('slew_lower_threshold_pct_rise','slew_upper_threshold_pct_rise',
         'slew_lower_threshold_pct_fall','slew_upper_threshold_pct_fall','slew_derate_from_library')}
    require(list(thresholds.values())==[10,90,10,90,1],'Unexpected I/O slew threshold convention')
    # No explicit pad max_transition is present in these pinned files.
    require(not re.search(r'\bmax_transition\s*:',pad),'Explicit pad limit requires review')
    return dict(cell=CELL,units={'time':'ns','capacitance':'pF'},thresholds=thresholds,
        pad_max_transition_ns=float(attr(lib,'default_max_transition')),
        pad_max_capacitance_pf=float(attr(pad,'max_capacitance')),
        input_max_transition_ns={p:float(attr(one_group(cell,'pin',p),'max_transition')) for p in ('c2p','c2p_en')},
        arcs=arcs)

def interpolate(table,x,y):
    require(math.isfinite(x) and math.isfinite(y),'Non-finite interpolation point')
    ax,ay=table['x'],table['y']; v=table['values']
    i=min(max(bisect.bisect_right(ax,x)-1,0),len(ax)-2)
    j=min(max(bisect.bisect_right(ay,y)-1,0),len(ay)-2)
    u=(x-ax[i])/(ax[i+1]-ax[i]); w=(y-ay[j])/(ay[j+1]-ay[j])
    value=(1-u)*((1-w)*v[i][j]+w*v[i][j+1])+u*((1-w)*v[i+1][j]+w*v[i+1][j+1])
    return value, ax[0]-1e-7<=x<=ax[-1]+1e-7 and ay[0]-1e-7<=y<=ay[-1]+1e-7

def bounds(model):
    result=[]
    for kind,tables in model['arcs'].items():
        for direction in ('rise','fall'):
            t=tables[direction+'_transition']; v=[n for row in t['values'] for n in row]
            result.append(dict(arc=kind,direction=direction,min_slew_ns=min(v),max_slew_ns=max(v),
                slew_limit_ns=model['pad_max_transition_ns'],min_above_limit=min(v)>model['pad_max_transition_ns'],
                slew_equals_delay_table=t==tables['cell_'+direction],
                input_slew_min_ns=t['x'][0],input_slew_max_ns=t['x'][-1],
                total_cap_min_pf=t['y'][0],total_cap_max_pf=t['y'][-1]))
    return result

def dcalc_rows(text):
    require('Cell: '+CELL in text,'Wrong cell in dcalc report')
    matches=list(re.finditer(r'^(c2p|c2p_en) ([\^v]) -> pad (\^|v|Z1|Z0|0Z|1Z)\s*$',text,re.M))
    require(matches,'No recognized pad arc calculations')
    result=[]
    for i,m in enumerate(matches):
        body=text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)]
        token=m[3]; kind='data' if token in ('^','v') else 'enable' if token.startswith('Z') else 'disable'
        direction='rise' if token in ('^','Z1','0Z') else 'fall'
        require(m[1]==('c2p' if kind=='data' else 'c2p_en'),'Unexpected related input')
        require(m[2]==('v' if kind=='disable' else '^' if kind=='enable' or direction=='rise' else 'v'),'Input polarity differs')
        def floats(pattern,count):
            vals=[float(x) for x in re.findall(pattern,body,re.M)]
            require(len(vals)==count and all(math.isfinite(x) for x in vals),'Incomplete dcalc field '+pattern)
            return vals
        slew=floats(r'input_net_transition = ('+NUMBER+r')',2)
        cap=floats(r'total_output_net_capacitance = ('+NUMBER+r')',2)
        factors=floats(r'^PVT scale factor = ('+NUMBER+r')',2)
        require(slew[0]==slew[1] and cap[0]==cap[1] and factors==[1,1],'Unexpected dcalc scaling or axes')
        result.append(dict(arc=kind,direction=direction,edge=token,input_slew_ns=slew[0],total_cap_pf=cap[0],
                           delay_ns=floats(r'^Delay = ('+NUMBER+r')',1)[0],slew_ns=floats(r'^Slew = ('+NUMBER+r')',1)[0]))
    require(len({(r['arc'],r['direction']) for r in result})==len(result),'Duplicate report arc')
    return result

def compare_report(model,text,expected_arcs):
    data=dcalc_rows(text)
    require({r['arc'] for r in data}==set(expected_arcs) and len(data)==2*len(expected_arcs),'Incomplete report arc coverage')
    for r in data:
        tables=model['arcs'][r['arc']]
        domains=[]
        for metric,key in [('delay','cell_'+r['direction']),('slew',r['direction']+'_transition')]:
            oracle,inside=interpolate(tables[key],r['input_slew_ns'],r['total_cap_pf'])
            error=abs(oracle-r[metric+'_ns']); domains.append(inside)
            # 20 fs absolute + 5 ppm relative accommodates float32 STA tables
            # and nine-decimal ns report serialization without hiding 1 ps errors.
            tolerance=2e-5+abs(oracle)*5e-6
            require(error<=tolerance,f'Liberty oracle differs for {r["arc"]}/{r["edge"]}/{metric}: {oracle} vs {r[metric+"_ns"]}')
            r[metric+'_oracle_ns']=oracle; r[metric+'_error_ns']=error
        r['within_characterized_domain']=all(domains)
    return data

def erc_rows(text):
    result=[]; kind=None
    for line in text.splitlines():
        if line.strip() in ('max slew','max capacitance','max fanout'): kind=line.strip()
        if not re.search(r'\((MET|VIOLATED)\)',line): continue
        p=line.split(); require(kind and len(p)==5,'Malformed ERC row')
        vals=list(map(float,p[1:4])); require(all(math.isfinite(v) for v in vals),'Non-finite ERC')
        result.append(dict(check=kind,pin=p[0],limit=vals[0],actual=vals[1],slack=vals[2],violated=p[4]=='(VIOLATED)'))
    return result
