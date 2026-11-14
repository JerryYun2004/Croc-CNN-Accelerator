#!/usr/bin/env python3
"""Prepare or compare four filler/corner boundary diagnostics."""
import sys
from cp0c_io_fill_controls import *

def main():
    mode=sys.argv[1];cfg=json.loads(Path(sys.argv[2]).read_text());out=Path(sys.argv[3]);out.mkdir(exist_ok=True)
    require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
    if mode=='prepare':
        pins=json.loads(Path(cfg['pins']).read_text())
        write_json(out/'prepared.json',prepare(cfg['io_gds'],cfg['io_reader_cdl'],out,pins['geometry']))
        print('IO_FILL_FIXTURES_PREPARED');return
    require(mode=='compare','Unknown stage')
    prepared=json.loads(Path(cfg['prepared']).read_text());targets={};failures={}
    require(set(prepared)==set(TARGETS),'Prepared targets incomplete')
    for key in TARGETS:
        try:
            targets[key]=compare_target(key,Path(cfg['probes'])/key,prepared[key]['geometry'],out/key)
            print(key+': boundary match; 35 negative controls rejected',flush=True)
        except Exception as exc:
            failures[key]=str(exc);print('REVIEW '+key+': '+str(exc),flush=True)
    final={'status':'IO_FILL_DIAGNOSTIC_MATCH_VERIFIED' if not failures else 'IO_FILL_REVIEW_REQUIRED',
        'targets':targets,'failures':failures,'full_chip_lvs':False,'candidate_promoted':False,'cp0c':'PENDING'}
    if not failures:validate_result(final,out)
    write_json(out/'conclusion.json',final);print(final['status'])

if __name__=='__main__':main()
