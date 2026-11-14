#!/usr/bin/env python3
"""Reproduce the small fixed CNN in NumPy; never train on the test set."""
import argparse,hashlib,json,math,os,time,urllib.request
from pathlib import Path
import numpy as np

URL='https://storage.googleapis.com/tensorflow/tf-keras-datasets/mnist.npz'
SHA='731c5ac602752760c8e48fbffcf8c3b850d9dc2a2aedcf2cc48468fc17b673d1'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def data(path):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 if not path.exists():
  tmp=path.with_suffix('.download')
  with urllib.request.urlopen(URL,timeout=60) as response, tmp.open('wb') as out:
   while b:=response.read(1024*1024):out.write(b)
  if sha(tmp)!=SHA:raise ValueError('MNIST digest differs')
  tmp.replace(path)
 if sha(path)!=SHA:raise ValueError('MNIST digest differs')
 with np.load(path,allow_pickle=False) as z:return {k:z[k] for k in z.files}
def forward(x,p,cache=False):
 # HWC tensor order; kernel order OC, KH, KW. maxpool tie uses first index.
 n=len(x);windows=np.lib.stride_tricks.sliding_window_view(x,(5,5),axis=(1,2)).reshape(n,576,25)
 a=windows@p['cw'].T+p['cb']
 grouped=a.reshape(n,6,4,6,4,4).transpose(0,1,3,2,4,5).reshape(n,6,6,16,4)
 winners=grouped.argmax(axis=3);v=grouped.max(axis=3);pool=np.maximum(v,0).reshape(n,144)
 logits=pool@p['fw'].T+p['fb']
 if cache:return logits,(windows,winners,v,pool)
 return logits

def accuracy(x,y,p):
 correct=0
 for i in range(0,len(x),128):correct+=int((forward(x[i:i+128],p).argmax(1)==y[i:i+128]).sum())
 return correct/len(x)
def quantize(p,cal):
 def qweight(w):
  exponent=math.ceil(math.log2(max(float(abs(w).max()),1e-20)/127))
  return np.clip(np.rint(w/(2.0**exponent)),-127,127).astype(np.int64),exponent
 cw,cwe=qweight(p['cw']);fw,fwe=qweight(p['fw'])
 peak=0.0
 for i in range(0,len(cal),128):
  _,(_,_,v,_)=forward(cal[i:i+128],p,True);peak=max(peak,float(v.max()))
 ae=math.ceil(math.log2(max(peak,1e-20)/127));shift=ae-(-7+cwe)
 if not 0<=shift<=30:raise ValueError('Conv shift outside supported range')
 cb=np.rint(p['cb']/2.0**(-7+cwe)).astype(np.int64)
 fb=np.rint(p['fb']/2.0**(ae+fwe)).astype(np.int64)
 return dict(format_version=1,name='croc_mnist_c4k5_p4_fc10',conv_weights=cw.reshape(4,5,5).tolist(),
  conv_bias=cb.tolist(),conv_shift=shift,fc_weights=fw.tolist(),fc_bias=fb.tolist(),
  scales_log2=dict(input=-7,conv_weights=cwe,activation=ae,fc_weights=fwe),
  rounding='nearest_ties_away_from_zero',weights_range=[-127,127],training_seed=20261023)
