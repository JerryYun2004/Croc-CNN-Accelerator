#!/usr/bin/env python3
import json,sys
from pathlib import Path
from cp0c_integration_common import db,require
require(db.__version__=='0.30.9','Expected KLayout 0.30.9')
mode=sys.argv[1];cfg=json.loads(Path(sys.argv[2]).read_text());out=Path(sys.argv[3]);out.mkdir(exist_ok=True)
if mode=='prepare':
    from cp0c_integration_prepare import prepare
    prepare(cfg,out)
else:
    require(mode in ('baseline','io_wrong_supply','gpio_swap','sram_supply_open','missing_io_resistor'),'Unknown comparison mode')
    from cp0c_integration_compare import compare
    compare(cfg,out,mode)
