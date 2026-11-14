"""Prove interchangeable combinational inputs by exhaustive Liberty truth tables."""
import itertools
import re
from common import require

def expression(text):
    tokens=re.findall(r'[A-Za-z_][A-Za-z_0-9]*|[01]|[!()*+^]',text)
    require(''.join(tokens)==re.sub(r'\s+','',text),'Unsupported Liberty Boolean expression')
    pos=0
    def atom():
        nonlocal pos
        require(pos<len(tokens),'Incomplete Boolean expression')
        token=tokens[pos];pos+=1
        if token=='!':return ('not',atom())
        if token=='(':
            value=level(0);require(pos<len(tokens) and tokens[pos]==')','Missing Boolean parenthesis');pos+=1;return value
        require(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*|[01]',token) is not None,'Invalid Boolean atom')
        return ('var',token)
    def level(n):
        nonlocal pos
        if n==3:return atom()
        value=level(n+1);op=['+','^','*'][n]
        while pos<len(tokens) and tokens[pos]==op:
            pos+=1;value=(op,value,level(n+1))
        return value
    tree=level(0);require(pos==len(tokens),'Trailing Boolean tokens');return tree

def evaluate(tree,values):
    op=tree[0]
    if op=='var':return int(tree[1]) if tree[1] in ['0','1'] else values[tree[1]]
    if op=='not':return 1-evaluate(tree[1],values)
    a,b=evaluate(tree[1],values),evaluate(tree[2],values)
    return a|b if op=='+' else a^b if op=='^' else a&b

def input_classes(cell):
    inputs=sorted(p for p,a in cell['pins'].items() if a['direction']=='input')
    identity={p:(p,) for p in inputs}
    if cell['sequential'] or len(inputs)<2:return identity
    expressions=[a[k] for a in cell['pins'].values() for k in ['function','three_state'] if k in a]
    if not expressions:return identity
    require(len(inputs)<=10,'Combinational truth table too large')
    trees=[expression(s) for s in expressions]
    cases=[dict(zip(inputs,bits)) for bits in itertools.product([0,1],repeat=len(inputs))]
    reference=[tuple(evaluate(t,c) for t in trees) for c in cases]
    equivalent={p:{p} for p in inputs}
    for a,b in itertools.combinations(inputs,2):
        swapped=[dict(c,**{a:c[b],b:c[a]}) for c in cases]
        if all(tuple(evaluate(t,c) for t in trees)==r for c,r in zip(swapped,reference)):
            equivalent[a].add(b);equivalent[b].add(a)
    # Pairwise interchangeable pins must form equivalence classes. Reject an
    # unexpected library relation rather than extending it by assumption.
    require(all(equivalent[a]==equivalent[b] for a in inputs for b in equivalent[a]),'Nontransitive input symmetry')
    return {p:tuple(sorted(equivalent[p])) for p in inputs}
