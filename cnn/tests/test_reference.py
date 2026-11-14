import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'reference'))
from reference import infer,round_shift,validate

def zero_model():
 return dict(format_version=1,conv_weights=[[[0]*5 for _ in range(5)] for _ in range(4)],conv_bias=[0]*4,conv_shift=0,fc_weights=[[0]*144 for _ in range(10)],fc_bias=[0]*10)
class Arithmetic(unittest.TestCase):
 def test_signed_ties_away_from_zero(self):
  for v,expected in [(-5,-3),(-4,-2),(-3,-2),(-1,-1),(0,0),(1,1),(3,2),(4,2),(5,3)]:self.assertEqual(round_shift(v,1),expected)
 def test_zero_shift_and_large_shift(self):
  for v in [-(1<<31),-1,0,1,(1<<31)-1]:self.assertEqual(round_shift(v,0),v)
  self.assertEqual(round_shift((1<<31)-1,30),2)
  self.assertEqual(round_shift(-(1<<31),30),-2)
 def test_no_implicit_signed_shift_requirement(self):
  self.assertEqual(round_shift(-1,2),0)
 def test_zero_weights_tie_selects_lowest_class(self):
  r=infer([255]*784,zero_model());self.assertEqual(r,dict(pooled=[0]*144,scores=[0]*10,prediction=0))
 def test_bias_relu_saturation_and_pool_layout(self):
  m=zero_model();m['conv_bias']=[-5,1,128,255]
  r=infer([255]*784,m);self.assertEqual(r['pooled'],[0,1,127,127]*36)
 def test_bottom_right_pixel_and_flatten_order(self):
  m=zero_model();m['conv_weights'][3][4][4]=1;m['fc_weights'][9][143]=1
  x=[0]*784;x[-1]=254;r=infer(x,m)
  self.assertEqual(r['pooled'],[0]*143+[127]);self.assertEqual(r['scores'],[0]*9+[127]);self.assertEqual(r['prediction'],9)
 def test_input_downshift_and_fc_negative_scores(self):
  m=zero_model();m['conv_weights'][0][0][0]=1;m['fc_weights'][0]=[-1]*144
  r=infer([3]*784,m);self.assertEqual(r['pooled'],[1,0,0,0]*36);self.assertEqual(r['scores'][0],-36);self.assertEqual(r['prediction'],1)
 def test_fc_bias_and_tie(self):
  m=zero_model();m['fc_bias'][4]=7;m['fc_bias'][6]=7
  self.assertEqual(infer([0]*784,m)['prediction'],4)
 def test_reject_overflow_capable_bias(self):
  for field in ['conv_bias','fc_bias']:
   m=zero_model();m[field][0]=(1<<31)-1
   with self.assertRaises(ValueError):validate(m)
 def test_reject_bad_shapes_weights_and_shift(self):
  m=zero_model();m['fc_weights'][0].pop()
  with self.assertRaises(ValueError):validate(m)
  m=zero_model();m['conv_weights'][0][0][0]=-128
  with self.assertRaises(ValueError):validate(m)
  for s in [-1,31,0.5]:
   with self.assertRaises(ValueError):round_shift(1,s)
 def test_reject_invalid_image(self):
  for x in [[0]*783,[256]*784,[-1]*784]:
   with self.assertRaises(ValueError):infer(x,zero_model())
if __name__=='__main__':unittest.main()
