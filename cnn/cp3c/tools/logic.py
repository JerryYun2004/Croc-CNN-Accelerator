"""Structural preservation under buffers, inverters, ties and equivalent sizing.

Retains every other original leaf and its full pin-level connection relation.
This is a bounded transformation audit, not unconstrained formal equivalence.
"""
import re
from collections import Counter,defaultdict
from functools import lru_cache
from pathlib import Path
from common import require
from power import records,norm

def blocks(text,kind):
    for m in re.finditer(r'\b'+kind+r'\s*\(\s*([^)]*)\)\s*\{',text):
        i=m.end();depth=1;quoted=False;escape=False
        for j in range(i,len(text)):
            c=text[j]
            if quoted:
                if c=='"' and not escape:quoted=False
                escape=(c=='\\' and not escape);continue
            if c=='"':quoted=True
            elif c=='{':depth+=1
            elif c=='}':
                depth-=1
                if depth==0:yield m[1].strip(),text[i:j];break
        else:raise RuntimeError('Unclosed Liberty group '+kind)

@lru_cache(maxsize=4)
def library(path):
    out={}
    for name,body in blocks(Path(path).read_text(),'cell'):
        pins={}
        for pin,pb in blocks(body,'pin'):
            attrs={k:norm(v.strip('"')) for k,v in re.findall(r'\b(direction|function|three_state)\s*:\s*([^;]+);',pb)}
            require('direction' in attrs,'Pin direction absent');pins[pin]=attrs
        seq=[(kind,n,norm(b)) for kind in ['ff','latch'] for n,b in blocks(body,kind)]
        kind='logic';args=[]
        inp=[p for p,a in pins.items() if a['direction']=='input'];op=[p for p,a in pins.items() if a['direction']=='output']
        if not seq and len(op)==1:
            f=re.sub(r'[\s()]','',pins[op[0]].get('function',''))
            if len(inp)==1 and f in [inp[0],'!'+inp[0]]:kind='wire';args=[inp[0],op[0],int(f.startswith('!'))]
            elif not inp and f in ['0','1']:kind='constant';args=[op[0],int(f)]
        out[name]=dict(pins=pins,sequential=seq,kind=kind,args=args)
    require(len(out)>50,'Incomplete standard-cell library');return out

class Parity:
    def __init__(self):self.parent={};self.parity={};self.rank={}
    def find(self,x):
        if x not in self.parent:self.parent[x]=x;self.parity[x]=0;self.rank[x]=0
        if self.parent[x]!=x:
            p,b=self.find(self.parent[x]);self.parity[x]^=b;self.parent[x]=p
        return self.parent[x],self.parity[x]
    def join(self,a,b,v,strict=False):
        aa,pa=self.find(a);bb,pb=self.find(b)
        if aa==bb:
            require(not strict,'Buffer/inverter cycle or reconvergent drivers')
            require((pa^pb)==v,'Contradictory buffer/inverter loop');return
        if self.rank[aa]<self.rank[bb]:aa,bb=bb,aa
        self.parent[bb]=aa;self.parity[bb]=pa^pb^v
        if self.rank[aa]==self.rank[bb]:self.rank[aa]+=1

def graph(path,lib):
    s=Path(path).read_text();comp=records(s,'COMPONENTS');cells={n:b.split()[0] for n,b in comp.items()}
    pn={};nets={};pg={};ds=Parity()
    for sec in ['NETS','SPECIALNETS']:
        for n,b in records(s,sec).items():
            terms=re.findall(r'\( (\S+) (\S+) \)',b)
            if '+ USE POWER' in b or '+ USE GROUND' in b:
                pg[n]=set(terms);continue
            require(n not in nets,'Duplicate signal net');nets[n]=terms
            for pin in terms:require(pin not in pn,'Pin on two nets');pn[pin]=n
    transparent=set();constant=set()
    for inst,master in cells.items():
        cell=lib.get(master)
        if cell and cell['kind']=='wire':
            transparent.add(inst);a,b,inv=cell['args'];an=pn.get((inst,a));bn=pn.get((inst,b))
            if an is not None and bn is not None:ds.join(an,bn,inv,strict=True)
            # An output-only buffer would add an unconstrained driver.
            require(an is not None or bn is None,'Driven buffer output has no input: '+inst)
        elif cell and cell['kind']=='constant':
            constant.add(inst);p,v=cell['args'];n=pn.get((inst,p))
            if n is not None:ds.join(n,'@CONSTANT_ZERO',v)
    ignored=transparent|constant
    anchors={p:n for p,n in pn.items() if p[0] not in ignored}
    groups=defaultdict(list)
    for p,n in anchors.items():
        root,x=ds.find(n);groups[root].append((p,x))
    if constant:
        root,x=ds.find('@CONSTANT_ZERO');groups[root].append((('@CONSTANT_ZERO','value'),x))
    signatures=[]
    for group in groups.values():
        group.sort();offset=group[0][1];signatures.append(tuple((p,x^offset) for p,x in group))
    signatures.sort()
    return dict(text=s,components=comp,cells=cells,transparent=transparent,constant=constant,
        anchors=anchors,signatures=signatures,power=pg,nets=nets)

def audit(before,after,libpath):
    lib=library(libpath);a=graph(before,lib);b=graph(after,lib)
    ac=set(a['cells'])-a['transparent']-a['constant'];bc=set(b['cells'])-b['transparent']-b['constant']
    require(ac==bc,'Non-buffer/constant cell set changed')
    resized=[]
    for n in sorted(ac):
        am,bm=a['cells'][n],b['cells'][n]
        if am!=bm:
            require(am in lib and bm in lib and lib[am]==lib[bm],'Cell logic changed: '+n)
            resized.append(dict(instance=n,before=am,after=bm))
    require(a['anchors'].keys()==b['anchors'].keys(),'Logical terminal set changed')
    require(a['signatures']==b['signatures'],'Logic connection or polarity changed after collapsing buffers/inverters/ties')
    fixed=[]
    for n,body in a['components'].items():
        if '+ FIXED ' in body:
            require(n in b['components'] and norm(body)==norm(b['components'][n]),'Fixed instance changed: '+n);fixed.append(n)
    for section,pat in [('pins',None),('die',r'^DIEAREA[^;]*;'),('rows',r'^ROW[^;]*;'),('tracks',r'^TRACKS[^;]*;')]:
        aa={n:norm(v) for n,v in records(a['text'],'PINS').items()} if pat is None else Counter(norm(x) for x in re.findall(pat,a['text'],re.M))
        bb={n:norm(v) for n,v in records(b['text'],'PINS').items()} if pat is None else Counter(norm(x) for x in re.findall(pat,b['text'],re.M))
        require(aa==bb,'Fixed '+section+' changed')
    added=set(b['cells'])-set(a['cells']);removed=set(a['cells'])-set(b['cells'])
    return dict(status='BOUNDED_LOGIC_TRANSFORM_VERIFIED',logical_anchor_terminals=len(a['anchors']),
        canonical_connection_groups=len(a['signatures']),fixed_instances=len(fixed),
        added_cells=len(added),removed_cells=len(removed),resized_logic_cells=len(resized),
        added_masters=dict(Counter(b['cells'][n] for n in added)),removed_masters=dict(Counter(a['cells'][n] for n in removed)),
        resizing=resized,scope='Exact retained leaf logic and pin relations modulo buffers, inverters and constants; no retiming, gate cloning or pin swaps')
