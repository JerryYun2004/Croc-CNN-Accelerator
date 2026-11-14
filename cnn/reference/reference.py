"""Scalar integer oracle: explicit loops, no NumPy and no floating point."""
import json
from pathlib import Path
I32_MIN=-(1<<31); I32_MAX=(1<<31)-1

def round_shift(value,shift):
 if not isinstance(shift,int) or not 0<=shift<=30:raise ValueError('shift outside 0..30')
 if shift==0:return value
 magnitude=(abs(value)+(1<<(shift-1)))>>shift
 return -magnitude if value<0 else magnitude

def validate(model):
 def integers(values,count,low,high):
  if len(values)!=count or any(type(v) is not int or not low<=v<=high for v in values):raise ValueError('invalid model dimensions/range')
 if model.get('format_version')!=1:raise ValueError('model version')
 cw=model['conv_weights'];fw=model['fc_weights']
 if len(cw)!=4 or any(len(c)!=5 or any(len(r)!=5 for r in c) for c in cw):raise ValueError('conv shape')
 integers([v for c in cw for r in c for v in r],100,-127,127)
 if len(fw)!=10:raise ValueError('FC shape')
 for row in fw:integers(row,144,-127,127)
 integers(model['conv_bias'],4,I32_MIN+25*127*127,I32_MAX-25*127*127)
 integers(model['fc_bias'],10,I32_MIN+144*127*127,I32_MAX-144*127*127)
 round_shift(0,model['conv_shift'])
 return model

def infer(image,model):
 validate(model)
 if len(image)!=784 or any(type(v) is not int or not 0<=v<=255 for v in image):raise ValueError('image must contain 784 uint8 pixels')
 x=[v>>1 for v in image];pooled=[0]*144
 # Fused pool: one convolution output at a time, then discard it.
 for py in range(6):
  for px in range(6):
   for oc in range(4):
    best=0
    for dy in range(4):
     for dx in range(4):
      y=py*4+dy;xx=px*4+dx;acc=model['conv_bias'][oc]
      for ky in range(5):
       for kx in range(5):acc+=x[(y+ky)*28+xx+kx]*model['conv_weights'][oc][ky][kx]
      value=max(0,min(127,round_shift(acc,model['conv_shift'])))
      best=max(best,value)
    pooled[(py*6+px)*4+oc]=best
 scores=[model['fc_bias'][c]+sum(a*w for a,w in zip(pooled,model['fc_weights'][c])) for c in range(10)]
 prediction=max(range(10),key=lambda c:scores[c])
 return dict(pooled=pooled,scores=scores,prediction=prediction)

def load(path):return validate(json.loads(Path(path).read_text()))
