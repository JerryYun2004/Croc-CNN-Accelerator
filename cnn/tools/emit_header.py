#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'reference'))
from reference import load

def emit(model_path,out):
 m=load(model_path);text=['#ifndef CROC_CNN_MODEL_DATA_H','#define CROC_CNN_MODEL_DATA_H','#include <stdint.h>',f'#define CONV_SHIFT {m["conv_shift"]}u']
 arrays=[('conv_weights','int8_t',[v for c in m['conv_weights'] for r in c for v in r]),('conv_bias','int32_t',m['conv_bias']),('fc_weights','int8_t',[v for r in m['fc_weights'] for v in r]),('fc_bias','int32_t',m['fc_bias'])]
 for name,ty,values in arrays:
  text.append(f'static const {ty} {name}[{len(values)}] = {{')
  for i in range(0,len(values),20):text.append('    '+', '.join(map(str,values[i:i+20]))+',')
  text.append('};')
 text+=['#endif',''];Path(out).write_text('\n'.join(text))
if __name__=='__main__':emit(sys.argv[1],sys.argv[2])
