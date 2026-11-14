import signal,json
from pathlib import Path
import klayout.db as k
signal.alarm(30)
b=Path(__file__).resolve().parent
repo=b.parents[2]
c=k.LEFDEFReaderConfiguration();c.lef_files=[str(repo/'ihp13/pdk/lef'/n) for n in ['sg13cmos5l_tech.lef','sg13g2_stdcell.lef','sg13cmos5l_io.lef','bondpad5l_70x70.lef']]+[str(p) for p in sorted((repo/'ihp13/pdk/lef').glob('RM_IHPSG13*.lef'))]
c.net_property_name='net';c.instance_property_name='instance';c.pin_property_name='pin';c.produce_lef_pins=True;c.produce_obstructions=True
opt=k.LoadLayoutOptions();opt.lefdef_config=c
layout=k.Layout();layout.read(str(b/'work/full/baseline/routed.def'),opt)
print('DBU',layout.dbu,'layers',[(i,str(layout.get_info(i))) for i in layout.layer_indexes() if 'Metal2' in str(layout.get_info(i))])
box=k.DBox(556.8,840.2,558.7,841.8).to_itype(layout.dbu)
rows=[]
for li in layout.layer_indexes():
 info=str(layout.get_info(li))
 if 'Metal2' not in info:continue
 it=layout.top_cell().begin_shapes_rec_touching(li,box)
 while not it.at_end():
  shape=it.shape();poly=shape.polygon.transformed(it.trans()) if not shape.is_text() else None
  if poly is not None:
   rows.append(dict(layer=info,cell=it.cell().name,properties=shape.properties(),points=[[p.x*layout.dbu,p.y*layout.dbu] for p in poly.each_point_hull()],bbox=str(poly.bbox().to_dtype(layout.dbu))))
  it.next()
(b/'local-shapes.json').write_text(json.dumps(rows,indent=2)+'\n')
print('shapes',len(rows))
for i,r in enumerate(rows):print(i,r['layer'],r['cell'],r['properties'],r['bbox'])
