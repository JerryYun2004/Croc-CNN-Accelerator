import signal,json,re
from pathlib import Path
import klayout.db as k
signal.alarm(30)
b=Path(__file__).resolve().parent
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
rows=[]
for region,coords in [('short',(1369.6,1051.6,1371.4,1053.4)),('spacing',(1212.3,892.8,1213.7,894.3))]:
 box=k.DBox(*coords).to_itype(layout.dbu)
 for li in layout.layer_indexes():
  info=str(layout.get_info(li))
  if 'Metal2' not in info:continue
  it=layout.top_cell().begin_shapes_rec_touching(li,box)
  while not it.at_end():
   shape=it.shape()
   if not shape.is_text():
    poly=shape.polygon.transformed(it.trans());props=shape.properties();net=props.get('net');via=it.cell().name.removeprefix('VIA_');disp=it.trans().disp
    if net is None:net=viaowners.get((via,disp.x,disp.y))
    rows.append(dict(region=region,layer=info,cell=it.cell().name,net=net,origin=[disp.x,disp.y],properties=props,points=[[p.x*layout.dbu,p.y*layout.dbu] for p in poly.each_point_hull()],bbox=str(poly.bbox().to_dtype(layout.dbu))))
   it.next()
(b/'shapes.json').write_text(json.dumps(rows,indent=2)+'\n')
for i,r in enumerate(rows):print(i,r['region'],r['net'],r['cell'],r['bbox'])
