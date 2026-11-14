"""Verify the completed native signal replay before consuming its lineage."""
from cp0c_io_power_support import *

def validate_replay(ctx):
    def read(name):return json.loads((ctx/name).read_text())
    require((ctx/'exit-status.txt').read_text().strip()=='0','Signal replay did not pass')
    require(read('reports/preservation.json')=={'unchanged':True,'changed':[]},'Signal replay preservation failed')
    final=read('reports/conclusion.json')
    require(final['status']=='IO_SIGNAL_DIAGNOSTIC_MATCH_VERIFIED' and final['failures']=={} and
            final['physical_jumper_controls_pass'] and not final['full_chip_lvs'] and
            not final['candidate_promoted'] and final['cp0c']=='PENDING','Signal replay conclusion differs')
    require(set(final['targets'])=={'output','gpio'},'Signal prerequisite target coverage differs')
    for key,counts,negatives in [('output',(24,7,15),27),('gpio',(41,9,24),35)]:
        t=final['targets'][key];expected={k:k=='translated' for k in t['outcomes']}
        require(t['status']=='IO_SIGNAL_PAD_DIAGNOSTIC_MATCH_VERIFIED' and t['negative_controls_pass']
                and t['source_graphs_unchanged'] and t['outcomes']==expected and
                t['outcomes'].get('translated') is True and len(expected)==negatives+1 and
                t['fault_controls']+t['context_controls']==negatives,'Signal control prerequisite differs')
        for case in expected:
            r=read('comparisons/'+key+'/'+case+'.json')
            require(r['raw_match']==r['strict_match']==expected[case],'Signal individual comparison differs')
        p=read('comparisons/'+key+'/translated.json')
        require(p['circuits']==['Match'] and p['pair_counts']==dict(zip(('devices','pins','nets'),
            ({'Match':n} for n in counts))),'Signal positive pair coverage differs')
    previous=read('provenance.json')
    require(previous['version']=='1.2-raw-fingers' and previous['klayout']=='0.30.9' and
            previous['original_io_load_pf']==15 and not previous['source_geometry_changed'] and
            not previous['new_extraction'],'Signal replay provenance differs')
    return previous

def check_files(directory,files,checked):
    for name,digest in files.items():
        rel=Path(name);require(not rel.is_absolute() and '..' not in rel.parts,'Unsafe pinned path')
        checked(directory/rel,digest)

def validate_signal(ctx,pin,checked,results):
    check_files(ctx,pin['signal_replay_files'],checked)
    previous=validate_replay(ctx)
    source=Path(previous['source_run']).resolve()
    require(source.parent==results and source.name==pin['signal_source_run'],'Signal source lineage differs')
    check_files(source,pin['signal_source_files'],checked)
    cp=json.loads((source/'provenance.json').read_text())
    require(cp['version']=='1.1-input-pad-prerequisite' and cp['klayout']=='0.30.9' and
            cp['original_io_load_pf']==15 and not cp['source_geometry_changed'],'Signal extraction provenance differs')
    require(json.loads((source/'reports/preservation.json').read_text())=={'unchanged':True,'changed':[]},
            'Signal extraction preservation failed')
    return cp
