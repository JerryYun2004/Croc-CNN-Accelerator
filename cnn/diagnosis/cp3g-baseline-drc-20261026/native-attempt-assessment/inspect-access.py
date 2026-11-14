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


for li in layout.layer_indexes():
 if not any(n in str(layout.get_info(li)) for n in ('Metal1','Metal3')):continue
 it=layout.top_cell().begin_shapes_rec_touching(li,k.DBox(1369.9,1052.1,1371.0,1052.9).to_itype(layout.dbu))
 while not it.at_end():
  sh=it.shape()
  if not sh.is_text():print(str(layout.get_info(li)),it.cell().name,sh.properties(),str(sh.polygon.transformed(it.trans()).bbox().to_dtype(layout.dbu)))
  it.next()
