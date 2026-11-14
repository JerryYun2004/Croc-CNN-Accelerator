"""Require the exact completed native power-pad evidence and strict outcomes."""
from cp0c_io_fill_support import *

def validate_power(ctx,pin,checked):
    for name,digest in pin['power_files'].items():
        rel=Path(name);require(not rel.is_absolute() and '..' not in rel.parts,'Unsafe prerequisite path')
        checked(ctx/rel,digest)
    def read(name):return json.loads((ctx/name).read_text())
    require((ctx/'exit-status.txt').read_text().strip()=='0','Power-pad stage did not pass')
    require(read('reports/preservation.json')=={'unchanged':True,'changed':[]},'Power-pad preservation failed')
    final=read('reports/conclusion.json')
    require(final['status']=='IO_POWER_DIAGNOSTIC_MATCH_VERIFIED' and final['failures']=={} and
            final['physical_rail_controls_pass'] and not final['full_chip_lvs'] and
            not final['candidate_promoted'] and final['cp0c']=='PENDING','Power-pad conclusion differs')
    expected={'vdd':(33,4,31,74),'iovdd':(33,4,31,74),'vss':(4,4,4,23),'iovss':(4,4,4,22)}
    require(set(final['targets'])==set(expected),'Power-pad target set differs')
    for key,(devices,pins,nets,negatives) in expected.items():
        r=final['targets'][key];outcomes=r['outcomes']
        require(r['status']=='IO_POWER_PAD_DIAGNOSTIC_MATCH_VERIFIED' and
                outcomes.get('translated') is True and len(outcomes)==negatives+1 and
                all(v is (k=='translated') for k,v in outcomes.items()) and
                r['negative_controls_pass'] and r['source_graphs_unchanged'] and
                r['fault_controls']+r['context_controls']==negatives,'Power-pad control coverage differs')
        for name,value in outcomes.items():
            c=read(f'comparisons/{key}/{name}.json')
            require(c['raw_match']==c['strict_match']==value,'Individual prerequisite comparison differs')
        c=read(f'comparisons/{key}/translated.json')
        require(c['circuits']==['Match'] and c['pair_counts']=={
            'devices':{'Match':devices},'pins':{'Match':pins},'nets':{'Match':nets}},'Power-pad pair counts differ')
    previous=read('provenance.json')
    require(previous['version']=='1.0-four-power-pads' and previous['klayout']=='0.30.9' and
            previous['original_io_load_pf']==15 and not previous['source_geometry_changed'] and
            previous['new_extraction'] and not previous['full_chip_lvs'],'Power-pad provenance differs')
    return previous
