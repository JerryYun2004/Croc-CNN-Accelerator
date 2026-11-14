#!/usr/bin/env python3
"""Separate process so KLayout's native diagnostics are retained in the stage log."""
import json
import sys
from cp0c_gds_support import assemble,merge_seal

args=json.load(open(sys.argv[2]))
if sys.argv[1]=='assemble':assemble(**args)
elif sys.argv[1]=='merge':merge_seal(**args)
else:raise SystemExit('Unknown worker stage')
print('CP0C_GDS_WORKER_COMPLETE '+sys.argv[1],flush=True)
