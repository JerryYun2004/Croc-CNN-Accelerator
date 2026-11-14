"""Fail-closed structural and placement checks; no timing/signoff claims."""
import csv,json,math,re
from collections import Counter
from pathlib import Path
from common import require
MACRO='RM_IHPSG13_1P_512x32_c2_bm_bist'
TEST_PORTS=['A_BIST_CLK','A_BIST_EN','A_BIST_MEN','A_BIST_WEN','A_BIST_REN','A_BIST_ADDR','A_BIST_DIN','A_BIST_BM']

def mapped(directory,repo,variant):
    before=json.loads((directory/'out/mapped-with-metadata.json').read_text())['modules']
    modules=json.loads((directory/'out/mapped.json').read_text())['modules']
    require(before.keys()==modules.keys(),'Metadata cleanup changed modules')
    removed=0
    for name,m in before.items():
        expected={n:c for n,c in m.get('cells',{}).items() if c['type']!='$scopeinfo'}
        removed+=len(m.get('cells',{}))-len(expected)
        require(expected==modules[name].get('cells',{}),'Metadata cleanup changed circuit: '+name)
    leaves=[]
    def walk(name,path,inputs):
        require(name in modules,'Unknown cell type: '+name)
        m=modules[name];require(not m.get('memories') and not m.get('processes'),'Inferred memory/process remains')
        known={'0':'0','1':'1'}
        for port,bits in inputs.items():
            if port in m.get('ports',{}):known.update(zip(m['ports'][port]['bits'],bits))
        for c in m.get('cells',{}).values():
            if c['type']=='sg13g2_tielo':known.update({b:'0' for b in c['connections']['L_LO']})
            if c['type']=='sg13g2_tiehi':known.update({b:'1' for b in c['connections']['L_HI']})
        for n,c in m.get('cells',{}).items():
            t=c['type'];full=path+n;connections={p:[known.get(b,'unknown') for b in bits] for p,bits in c['connections'].items()}
            require(t in modules,'Undefined module: '+t)
            if modules[t].get('attributes',{}).get('blackbox','0').endswith('1'):
                require(t==MACRO or t.startswith('sg13g2_') or t.startswith('sg13cmos5l_IOPad'),'Unmapped/unknown leaf: '+t)
                leaves.append((full,t,c))
                if t==MACRO:
                    ports=TEST_PORTS+(['A_DLY'] if '/i_user/' in full else [])
                    for p in ports:require(p in connections and set(connections[p])=={'0'},'Unsafe SRAM test tie: '+full+'/'+p)
                    for p,width in [('A_ADDR',9),('A_BM',32),('A_DIN',32),('A_DOUT',32)]:require(len(c['connections'][p])==width,'SRAM port width: '+p)
            else:walk(t,full+'/',connections)
    walk('croc_chip','',{})
    count=Counter(t for n,t,c in leaves);macros=[n for n,t,c in leaves if t==MACRO]
    expected=4 if variant=='cnn' else 2
    require(len(macros)==expected,'Wrong whole-chip SRAM count')
    require(sum('/i_user/' in n for n in macros)==expected-2,'Wrong CNN SRAM hierarchy')
    pads={n:t for n,t,c in leaves if t.startswith('sg13cmos5l_IOPad')}
    require(len(pads)==64 and all(n.startswith('pad_') for n in pads),'Expected original 64 pad instances')
    text=(repo/'ihp13/pdk/lib/sg13g2_stdcell_typ_1p20V_25C.lib').read_text()
    areas={}
    for block in re.split(r'\bcell\s*\(',text)[1:]:
        n=block.split(')',1)[0].strip().strip('"');a=re.search(r'\barea\s*:\s*([\d.eE+-]+)\s*;',block)
        require(a is not None,'Missing Liberty area: '+n);areas[n]=float(a.group(1))
    logic=sum(v*areas[t] for t,v in count.items() if t.startswith('sg13g2_'))
    require(math.isfinite(logic) and logic>0,'Invalid logic area')
    return dict(variant=variant,standard_cells=sum(v for t,v in count.items() if t.startswith('sg13g2_')),
        logic_area_um2=logic,sram_macros=len(macros),sram_area_um2=len(macros)*416.64*191.34,
        core_cell_area_um2=logic+len(macros)*416.64*191.34,macro_names=macros,pads=pads,
        cell_types=dict(count),scope_metadata_removed=removed,macro_test_inputs_tied_low=True)

