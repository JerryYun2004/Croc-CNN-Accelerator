"""RC validation reused from the completed native connectivity run."""
import collections
import math
from cp0c_erc_support import require, parse_metrics
from cp0c_rc_common import classify
from cp0c_spef_trace import rows, table

def spef_stats(path):
    counts = collections.Counter()
    units = {}
    section = None
    in_net = False
    minimum = math.inf
    with path.open() as f:
        require(f.readline().startswith('*SPEF '), 'Not a SPEF file.')
        for line in f:
            p = line.split()
            if not p: continue
            key = p[0]
            if key in ('*C_UNIT','*R_UNIT','*T_UNIT'):
                units[key] = ' '.join(p[1:])
            elif key == '*D_NET':
                require(not in_net and len(p)==3, 'Malformed SPEF net.')
                value = float(p[-1])
                require(math.isfinite(value) and value>=0, 'Invalid net capacitance.')
                counts['nets'] += 1
                in_net, section = True, None
            elif key in ('*CONN','*CAP','*RES'):
                require(in_net, 'Section outside SPEF net.')
                section = key
            elif key == '*END':
                require(in_net, 'SPEF END without net.')
                counts['ends'] += 1
                in_net, section = False, None
            elif in_net and section=='*CONN' and key in ('*I','*P'):
                counts['connections'] += 1
            elif in_net and section in ('*CAP','*RES') and key.isdigit():
                require(len(p)==4 if section=='*RES' else len(p) in (3,4), 'Malformed RC row.')
                value = float(p[-1])
                require(math.isfinite(value) and value>=0, 'Invalid SPEF RC.')
                tag = 'resistances' if section=='*RES' else 'capacitances'
                counts[tag] += 1
                counts['positive_'+tag] += value>0
                if section=='*RES' and value>0: minimum = min(minimum,value)
    require(not in_net and counts['nets']==counts['ends'], 'Incomplete SPEF.')
    require(all(counts[k]>0 for k in ('nets','connections','positive_resistances','positive_capacitances')), 'Empty RC model.')
    require(units=={'*C_UNIT':'1 PF','*R_UNIT':'1 OHM','*T_UNIT':'1 NS'}, 'Unexpected SPEF units.')
    counts['minimum_positive_resistance_ohm'] = minimum
    return dict(counts)

def inspect_replay(case, reference):
    metrics = parse_metrics(case/'reports/final.tsv')
    audit = parse_metrics(case/'reports/audit.tsv')
    classified = classify(case/'reports/final-erc.rpt')
    for kind,key in (('max slew','max_slew_violations'),('max capacitance','max_cap_violations'),('max fanout','max_fanout_violations')):
        require(sum(r[0]==kind for r in classified)==metrics[key], 'ERC count/report mismatch.')
    fields = ['check','category','pin','limit','actual','reported_slack']
    table(case/'reports/classified.tsv',fields,[dict(zip(fields,r)) for r in classified])
    table(case/'reports/internal-findings.tsv',fields,[dict(zip(fields,r)) for r in classified if r[1]=='Internal'])
    full = rows(case/'reports/unannotated.tsv')
    partial = (case/'reports/partially-unannotated-drivers.txt').read_text().splitlines()
    loads = rows(case/'reports/partially-unannotated-loads.tsv')
    require(len(full)==audit['unannotated_drivers'], 'Full-driver count mismatch.')
    require(len(partial)==len(set(partial))==audit['partially_unannotated_drivers'], 'Partial-driver count mismatch.')
    require(len(loads)==audit['partially_unannotated_loads'], 'Missing-load count mismatch.')
    require(all(r['driver'] in set(partial) and r['missing_load'] for r in loads), 'Orphan missing load.')
    known = {(r['driver'],r['category'],r['net']) for r in rows(reference)}
    unexpected = [r for r in full if (r['driver'],r['category'],r['net']) not in known]
    table(case/'reports/unexpected-unannotated.tsv',['driver','category','net'],
          [{k:r[k] for k in ('driver','category','net')} for r in unexpected])
    return metrics,audit,sum(r[1]=='Internal' for r in classified),not partial and not loads and not unexpected
