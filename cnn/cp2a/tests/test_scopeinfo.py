"""Metadata cleanup must not accept or delete real unmapped circuitry."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from checks import metadata_cleanup,synthesis_result

class ScopeInfo(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.before=self.root/'before.json';self.after=self.root/'after.json'
        self.top=dict(num_cells=3,area=10.0,num_memories=0,
                      num_cells_by_type={'sg13g2_inv_1':1,'$scopeinfo':2})
    def tearDown(self):self.tmp.cleanup()
    def write(self,path,top):path.write_text(json.dumps({'modules':{'\\cnn_engine':top}}))
    def pair(self):
        after=copy.deepcopy(self.top);after['num_cells']-=after['num_cells_by_type'].pop('$scopeinfo',0)
        self.write(self.before,self.top);self.write(self.after,after);return after
    def test_two_metadata_records_only(self):
        self.pair();self.assertEqual(metadata_cleanup(self.before,self.after)['removed_count'],2)
        self.assertEqual(synthesis_result(self.after)['engine_cell_count'],1)
    def test_older_yosys_without_metadata(self):
        self.top['num_cells_by_type'].pop('$scopeinfo');self.top['num_cells']=1
        self.pair();self.assertEqual(metadata_cleanup(self.before,self.after)['removed_count'],0)
    def test_scopeinfo_is_still_rejected_in_final_report(self):
        self.write(self.after,self.top)
        with self.assertRaisesRegex(RuntimeError,r'\$scopeinfo'):synthesis_result(self.after)
    def test_real_generic_cell_still_fails(self):
        self.top['num_cells_by_type']['$mul']=1;self.top['num_cells']+=1;self.pair()
        metadata_cleanup(self.before,self.after)
        with self.assertRaisesRegex(RuntimeError,r'\$mul'):synthesis_result(self.after)
    def test_deleting_real_cell_fails_audit(self):
        self.top['num_cells_by_type']['$mul']=1;self.top['num_cells']+=1;after=self.pair()
        after['num_cells_by_type'].pop('$mul');after['num_cells']-=1;self.write(self.after,after)
        with self.assertRaises(RuntimeError):metadata_cleanup(self.before,self.after)
    def test_changed_area_fails_audit(self):
        after=self.pair();after['area']=9.0;self.write(self.after,after)
        with self.assertRaises(RuntimeError):metadata_cleanup(self.before,self.after)
    def test_changed_count_fails_audit(self):
        after=self.pair();after['num_cells']+=1;self.write(self.after,after)
        with self.assertRaises(RuntimeError):metadata_cleanup(self.before,self.after)
    def test_changed_memory_fails_audit(self):
        after=self.pair();after['num_memories']=1;self.write(self.after,after)
        with self.assertRaises(RuntimeError):metadata_cleanup(self.before,self.after)

if __name__=='__main__':unittest.main()
