"""Parse every reported electrical violation, including blank fanout slack."""
import re
from collections import Counter
from pathlib import Path
from common import require
from power import records

def unescape(s):return re.sub(r'\\(.)',r'\1',s)

def inventory(defpath,reports,prefix='final'):
    cells={unescape(n):b.split()[0] for n,b in records(Path(defpath).read_text(),'COMPONENTS').items()}
    ports={unescape(n) for n in records(Path(defpath).read_text(),'PINS')}
    result={}
    for corner in ['tt','ff']:
        text=(Path(reports)/((prefix+'-' if prefix else '')+f'{corner}-electrical.rpt')).read_text()
        group=None;rows=[]
        for line in text.splitlines():
            if line in ['max slew','max fanout','max capacitance','min pulse width']:group=line
            if '(VIOLATED)' not in line:continue
            require(group in ['max slew','max fanout','max capacitance'],'Unrecognized electrical violation type')
            m=re.fullmatch(r'\s*(\S+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)(?:\s+([-+\d.eE]+))?\s+\(VIOLATED\)\s*',line)
            require(m is not None,'Unparsed electrical violation: '+line)
            pin=unescape(m[1]);limit=float(m[2]);actual=float(m[3]);slack=limit-actual
            require(limit>0 and actual>limit,'Invalid electrical limit or violation')
            if m[4] is not None:require(abs(float(m[4])-slack)<=0.000002,'Electrical slack inconsistent')
            if pin in ports:domain='port'
            else:
                inst=pin.rsplit('/',1)[0];require(inst in cells,'Unknown electrical instance: '+inst)
                master=cells[inst]
                domain='sram' if master.startswith('RM_IHPSG13') else 'standard_cell' if master.startswith('sg13g2_') else 'io'
            rows.append(dict(type=group,pin=pin,domain=domain,limit=limit,actual=actual,slack=slack))
        require(len(rows)==text.count('(VIOLATED)'),'Electrical report rows were skipped')
        summary={}
        for kind in ['max slew','max fanout','max capacitance']:
            selected=[r for r in rows if r['type']==kind];internal=[r for r in selected if r['domain'] in ['standard_cell','sram']]
            summary[kind]=dict(total=len(selected),internal=len(internal),by_domain=dict(Counter(r['domain'] for r in selected)),worst_slack=min((r['slack'] for r in selected),default=0),internal_worst_slack=min((r['slack'] for r in internal),default=0))
        result[corner]=dict(summary=summary,violations=rows)
    return result

def progress(before_times,after_times,before_electrical,after_electrical):
    require(after_times['worst_max_ns']>=before_times['worst_max_ns']+0.05 or after_times['worst_max_ns']>=0,'Setup worst slack did not improve by 0.05 ns or reach zero')
    require(after_times['tns_max_ns']>=before_times['tns_max_ns']-0.001,'Setup total negative slack regressed')
    require(after_times['worst_min_ns']>=0.1 and after_times['tns_min_ns']>=-1e-6,'Final 0.1 ns minimum timing margin not met')
    changes={}
    for corner in ['tt','ff']:
        changes[corner]={}
        for kind in ['max slew','max fanout','max capacitance']:
            a=before_electrical[corner]['summary'][kind];b=after_electrical[corner]['summary'][kind]
            require(b['internal']==0,'Internal electrical violations remain: '+corner+'/'+kind)
            require(b['internal_worst_slack']>=-1e-9,'Internal electrical slack inconsistent')
            changes[corner][kind]=dict(before=a['internal'],after=b['internal'])
    return dict(status='CP3E_TARGETED_REFINEMENT_VERIFIED',worst_max_improvement_ns=after_times['worst_max_ns']-before_times['worst_max_ns'],max_tns_improvement_ns=after_times['tns_max_ns']-before_times['tns_max_ns'],estimated_setup_nonnegative=after_times['worst_max_ns']>=0,hold_margin_0_1_ns_met=True,internal_electrical_counts=changes,timing_closed=False,electrical_closed=False)
