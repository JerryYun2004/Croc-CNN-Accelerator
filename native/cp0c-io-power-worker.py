#!/usr/bin/env python3
"""Prepare, audit and compare isolated power-pad extractions."""
import sys
from cp0c_io_power_controls import *

def main():
    mode=sys.argv[1];cfg=json.loads(Path(sys.argv[2]).read_text());out=Path(sys.argv[3]);out.mkdir(exist_ok=True)
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    if mode=='prepare':
        write_json(out/'prepared.json',prepare(cfg['io_gds'],cfg['io_reader_cdl'],out))
        print('IO_POWER_FIXTURES_PREPARED');return
    require(mode=='compare','Unknown worker stage')
    results={};failures={}
    for key in TARGETS:
        try:results[key]=compare_target(key,Path(cfg['probes'])/key,out/key)
        except Exception as exc:
            failures[key]=f'{type(exc).__name__}: {exc}'
            write_json(out/(key+'-failure.json'),{'error':failures[key]})
            print('REVIEW '+key+': '+failures[key],flush=True)
    passed=not failures and all(r['status']=='IO_POWER_PAD_DIAGNOSTIC_MATCH_VERIFIED' for r in results.values())
    final={'status':'IO_POWER_DEVICE_MATCH_VERIFIED' if passed else 'IO_POWER_REVIEW_REQUIRED',
        'targets':results,'failures':failures,'full_chip_lvs':False,'candidate_promoted':False,'cp0c':'PENDING'}
    write_json(out/'conclusion.json',final);print(final['status'],flush=True)
if __name__=='__main__':main()
