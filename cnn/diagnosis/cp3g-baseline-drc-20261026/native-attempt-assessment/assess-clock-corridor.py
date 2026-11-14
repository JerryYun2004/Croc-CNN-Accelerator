import signal,json,re
from pathlib import Path
import klayout.db as k
signal.alarm(30)
b=Path(__file__).resolve().parent
repo=b.parents[3]
candidate=repo/'cnn/results/cp3g-native-baseline-20261026-141130-4hob7_j3'
c=k.LEFDEFReaderConfiguration();c.lef_files=[str(repo/'ihp13/pdk/lef'/n) for n in ['sg13cmos5l_tech.lef','sg13g2_stdcell.lef','sg13cmos5l_io.lef','bondpad5l_70x70.lef']]+[str(p) for p in sorted((repo/'ihp13/pdk/lef').glob('RM_IHPSG13*.lef'))]
c.net_property_name='net';c.instance_property_name='instance';c.pin_property_name='pin';c.produce_lef_pins=True;c.produce_obstructions=True
c.routing_suffix='_ROUTE';c.via_geometry_suffix='_VIA';c.lef_pins_suffix='_PIN';c.obstructions_suffix='_OBS';c.pins_suffix='_PORT'
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
# Original outer endpoints retained; proposed replacement trunk is 0.960um west.
segments=[('lower reconnection',(1369275,1050840),(1370400,1050840)),('west trunk',(1369275,1050840),(1369275,1053360)),('upper reconnection',(1369275,1053360),(1370880,1053360))]
proposed=[(name,k.Box(min(a[0],z[0])-100,min(a[1],z[1])-100,max(a[0],z[0])+100,max(a[1],z[1])+100)) for name,a,z in segments]
window=k.Box(1368500,1050000,1371700,1054200)
rows=[];blockers=[]
for li in layout.layer_indexes():
 info=str(layout.get_info(li))
 if 'Metal2' not in info:continue
 it=layout.top_cell().begin_shapes_rec_touching(li,window)
 while not it.at_end():
  sh=it.shape()
  if not sh.is_text():
   poly=sh.polygon.transformed(it.trans());box=poly.bbox();props=sh.properties();net=props.get('net');via=it.cell().name.removeprefix('VIA_');disp=it.trans().disp
   if net is None:net=viaowners.get((via,disp.x,disp.y))
   row=dict(layer=info,cell=it.cell().name,net=net,properties=props,bbox_dbu=[box.left,box.bottom,box.right,box.top],origin_dbu=[disp.x,disp.y],points_dbu=[[p.x,p.y] for p in poly.each_point_hull()])
   rows.append(row)
   if net!='cp3d_clock_net_55061':
    for name,pbox in proposed:
     dx=max(box.left-pbox.right,pbox.left-box.right,0);dy=max(box.bottom-pbox.top,pbox.bottom-box.top,0)
     dist=math.hypot(dx,dy)
     if dist<560:
      hit=dict(row,proposed_segment=name,minimum_bbox_clearance_um=dist/1000,required_NDR_spacing_um=.560)
      blockers.append(hit)
  it.next()
(b/'clock-corridor-shapes.json').write_text(json.dumps(rows,indent=2)+'\n')
(b/'clock-corridor-assessment.json').write_text(json.dumps(dict(proposal=segments,blockers=blockers,note='Bounding-box clearance is exact for rectangular shapes; polygon confirmation required otherwise. No design edits or DRC execution.'),indent=2)+'\n')
print('Metal2 layers',[(i,str(layout.get_info(i))) for i in layout.layer_indexes() if 'Metal2' in str(layout.get_info(i))])
print('local shapes',len(rows),'candidate blocking pairs',len(blockers))
for r in blockers:print(r['proposed_segment'],r['layer'],r['cell'],r['net'],r['bbox_dbu'],r['minimum_bbox_clearance_um'],r['points_dbu'])
