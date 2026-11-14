import signal,json,html
from pathlib import Path
import klayout.db as k
signal.alarm(30)
b=Path(__file__).resolve().parent
rows=json.loads((b/'local-shapes.json').read_text())
labels={3:'S1 connecting segment',5:'S4 area patch',6:'S2 Via1_XY enclosure',7:'S3 Via2_YX enclosure',0:'N1 net3039',4:'N2 _1433_',1:'N3 _1442_',2:'N4 clock',8:'N2 Via1_YY',9:'N3 Via1_YY'}
colors={3:'#66bfa4',5:'#87cbb9',6:'#2878ba',7:'#a95ca8',0:'#b5b9bd',4:'#b5b9bd',1:'#b5b9bd',2:'#b5b9bd',8:'#c4c8cc',9:'#c4c8cc'}
reg=k.Region()
for i in (3,5,6,7):reg.insert(k.Polygon([k.Point(round(x*1000),round(y*1000)) for x,y in rows[i]['points']]))
reg.merge();marker=k.Box(557685,840985,557885,841115)
missing=k.Region(marker)-reg
print('Target merged polygons',reg.count(),'uncovered marker area DBU^2',missing.area())
(b/'union-analysis.json').write_text(json.dumps({'reader':'KLayout LEF/DEF; no DRC performed','target_union_polygons':reg.count(),'uncovered_marker_area_um2':missing.area()/1e6,'marker_enclosure_gap_um':0.130,'LEF_base_spacing_um':0.210,'note':'Candidate via pair inferred; checker reports net and bbox only. Marker is filled by connected target metal. This reconstruction does not establish checker edge identities or invalidate full DRC.'},indent=2)+'\n')
W,H=1120,850;x0,y0=556.55,840.3;scale=450
X=lambda x:80+(x-x0)*scale
Y=lambda y:720-(y-y0)*scale
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">','<defs><clipPath id="plot"><rect x="70" y="100" width="1040" height="640"/></clipPath></defs>','<rect width="100%" height="100%" fill="white"/>','<g font-family="Arial,sans-serif" fill="#182331">','<text x="35" y="34" font-size="23">Baseline Metal2: reproduced full-design marker</text>','<text x="35" y="61" font-size="15">LEF/DEF reconstruction • coordinates in µm • edge identities inferred, not checker-confirmed</text>','<g clip-path="url(#plot)">']
for i,r in enumerate(rows):
 pts=' '.join(f'{X(x):.2f},{Y(y):.2f}' for x,y in r['points'])
 svg.append(f'<polygon points="{pts}" fill="{colors[i]}" fill-opacity="0.55" stroke="{colors[i]}" stroke-width="1.5"/>')
for poly in reg.each():
 pts=' '.join(f'{X(p.x/1000):.2f},{Y(p.y/1000):.2f}' for p in poly.each_point_hull());svg.append(f'<polygon points="{pts}" fill="none" stroke="#064b37" stroke-width="3"/>')
svg.append(f'<rect x="{X(557.685)}" y="{Y(841.115)}" width="{.2*scale}" height="{.13*scale}" fill="none" stroke="#df2538" stroke-width="3" stroke-dasharray="6 3"/>')
svg.append('</g>')
for x in (556.8,557.2,557.6,558.0,558.4,558.8):svg.append(f'<text x="{X(x)-22}" y="762" font-size="13">{x:.1f}</text>')
for y in (840.4,840.8,841.2,841.6):svg.append(f'<text x="6" y="{Y(y)+4}" font-size="13">{y:.1f}</text>')
# Callout labels avoid overlays on small shapes.
for txt,px,py,tx,ty in [('S2 Via1_XY',557.78,840.76,600,660),('S3 Via2_YX',557.78,841.32,630,160),('S1 bridge',557.79,841.04,725,420),('S4 patch',557.78,840.69,690,705),('N1 net3039',558.24,841.4,850,220),('N2 _1433_',557.28,841.4,170,170),('N3 _1442_',556.8,840.8,100,610),('N4 clock',558.72,841.4,920,140)]:
 svg.append(f'<path d="M {X(px)} {Y(py)} L {tx} {ty-4}" fill="none" stroke="#465363"/><text x="{tx}" y="{ty}" font-size="15">{txt}</text>')
svg+=['<text x="35" y="800" font-size="15" fill="#c7192c">Red dashed: full-design DRC marker, (557.685,840.985)–(557.885,841.115)</text>','<text x="35" y="825" font-size="15">Dark green: merged target metal. Marker interior is covered by the bridge; no 0.130 µm void.</text>','</g></svg>']
(b/'local-geometry.svg').write_text('\n'.join(svg))
header='| ID | Net | Layer | Bounding coordinates (µm) | Source | Classification |\n|---|---|---|---|---|---|\n'
lines=[]
for i in (6,7,3,5,0,4,1,2,8,9):
 r=rows[i];net=r['properties'].get('net')
 if net is None:net='cp3d_fanout_net_30763077' if i in (6,7) else ('i_croc_soc/i_croc/i_dm_top.i_dm_top/_1433_' if i==8 else 'i_croc_soc/i_croc/i_dm_top.i_dm_top/_1442_')
 source='DEF route wire' if i in (0,1,2,3,4) else ('DEF RECT patch' if i==5 else 'LEF via enclosure + DEF via placement')
 lines.append(f"| {labels[i]} | {net} | Metal2 | {r['bbox']} | {source} | Routed in DEF; internal checker classification unexposed |")
(b/'candidate-shapes.md').write_text(header+'\n'.join(lines)+'\n\nChecker-confirmed: net cp3d_fanout_net_30763077 and marker box only. All candidate shape/edge identities above are inferred from geometry. No local fixed Metal2 pin/obstruction was returned by the LEF/DEF reader in the inspected window.\n')