def integer_batch(images,m):
 x=(images.astype(np.int64)>>1)
 w=np.array(m['conv_weights'],dtype=np.int64).reshape(4,25)
 windows=np.lib.stride_tricks.sliding_window_view(x,(5,5),axis=(1,2)).reshape(len(x),576,25)
 a=windows@w.T+np.array(m['conv_bias'])
 shift=m['conv_shift'];a=np.maximum(a,0)
 if shift:a=(a+(1<<(shift-1)))>>shift
 a=np.minimum(a,127)
 pool=a.reshape(len(x),6,4,6,4,4).max(axis=(2,4)).reshape(len(x),144)
 scores=pool@np.array(m['fc_weights'],dtype=np.int64).T+np.array(m['fc_bias'])
 return pool,scores

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--data',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--epochs',type=int,default=15)
 args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=False)
 z=data(args.data);rng=np.random.default_rng(20261023)
 x=(z['x_train'].astype(np.int16)>>1).astype(np.float32)/128
 y=z['y_train'];p=dict(cw=(rng.normal(size=(4,25))*math.sqrt(2/25)).astype(np.float32),cb=np.zeros(4,np.float32),fw=(rng.normal(size=(10,144))*math.sqrt(2/144)).astype(np.float32),fb=np.zeros(10,np.float32))
 adam={k:[np.zeros_like(v),np.zeros_like(v)] for k,v in p.items()};step=0;history=[];best=-1;start=time.monotonic()
 for epoch in range(args.epochs):
  order=rng.permutation(55000);total=0.0
  for begin in range(0,55000,128):
   idx=order[begin:begin+128];n=len(idx);logits,(windows,winners,v,pool)=forward(x[idx],p,True)
   logits-=logits.max(1,keepdims=True);ex=np.exp(logits);probs=ex/ex.sum(1,keepdims=True)
   total+=float(-np.log(np.maximum(probs[np.arange(n),y[idx]],1e-20)).sum())
   probs[np.arange(n),y[idx]]-=1;probs/=n
   grad=dict(fw=probs.T@pool,fb=probs.sum(0))
   dp=(probs@p['fw']).reshape(n,6,6,4)*(v>0)
   dg=np.zeros((n,6,6,16,4),np.float32)
   np.put_along_axis(dg,winners[:,:,:,None,:],dp[:,:,:,None,:],axis=3)
   da=dg.reshape(n,6,6,4,4,4).transpose(0,1,3,2,4,5).reshape(n*576,4)
   grad['cw']=da.T@windows.reshape(n*576,25);grad['cb']=da.sum(0)
   step+=1;lr=0.002 if epoch<10 else 0.0007
   for k in p:
    av,bv=adam[k];av*=0.9;av+=0.1*grad[k];bv*=0.999;bv+=0.001*grad[k]**2
    p[k]-=lr*(av/(1-0.9**step))/(np.sqrt(bv/(1-0.999**step))+1e-8)
  val=accuracy(x[55000:],y[55000:],p)
  row=dict(epoch=epoch+1,loss=total/55000,validation_accuracy=val,elapsed_seconds=round(time.monotonic()-start,2));history.append(row);print(json.dumps(row),flush=True)
  if val>best:best=val;bestp={k:v.copy() for k,v in p.items()};bestepoch=epoch+1
 np.savez(args.out/'float-model.npz',**bestp)
 m=quantize(bestp,x[:2048]);(args.out/'model.json').write_text(json.dumps(m,indent=2)+'\n')
 correct=0;pred=[];counts=0
 for i in range(0,10000,128):
  _,scores=integer_batch(z['x_test'][i:i+128],m);a=scores.argmax(1);pred.extend(a.tolist());correct+=int((a==z['y_test'][i:i+128]).sum());counts+=len(a)
 floating=accuracy((z['x_test'].astype(np.int16)>>1).astype(np.float32)/128,z['y_test'],bestp)
 report=dict(status='TRAINING_EVALUATION_COMPLETE',numpy=np.__version__,seed=20261023,train_examples=55000,validation_examples=5000,test_examples=counts,best_epoch=bestepoch,validation_accuracy=best,integer_test_correct=correct,integer_test_accuracy=correct/counts,float_test_accuracy=floating,accuracy_target=0.95,accuracy_target_met=correct/counts>=0.95,quantization_drop_percentage_points=100*(floating-correct/counts),dataset_url=URL,dataset_sha256=SHA,model_sha256=sha(args.out/'model.json'),history=history,test_set_used_for_selection=False)
 (args.out/'training-report.json').write_text(json.dumps(report,indent=2)+'\n');(args.out/'predictions.bin').write_bytes(bytes(pred));print(json.dumps({k:v for k,v in report.items() if k!='history'}),flush=True)
if __name__=='__main__':main()
