"""Negative controls for the synthesis/timing acceptance checks."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from checks import synthesis,timing
BASE=Path(sys.argv.pop(1)).resolve()
LEF=Path(sys.argv.pop(1)).resolve()
class Checks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.r=Path(self.temp.name)
        self.before=json.loads((BASE/'reports/synthesis-before.json').read_text())
        self.after=json.loads((BASE/'reports/synthesis-stat.json').read_text())
        self.mapped=json.loads((BASE/'work/mapped.json').read_text())
        self.log=(BASE/'logs/timing.log').read_text()
    def tearDown(self):self.temp.cleanup()
    def top(self,data):return next(v for k,v in data['modules'].items() if k.lstrip('\\')=='cnn_peripheral')
    def area(self):
        for name,data in [('before',self.before),('after',self.after),('mapped',self.mapped)]:
            (self.r/(name+'.json')).write_text(json.dumps(data))
        return synthesis(self.r/'before.json',self.r/'after.json',self.r/'mapped.json',LEF)
    def sta(self):
        (self.r/'timing.log').write_text(self.log);(self.r/'mapped.json').write_text(json.dumps(self.mapped))
        return timing(self.r/'timing.log',self.r/'mapped.json')
    def test_good(self):self.assertEqual(self.area()['sram_macros'],2);self.assertTrue(self.sta()['setup_pass'])
    def test_unmapped(self):
        for d in [self.before,self.after]:self.top(d)['num_cells_by_type']['$add']=1;self.top(d)['num_cells']+=1
        with self.assertRaisesRegex(RuntimeError,'Unmapped'):self.area()
    def test_macro_count(self):
        for d in [self.before,self.after]:self.top(d)['num_cells_by_type']['RM_IHPSG13_1P_512x32_c2_bm_bist']=1;self.top(d)['num_cells']-=1
        with self.assertRaisesRegex(RuntimeError,'exactly two'):self.area()
    def test_inferred_memory(self):
        for d in [self.before,self.after]:self.top(d)['num_memories']=1
        with self.assertRaisesRegex(RuntimeError,'Inferred memory'):self.area()
    def test_area_changed(self):
        self.top(self.after)['area']+=1
        with self.assertRaisesRegex(RuntimeError,'changed circuit'):self.area()
    def test_bist_tie(self):
        c=next(c for c in self.mapped['modules']['cnn_peripheral']['cells'].values() if c['type'].startswith('RM_IHP'))
        c['connections']['A_BIST_EN']=['1']
        with self.assertRaisesRegex(RuntimeError,'test-mode tie'):self.area()
    def test_logic_removed(self):
        self.top(self.after)['num_cells_by_type'].pop(next(k for k in self.top(self.after)['num_cells_by_type'] if k.startswith('sg13g2_')))
        with self.assertRaisesRegex(RuntimeError,'changed circuit'):self.area()
    def test_missing_completion(self):
        self.log=self.log.replace('CP2B_STA_COMPLETE','')
        with self.assertRaisesRegex(RuntimeError,'incomplete'):self.sta()
    def test_timing_error(self):
        self.log+='\nError: invalid constraint\n'
        with self.assertRaisesRegex(RuntimeError,'tool error'):self.sta()
    def test_unknown_unconstrained(self):
        a=self.log.index('CP2B_TIMING_CHECKS')+len('CP2B_TIMING_CHECKS');b=self.log.index('CP2B_SETUP_PATHS')
        self.log=self.log[:a]+'\nWarning: There are 1 unconstrained endpoints.\n  rdata_o[0]\n'+self.log[b:]
        with self.assertRaisesRegex(RuntimeError,'Nonconstant/unexpected'):self.sta()
    def test_bad_warning(self):
        self.log=self.log.replace('CP2B_TIMING_CHECKS','CP2B_TIMING_CHECKS\nWarning: There are 1 unclocked registers.')
        with self.assertRaisesRegex(RuntimeError,'warnings need review'):self.sta()
    def test_bist_endpoint_not_tied(self):
        c=next((name,c) for name,c in self.mapped['modules']['cnn_peripheral']['cells'].items() if c['type'].startswith('RM_IHP'))
        c[1]['connections']['A_BIST_ADDR'][0]=42
        a=self.log.index('CP2B_TIMING_CHECKS')+len('CP2B_TIMING_CHECKS');b=self.log.index('CP2B_SETUP_PATHS')
        self.log=self.log[:a]+'\nWarning: There are 1 unconstrained endpoints.\n  '+c[0]+'/A_BIST_ADDR[0]\n'+self.log[b:]
        with self.assertRaisesRegex(RuntimeError,'Nonconstant/unexpected'):self.sta()
if __name__=='__main__':unittest.main()