def geometry(directory,synth):
    log=(directory/'placement.log').read_text()
    require('CP3A_PLACEMENT_PASS' in log and not re.search(r'^Error|\[ERROR',log,re.M),'Placement did not complete cleanly')
    g={row[0]:[float(x) for x in row[1:]] for row in csv.reader((directory/'geometry.tsv').open(),delimiter='\t')}
    dbu=g['dbu'][0];die=[x/dbu for x in g['die']];core=g['core']
    require(die==[0,0,1916,1916],'Die changed')
    require(all(abs(x-y)<5 for x,y in zip(core,[330,330,1586,1586])),'Core moved beyond site snapping')
    rows=list(csv.DictReader((directory/'instances.tsv').open(),delimiter='\t'))
    for r in rows:r['bbox']=[int(r[k])/dbu for k in ['x0','y0','x1','y1']]
    macros=[r for r in rows if r['master']==MACRO]
    require(len(macros)==synth['sram_macros'],'Placed macro count differs')
    standard=[r for r in rows if r['master'].startswith('sg13g2_')]
    require(len(standard)==synth['standard_cells'],'Placement changed standard-cell count')
    fw=sorted([r for r in macros if '/i_croc/' in r['name']],key=lambda x:x['name'])
    cnn=sorted([r for r in macros if '/i_user/' in r['name']],key=lambda x:x['name'])
    require(len(fw)==2 and len(cnn)==len(macros)-2,'Unexpected SRAM location')
    for group,y,orient in [(fw,core[3]-20-191.34,'R0'),(cnn,core[1]+20,'MX')]:
        for i,r in enumerate(group):
            x=core[0]+20 if i==0 else core[2]-20-416.64
            require(r['orient']==orient and r['status'] in ['FIRM','LOCKED'],'SRAM orientation/fixity differs')
            require(all(abs(a-b)<.002 for a,b in zip(r['bbox'],[x,y,x+416.64,y+191.34])),'SRAM placement differs: '+r['name'])
    def overlap(a,b):return min(a[2],b[2])-max(a[0],b[0])>1e-6 and min(a[3],b[3])-max(a[1],b[1])>1e-6
    halos=[[r['bbox'][0]-10,r['bbox'][1]-10,r['bbox'][2]+10,r['bbox'][3]+10] for r in macros]
    for i,a in enumerate(halos):
        require(a[0]>=core[0] and a[1]>=core[1] and a[2]<=core[2] and a[3]<=core[3],'SRAM halo outside core')
        require(not any(overlap(a,b) for b in halos[i+1:]),'SRAM halos overlap')
    occupied={};physical_area=0.0
    for r in standard:
        a=r['bbox'];require(r['status'] in ['PLACED','FIRM','LOCKED'],'Unplaced cell: '+r['name'])
        require(a[0]>=core[0]-.002 and a[1]>=core[1]-.002 and a[2]<=core[2]+.002 and a[3]<=core[3]+.002,'Cell outside core')
        require(not any(overlap(a,b) for b in halos),'Cell intrudes into SRAM halo')
        xsite=(a[0]-core[0])/.48;yrow=(a[1]-core[1])/3.78;height=(a[3]-a[1])/3.78
        require(abs(xsite-round(xsite))<.005 and abs(yrow-round(yrow))<.001 and abs(height-round(height))<.001,'Cell off placement grid')
        for row in range(round(yrow),round(yrow)+round(height)):
            occupied.setdefault(row,[]).append((a[0],a[2],r['name']))
        physical_area+=(a[2]-a[0])*(a[3]-a[1])
    for spans in occupied.values():
        spans.sort()
        for left,right in zip(spans,spans[1:]):require(left[1]<=right[0]+.001,'Standard cells overlap: '+left[2]+' / '+right[2])
    rowarea=g['row_area_dbu2'][0]/dbu**2
    require(rowarea>0 and physical_area/rowarea<.60,'Standard-cell row utilization exceeds 60%')
    pads={r['name']:{k:r[k] for k in ['master','status','orient','bbox']} for r in rows if r['name'] in synth['pads']}
    require(len(pads)==64 and all(v['status'] in ['FIRM','LOCKED'] for v in pads.values()),'Pad count/fixity differs')
    require({n:v['master'] for n,v in pads.items()}==synth['pads'],'Pad master changed')
    return dict(die_um=die,core_um=core,available_row_area_um2=rowarea,
        placed_standard_cell_area_um2=physical_area,standard_cell_row_utilization=physical_area/rowarea,
        macros=[{k:r[k] for k in ['name','master','orient','bbox']} for r in macros],pads=pads,
        placement_legal=True,macro_halo_um=10,die_or_original_macro_movement=False)

def compare(baseline,cnn):
    require(baseline['geometry']['pads']==cnn['geometry']['pads'],'Original pads changed')
    require(baseline['geometry']['core_um']==cnn['geometry']['core_um'],'Core changed')
    b={m['name']:m for m in baseline['geometry']['macros']}
    c={m['name']:m for m in cnn['geometry']['macros'] if '/i_croc/' in m['name']}
    require(b==c,'Original firmware macros changed')
    return dict(standard_cells=cnn['synthesis']['standard_cells']-baseline['synthesis']['standard_cells'],
        logic_area_um2=cnn['synthesis']['logic_area_um2']-baseline['synthesis']['logic_area_um2'],
        sram_area_um2=cnn['synthesis']['sram_area_um2']-baseline['synthesis']['sram_area_um2'],
        original_pads_and_firmware_macros_preserved=True)
