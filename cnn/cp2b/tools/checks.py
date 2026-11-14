import json,re,math
from pathlib import Path
from common import require,sha,manifest
MACRO='RM_IHPSG13_1P_512x32_c2_bm_bist'

def prerequisite(cnn,evidence,pins):
    require(sha(cnn/'SHA256SUMS')==pins['cp1_package_manifest'],'CP1 package differs')
    manifest(cnn)
    require(sha(cnn/'cp2a/SHA256SUMS')==pins['cp2a_package_manifest'],'CP2A 1.2 package differs')
    manifest(cnn/'cp2a')
    require(sha(evidence/'SHA256SUMS')==pins['cp2a_evidence_manifest'],'Use accepted CP2A evidence cp2a-20261023-152105-p12sdbxc')
    manifest(evidence)
    c=json.loads((evidence/'conclusion.json').read_text())
    require(c['status']=='CP2A_PASS' and c['version']=='1.2-openroad-lef-init','CP2A prerequisite failed')
    require(json.loads((evidence/'preservation.json').read_text())['unchanged'],'CP2A preservation failed')
    for p in (cnn/'cp2a/rtl').glob('*.sv'):
        require(sha(p)==sha(evidence/'work'/p.name),'Accepted engine RTL differs: '+p.name)
    return dict(status='ACCEPTED_CP2A_VERIFIED',evidence=str(evidence),manifest_sha256=pins['cp2a_evidence_manifest'])

def top(path):
    s=Path(path).read_text();d=json.loads(s[s.index('{'):]);m=d['modules']
    t=m.get('\\cnn_peripheral') or m.get('cnn_peripheral')
    require(t is not None,'Missing peripheral synthesis top');return t

def synthesis(before,after,netlist_json,lef):
    b,a=top(before),top(after);k=dict(b['num_cells_by_type']);removed=k.pop('$scopeinfo',0)
    require(a['num_cells_by_type']==k and a['num_cells']==b['num_cells']-removed and a['area']==b['area'],'Metadata cleanup changed circuit')
    require(k.get(MACRO)==2,'Expected exactly two 512x32 SRAM macros')
    require(not [x for x in k if x!=MACRO and not x.startswith('sg13g2_')],'Unmapped cells remain')
    require(a['num_memories']==0 and a['num_processes']==0,'Inferred memory/process remains')
    require(math.isfinite(a['area']) and a['area']>0,'Invalid standard-cell area')
    data=json.loads(Path(netlist_json).read_text())['modules']['cnn_peripheral']['cells']
    macros=[v for v in data.values() if v['type']==MACRO]
    require(len(macros)==2,'Mapped netlist macro count differs')
    for m in macros:
        c=m['connections']
        for p in ['A_BIST_CLK','A_BIST_EN','A_BIST_MEN','A_BIST_WEN','A_BIST_REN','A_BIST_ADDR','A_BIST_DIN','A_BIST_BM','A_DLY']:
            require(set(c[p])=={'0'},'Unsafe SRAM test-mode tie: '+p)
        require(len(c['A_ADDR'])==9 and len(c['A_BM'])==32 and len(c['A_DOUT'])==32,'Macro port shape differs')
    match=re.search(r'^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;',Path(lef).read_text(),re.M)
    require(match is not None,'Missing SRAM LEF dimensions');w,h=map(float,match.groups())
    return dict(status='MAPPED_WITH_TWO_SRAMS',standard_cells=a['num_cells']-2,logic_area_um2=a['area'],
                sram_macros=2,sram_bytes=4096,sram_area_um2=2*w*h,total_cell_macro_area_um2=a['area']+2*w*h,
                scope_metadata_removed=removed,macro_test_inputs_tied_low=True,cell_types=k,
                whole_chip_fit_verified=False,placement_routing_and_halos_included=False)

def timing(path,mapped_json=None):
    s=Path(path).read_text();require('CP2B_STA_COMPLETE' in s,'Timing incomplete')
    require(not re.search(r'^Error|\[ERROR|invalid command',s,re.M),'Timing tool error')
    checks=s.split('CP2B_TIMING_CHECKS',1)[1].split('CP2B_SETUP_PATHS',1)[0]
    constant_endpoints=[]
    if re.search(r'Warning:|\[WARNING',checks):
        # Older OpenSTA reports tied-off SRAM BIST endpoints. Accept only the
        # exact reported pins after proving their mapped connections are zero.
        lines=checks.strip().splitlines()
        m=re.fullmatch(r'Warning: There are (\d+) unconstrained endpoints\.',lines[0])
        require(m is not None and mapped_json is not None,'Timing constraint warnings need review')
        constant_endpoints=[x.strip() for x in lines[1:]]
        require(len(constant_endpoints)==int(m.group(1)) and len(set(constant_endpoints))==len(constant_endpoints),'Unconstrained endpoint count differs')
        cells=json.loads(Path(mapped_json).read_text())['modules']['cnn_peripheral']['cells'];allowed=set()
        for name,c in cells.items():
            if c['type']!='RM_IHPSG13_1P_512x32_c2_bm_bist':continue
            for port,bits in c['connections'].items():
                if not port.startswith('A_BIST_') or set(bits)!={'0'}:continue
                if len(bits)==1:allowed.add(name+'/'+port)
                else:allowed.update(name+'/'+port+'['+str(i)+']' for i in range(len(bits)))
        require(constant_endpoints and set(constant_endpoints)<=allowed,'Nonconstant/unexpected unconstrained endpoints')
    m=re.search(r'CP2B_WORST_MAX_BEGIN\s+worst slack(?: max)?\s+(-?[\d.]+)\s+CP2B_WORST_MAX_END',s)
    require(m is not None,'Worst setup slack missing');slack=float(m.group(1))
    require(math.isfinite(slack),'Invalid timing result')
    hold=s.split('CP2B_HOLD_ESTIMATE_ONLY')[1].split('CP2B_MAX_SLEW_CAP_FANOUT')[0]
    h=[float(x) for x in re.findall(r'(-?[\d.]+)\s+slack \((?:VIOLATED|MET)\)',hold)]
    e=s.split('CP2B_MAX_SLEW_CAP_FANOUT')[1].split('CP2B_WORST_MAX_BEGIN')[0];counts={}
    for kind in ['max slew','max capacitance','max fanout']:
        section=e.split(kind,1)[1] if kind in e else ''
        for other in ['max slew','max capacitance','max fanout']:
            if other!=kind:section=section.split(other,1)[0]
        counts[kind]=section.count('(VIOLATED)')
    return dict(worst_setup_slack_ns=slack,setup_pass=slack>=0,clock_period_ns=10.0,
                corner='TT 1.20 V 25 C',sram_timing_included=True,wire_parasitics=False,
                worst_reported_hold_slack_ns=min(h) if h else None,electrical_violating_pin_counts=counts,
                verified_constant_bist_endpoints=len(constant_endpoints),
                electrical_closed=False,whole_chip_fit_verified=False)
