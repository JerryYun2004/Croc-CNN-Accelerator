#!/usr/bin/env python3
import faulthandler, json, resource, sys
from pathlib import Path
faulthandler.enable(all_threads=True)
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
from cp0c_integration_compare import compare
if __name__=='__main__':
    compare(json.loads(Path(sys.argv[1]).read_text()),Path(sys.argv[2]),sys.argv[3])
