import signal,json,re
from pathlib import Path
import klayout.db as k
signal.alarm(30)
b=Path(__file__).resolve().parent.parent
repo=b.parents[3]
candidate=repo/'cnn/results/cp3g-native-baseline-20261026-141130-4hob7_j3'
c=k.LEFDEFReaderConfiguration();c.lef_files=[str(repo/'ihp13/pdk/lef'/n) for n in ['sg13cmos5l_tech.lef','sg13g2_stdcell.lef','sg13cmos5l_io.lef','bondpad5l_70x70.lef']]+[str(p) for p in sorted((repo/'ihp13/pdk/lef').glob('RM_IHPSG13*.lef'))]
c.net_property_name='net';c.instance_property_name='instance';c.pin_property_name='pin';c.produce_lef_pins=True;c.produce_obstructions=True
opt=k.LoadLayoutOptions();opt.lefdef_config=c
layout=k.Layout();layout.read(str(candidate/'routed.def'),opt)
print('DBU',layout.dbu,'layers',[(i,str(layout.get_info(i))) for i in layout.layer_indexes() if 'Metal2' in str(layout.get_info(i))])

text=(candidate/'routed.def').read_text()
targets=['cp3d_clock_net_55061','net938','cp3d_fanout_net_15641565']
for net in targets:
 m=re.search(r'^\s*- '+re.escape(net)+r'\s.*?;',text,re.M|re.S)
 (b/(net+'.def.txt')).write_text(m.group()+'\n')
# Associate via instances with their DEF owner net using exact placements.
viaowners={}
for m in re.finditer(r'^\s*- (\S+) [^;]*;',text,re.M):
 rec=m.group()
 if '+ ROUTED' not in rec and '+ FIXED' not in rec:continue
 for v in re.finditer(r'(?:ROUTED|NEW)\s+\S+\s+(?:TAPER\s+)?\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(Via\w+)',rec):
  viaowners[(v[3],int(v[1]),int(v[2]))]=m[1]

import math
out=b/'placement-eco-assessment';rows=[]
box=k.Box(1363000,1049000,1374000,1055500)
for li in layout.layer_indexes():
 if 'Metal2' not in str(layout.get_info(li)):continue
 it=layout.top_cell().begin_shapes_rec_touching(li,box)
 while not it.at_end():
  sh=it.shape()
  if not sh.is_text():
   poly=sh.polygon.transformed(it.trans());r=poly.bbox();net=sh.properties().get('net');disp=it.trans().disp
   if net is None:net=viaowners.get((it.cell().name.removeprefix('VIA_'),disp.x,disp.y))
   rows.append(dict(net=net,cell=it.cell().name,box=[r.left,r.bottom,r.right,r.top]))
  it.next()
results=[]
for delta in (480,960,1440,1920,2400,2880):
 x=1367520-delta;hits=[]
 for pin,px,py in [('A',x+2880,1052520),('X',x+1440,1052940)]:
  landing=k.Box(px-100,py-145,px+100,py+145)
  for row in rows:
   if row['net'] in ('net938','cp3d_fanout_net_22022203'):continue
   r=k.Box(*row['box']);dx=max(r.left-landing.right,landing.left-r.right,0);dy=max(r.bottom-landing.top,landing.bottom-r.top,0);dist=math.hypot(dx,dy)
   required=560 if row['net'] and ('clock' in row['net'] or row['net'].startswith('clk')) else 210
   if dist<required:hits.append(dict(pin=pin,blocker=row,clearance_um=dist/1000,required_um=required/1000))
 results.append(dict(origin_um=[x/1000,1050.840],delta_um=-delta/1000,hits=hits))
(out/'landing-assessment.json').write_text(json.dumps(results,indent=2)+'\n');(out/'surrounding-metal2.json').write_text(json.dumps(rows,indent=2)+'\n')
for result in results:print(result['origin_um'],'hits',[(h['pin'],h['blocker']['net'],h['clearance_um'],h['required_um']) for h in result['hits']])
