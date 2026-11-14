import json,re
from pathlib import Path
from common import require,sha,manifest

def prerequisite(cnn,evidence,pins):
    require(sha(cnn/'SHA256SUMS')==pins['cp1_package_manifest'],'CP1 package identity differs')
    manifest(cnn)
    require(sha(evidence/'SHA256SUMS')==pins['cp1_evidence_manifest'],'CP1 evidence identity differs')
    manifest(evidence)
    c=json.loads((evidence/'conclusion.json').read_text())
    require(c['status']=='CP1_PASS' and c['vectors']==48,'CP1 is not complete')
    require(json.loads((evidence/'preservation.json').read_text())['unchanged'],'CP1 preservation failed')
    return c

def synthesis_top(path):
    raw=Path(path).read_text();data=json.loads(raw[raw.index('{'):])
    modules=data['modules'];top=modules.get('\\cnn_engine') or modules.get('cnn_engine')
    require(top is not None,'Synthesis top missing')
    return top

def metadata_cleanup(before_path,after_path):
    before=synthesis_top(before_path);after=synthesis_top(after_path)
    expected=dict(before['num_cells_by_type']);removed=expected.pop('$scopeinfo',0)
    require(type(removed) is int and removed>=0,'Invalid scope metadata count')
    require(after['num_cells_by_type']==expected,'Cleanup changed cells other than $scopeinfo')
    require(after['num_cells']==before['num_cells']-removed,'Cleanup cell count differs')
    require(after['area']==before['area'],'Cleanup changed standard-cell area')
    require(after['num_memories']==before['num_memories'],'Cleanup changed memories')
    return dict(removed_type='$scopeinfo',removed_count=removed,logic_cell_counts_unchanged=True,
                standard_cell_area_unchanged=True)

def synthesis_result(path):
    top=synthesis_top(path)
    kinds=top['num_cells_by_type']
    unexpected=sorted(k for k in kinds if not k.startswith('sg13g2_'))
    require(kinds and not unexpected,'Unmapped cells remain: '+', '.join(unexpected))
    require(top['num_memories']==0,'Memory unexpectedly synthesized inside engine')
    require(top['num_cells']>0 and top['area']>0,'No synthesized engine area')
    return dict(engine_cell_area_um2=top['area'],engine_cell_count=top['num_cells'],cell_types=kinds,
                external_sram_excluded=True)

def timing_result(path):
    text=Path(path).read_text()
    require('CP2A_STA_COMPLETE' in text,'STA did not complete')
    require(not re.search(r'(^Error:|\[ERROR|^Error |invalid command)',text,re.M),'STA reported an error')
    match=re.search(r'CP2A_WORST_MAX_BEGIN\s+worst slack(?: max)?\s+(-?[0-9.]+)\s+CP2A_WORST_MAX_END',text)
    require(match is not None,'Worst setup slack missing')
    slack=float(match.group(1))
    setup=text.split('CP2A_TIMING_CHECKS',1)[1].split('CP2A_SETUP_PATHS',1)[0]
    require(not re.search(r'Warning:|\[WARNING',setup),'STA constraint checks need review')
    electrical=text.split('CP2A_MAX_SLEW_CAP_FANOUT',1)[-1].split('CP2A_WORST_MAX_BEGIN',1)[0]
    hold=text.split('CP2A_HOLD_ESTIMATE_ONLY',1)[-1].split('CP2A_MAX_SLEW_CAP_FANOUT',1)[0]
    hold_slacks=[float(s) for s in re.findall(r'(-?[0-9.]+)\s+slack \((?:VIOLATED|MET)\)',hold)]
    electrical_counts={}
    for kind,following in [('max slew','max capacitance'),('max capacitance','max fanout'),('max fanout',None)]:
        if kind not in electrical:electrical_counts[kind]=0;continue
        section=electrical.split(kind,1)[1]
        if following:section=section.split(following,1)[0]
        # Some empty report sections are omitted by OpenSTA.
        if kind=='max slew':section=section.split('max fanout',1)[0]
        electrical_counts[kind]=section.count('(VIOLATED)')
    return dict(clock_period_ns=10.0,clock_uncertainty_ns=0.1,worst_setup_slack_ns=slack,
                setup_pass=slack>=0,corner='TT 1.20 V 25 C',wire_parasitics=False,
                interface_delays_provisional=True,hold_closed=False,physical_fit_verified=False,
                worst_reported_hold_slack_ns=min(hold_slacks) if hold_slacks else None,
                electrical_violating_pin_counts=electrical_counts,electrical_closed=False)

def macro_area(path):
    text=Path(path).read_text();match=re.search(r'^\s*SIZE\s+([0-9.]+)\s+BY\s+([0-9.]+)\s*;',text,re.M)
    require(match is not None,'SRAM LEF dimensions missing')
    w,h=map(float,match.groups())
    return dict(candidate='2 x RM_IHPSG13_1P_512x32_c2_bm_bist',bytes=4096,
                one_macro_width_um=w,one_macro_height_um=h,two_macro_area_um2=round(2*w*h,6),
                macro_mapping_implemented=False,halos_routing_and_adapter_excluded=True)
