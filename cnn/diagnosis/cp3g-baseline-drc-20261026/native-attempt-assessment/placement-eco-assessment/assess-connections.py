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
out=b/'placement-eco-assessment'
# One concrete proposal, not a sweep. Same-row origin1366.080/1050.840.
paths=[('output west trunk','Metal2',(1367520,1050420),(1367520,1059240),200),('output lower reconnect','Metal2',(1367520,1050420),(1369440,1050420),200),('output upper reconnect','Metal2',(1367520,1059240),(1368480,1059240),200),('input stub','Metal2',(1368960,1051680),(1368960,1052520),200),('input overpass','Metal3',(1368960,1051680),(1371360,1051680),200)]
shapes=[]
for li in layout.layer_indexes():
 layer='Metal2' if 'Metal2' in str(layout.get_info(li)) else ('Metal3' if 'Metal3' in str(layout.get_info(li)) else None)
 if layer is None:continue
 it=layout.top_cell().begin_shapes_rec_touching(li,k.Box(1366500,1049700,1372200,1060000))
 while not it.at_end():
  sh=it.shape()
  if not sh.is_text():
   poly=sh.polygon.transformed(it.trans());r=poly.bbox();net=sh.properties().get('net');disp=it.trans().disp
   if net is None:net=viaowners.get((it.cell().name.removeprefix('VIA_'),disp.x,disp.y))
   shapes.append(dict(net=net,layer=layer,cell=it.cell().name,box=[r.left,r.bottom,r.right,r.top],points=[[p.x,p.y] for p in poly.each_point_hull()]))
  it.next()
hits=[]
for name,layer,a,z,w in paths:
 box=k.Box(min(a[0],z[0])-w//2,min(a[1],z[1])-w//2,max(a[0],z[0])+w//2,max(a[1],z[1])+w//2)
 for r in shapes:
  if r['layer']!=layer or r['net'] in ('net938','cp3d_fanout_net_22022203'):continue
  other=k.Box(*r['box']);dx=max(box.left-other.right,other.left-box.right,0);dy=max(box.bottom-other.top,other.bottom-box.top,0);dist=math.hypot(dx,dy)
  req=560 if r['net'] and ('clock' in r['net'] or r['net'].startswith('clk')) else 210
  if dist<req:hits.append(dict(path=name,blocker=r,clearance_um=dist/1000,required_um=req/1000))
(out/'connection-corridor-assessment.json').write_text(json.dumps(dict(paths=paths,hits=hits),indent=2)+'\n')
for h in hits:print(h['path'],h['blocker']['net'],h['blocker']['layer'],h['blocker']['box'],'clearance',h['clearance_um'],'required',h['required_um'])
print('blocking pairs',len(hits))
