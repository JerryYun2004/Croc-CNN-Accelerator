"""Bounded capture-clock extensions: preserve all original clock connections."""
import re
from collections import Counter
from pathlib import Path
from common import require
from power import records
from electrical import unescape

def capture_targets(reports):
    rows={}
    for corner in ['tt','ff']:
        text=(Path(reports)/f'capture-{corner}-max.rpt').read_text()
        for block in re.split(r'(?=^Startpoint:)',text,flags=re.M):
            m=re.search(r'^\s*([-+\d.]+)\s+slack \(',block,re.M)
            if not m or float(m[1])>=0 or 'Path Group: clk_sys\n' not in block:continue
            m2=re.search(r'^Endpoint: (\S+)\n\s+\(rising edge-triggered flip-flop clocked by clk_sys\)',block,re.M)
            require(m2 is not None,'Unsupported negative setup endpoint')
            target=unescape(m2[1]);rows[target]=min(rows.get(target,0),float(m[1]))
    selected=sorted(rows,key=lambda n:(rows[n],n))[:64]
    return [dict(instance=n,slack_ns=rows[n],master='sg13g2_buf_1') for n in sorted(selected)]

def clock_audit(before,after,events):
    def parse(p):
        t=Path(p).read_text();cells={unescape(n):b.split()[0] for n,b in records(t,'COMPONENTS').items()}
        nets={unescape(n):{tuple(unescape(x) for x in p) for p in re.findall(r'\( (\S+) (\S+) \)',b)} for n,b in records(t,'NETS').items() if '+ USE CLOCK' in b}
        return cells,nets
    ac,an=parse(before);bc,bn=parse(after);require(an,'Missing reference clock nets')
    expected=Counter((x['instance'],e['prefix'],x['master']) for e in events for x in e['targets'])
    require(all(re.fullmatch(r'cp3f_skew[1-8]_',e['prefix']) for e in events),'Unexpected clock repair prefix')
    require(len({e['prefix'] for e in events})==len(events),'Duplicate clock repair stage')
    require(all(e['targets'] and len(e['targets'])<=64 for e in events),'Invalid capture plan size')
    added={n:m for n,m in bc.items() if n not in ac and n.startswith('cp3f_skew') and '_capture_' in n}
    require(len(added)==sum(expected.values()),'Unexpected clock delay cell count')
    require(all(re.fullmatch(r'cp3f_skew[1-8]_capture_\d+(?:_\d+)?',n) for n in added),'Unexpected clock delay cell name')
    for terms in an.values():
        for inst,pin in terms:
            if inst=='PIN':continue
            if pin=='CLK' and re.fullmatch(r'sg13g2_dfrbpq_\d+',ac.get(inst,'')):
                require(re.fullmatch(r'sg13g2_dfrbpq_\d+',bc.get(inst,'')) is not None,'Original capture register type changed: '+inst)
            else:require(bc.get(inst)==ac.get(inst),'Original clock driver master changed: '+inst)
    pin_net={}
    for net,pins in bn.items():
        for pin in pins:require(pin not in pin_net,'Clock pin connected twice');pin_net[pin]=net
    collapsed={n:set(p) for n,p in bn.items()};seen=set();actual=Counter()
    for target in sorted({x[0] for x in expected}):
        require(bc.get(target,'').startswith('sg13g2_dfrbpq_'),'Unexpected capture endpoint master')
        leaf=(target,'CLK');require(leaf in pin_net,'Missing capture clock pin')
        net=pin_net[leaf];downstream=leaf
        while True:
            drivers=[p for p in bn[net] if p[0] in added and p[1]=='X']
            if not drivers:break
            require(len(drivers)==1,'Multiple capture-clock drivers');inst=drivers[0][0]
            require(inst not in seen,'Branched or cyclic capture delay');seen.add(inst)
            require(bn[net]=={(inst,'X'),downstream},'Capture delay drives an unplanned load')
            require((inst,'A') in pin_net,'Missing capture delay input')
            prefix=re.match(r'(cp3f_skew[1-8]_)',inst)[1];actual[target,prefix,added[inst]]+=1
            collapsed.pop(net);net=pin_net[inst,'A'];downstream=(inst,'A')
        require(net in an,'Capture chain does not reach original clock net')
        collapsed[net].discard(downstream);collapsed[net].add(leaf)
    require(seen==set(added) and actual==expected,'Clock delay chain differs from planned targets')
    require(collapsed==an,'Original clock topology/domain changed')
    return dict(status='CONTROLLED_CAPTURE_CLOCK_EXTENSIONS_VERIFIED',original_clock_nets=len(an),added_capture_delays=len(added),original_roots_and_connections_preserved_after_collapsing_planned_delays=True,events=events)
