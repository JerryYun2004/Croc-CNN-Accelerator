import re,json
from pathlib import Path
from collections import Counter
CELL='cp3d_fanout_buf_22022203'
def need(condition,message):
    if not condition:raise RuntimeError(message)
def norm(s):return ' '.join(s.split())
def section(t,n):
    m=re.search(r'^'+n+r' \d+ ;\s*\n(.*?)^END '+n+r'\s*$',t,re.M|re.S)
    return m.group(1) if m else ''
def records(t,n):
    rows=re.findall(r'^\s*- (\S+)\s+(.*?);',section(t,n),re.M|re.S)
    need(len(rows)==len(dict(rows)),n+' duplicate record');return dict(rows)
def head_route(s):
    m=re.search(r'\+\s+(?:ROUTED|FIXED|COVER|SHIELD)\b',s)
    return (s[:m.start()],s[m.start():]) if m else (s,'')
def header(s):
    h,_=head_route(s);terms=sorted(re.findall(r'\(\s*(.*?)\s*\)',h))
    attrs=re.sub(r'\([^)]*\)','',h)
    return terms,sorted(norm(a) for a in attrs.split('+') if a.strip())
def make_input(out):
    p=out/'input.def';text=p.read_text();components=records(text,'COMPONENTS')
    old=components[CELL];need(old.split()[0]=='sg13g2_buf_4','Wrong buffer master')
    before='+ PLACED ( 1367520 1050840 ) N';after='+ PLACED ( 1366080 1050840 ) N'
    need(before in old,'Unexpected original placement/orientation')
    new=old.replace(before,after,1)
    pattern=r'(^\s*- '+re.escape(CELL)+r'\s+)'+re.escape(old)+r';'
    changed,count=re.subn(pattern,lambda m:m[1]+new+';',text,count=1,flags=re.M)
    need(count==1,'Single placement patch failed');p.write_text(changed)
    (out/'placement-patch.json').write_text(json.dumps({'cell':CELL,'before':old,'after':new,'net938_and_output_routes':'normal routing allowed; terminal membership unchanged','other_cell_changes_allowed':False},indent=2)+'\n')
def validate(out):
    a=(out/'input.def').read_text();b=(out/'routed.def').read_text()
    for sec in ('COMPONENTS','PINS','ROWS','REGIONS','GROUPS','BLOCKAGES','NONDEFAULTRULES'):
        x,y=records(a,sec),records(b,sec)
        need(x.keys()==y.keys(),sec+' membership changed')
        for key in x:need(norm(x[key])==norm(y[key]),sec+' unauthorized change: '+key)
    x,y=records(a,'VIAS'),records(b,'VIAS')
    for key in x:need(key in y and norm(x[key])==norm(y[key]),'Original via changed: '+key)
    added={key:y[key] for key in y.keys()-x.keys()}
    x,y=records(a,'SPECIALNETS'),records(b,'SPECIALNETS');need(x.keys()==y.keys(),'Power net membership changed')
    for key in x:
        need(header(x[key])==header(y[key]),'Power connectivity/attributes changed: '+key)
        def routes(body):return Counter(norm(r) for r in re.split(r'(?:\+\s+ROUTED|\bNEW)\s+',head_route(body)[1]) if r.strip())
        need(routes(x[key])==routes(y[key]),'Power-grid geometry changed: '+key)
    x,y=records(a,'NETS'),records(b,'NETS');need(x.keys()==y.keys(),'Logical net membership changed')
    changed=[]
    with (out/'routing-differences.jsonl').open('w') as f:
        for key in sorted(x):
            need(header(x[key])==header(y[key]),'Logical connectivity/USE/NDR assignment changed: '+key)
            old,new=head_route(x[key])[1],head_route(y[key])[1]
            need(not re.search(r'\+\s+(FIXED|COVER|SHIELD)\b',new),'Unauthorized wire classification: '+key)
            if norm(old)!=norm(new):
                changed.append(key);f.write(json.dumps({'net':key,'before':old,'after':new})+'\n')
    # All unhandled design attributes remain exact modulo whitespace/comments.
    def remaining(t):
        for sec in ('COMPONENTS','PINS','REGIONS','GROUPS','BLOCKAGES','NONDEFAULTRULES','VIAS','SPECIALNETS','NETS'):
            t=re.sub(r'^'+sec+r' \d+ ;.*?^END '+sec+r'\s*$', '',t,flags=re.M|re.S)
        t=re.sub(r'^GCELLGRID[^;]*;','',t,flags=re.M)
        t=re.sub(r'^#.*$','',t,flags=re.M)
        return norm(t)
    need(remaining(a)==remaining(b),'Unauthorized header/row/track/blockage/design attribute changed')
    need((out/'before-connections.tcllist').read_bytes()==(out/'after-connections.tcllist').read_bytes(),'DB terminal membership, supply assignment, USE or NDR topology changed')
    need((out/'imported.sdc').read_bytes()==(out/'propagated.sdc').read_bytes(),'Serialized constraints changed')
    report={'status':'ALLOWLIST_PRESERVATION_PASS','cells_changed_vs_patched_input':0,'authorized_placement_patch':'placement-patch.json','logical_and_clock_topology':'unchanged net/terminal membership, cell masters and NDR assignments','power_grid':'unchanged','constraints':'unchanged','routing_changed_nets':changed,'new_via_definitions':added,'signal_connectivity':'native detailed-router connectivity checker required during each completed iteration; DRT-0206 is fatal','supply_connectivity':'separate native power stage required','acceptance':False}
    (out/'preservation.json').write_text(json.dumps(report,indent=2)+'\n')

def record_differences(out):
    a=(out/'input.def').read_text();b=(out/'routed.def').read_text()
    for sec,name in [('COMPONENTS','component-differences.jsonl'),('NETS','routing-differences.jsonl'),('SPECIALNETS','power-differences.jsonl'),('VIAS','via-definition-differences.jsonl')]:
        x,y=records(a,sec),records(b,sec)
        with (out/name).open('w') as f:
            for key in sorted(x.keys()|y.keys()):
                if norm(x.get(key,''))!=norm(y.get(key,'')):
                    f.write(json.dumps({'record':key,'before':x.get(key),'after':y.get(key)})+'\n')
