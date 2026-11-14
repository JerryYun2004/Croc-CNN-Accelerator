"""Fail-closed tests for report interpretation; no native EDA tools needed."""
import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from checks import synthesis_result,timing_result

class Checks(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name)/'report'
    def tearDown(self):self.tmp.cleanup()
    def sta(self,slack='1.250000',warning=''):
        self.p.write_text('CP2A_TIMING_CHECKS\n'+warning+'CP2A_SETUP_PATHS\nCP2A_WORST_MAX_BEGIN\nworst slack '+slack+'\nCP2A_WORST_MAX_END\nCP2A_STA_COMPLETE\n')
    def test_setup_pass(self):self.sta();self.assertTrue(timing_result(self.p)['setup_pass'])
    def test_explicit_max_format(self):
        self.sta();self.p.write_text(self.p.read_text().replace('worst slack ','worst slack max '))
        self.assertTrue(timing_result(self.p)['setup_pass'])
    def test_open_physical_issues_are_recorded(self):
        self.sta();self.p.write_text(self.p.read_text().replace('CP2A_WORST_MAX_BEGIN',
            'CP2A_HOLD_ESTIMATE_ONLY\n-0.0156 slack (VIOLATED)\nCP2A_MAX_SLEW_CAP_FANOUT\nmax fanout\npin 8 9 (VIOLATED)\nCP2A_WORST_MAX_BEGIN'))
        result=timing_result(self.p)
        self.assertEqual(result['electrical_violating_pin_counts']['max fanout'],1)
        self.assertEqual(result['electrical_violating_pin_counts']['max slew'],0)
        self.assertEqual(result['worst_reported_hold_slack_ns'],-0.0156)
        self.assertFalse(result['electrical_closed'])
    def test_negative_slack(self):self.sta('-0.000001');self.assertFalse(timing_result(self.p)['setup_pass'])
    def test_missing_completion(self):
        self.sta();self.p.write_text(self.p.read_text().replace('CP2A_STA_COMPLETE',''))
        with self.assertRaises(RuntimeError):timing_result(self.p)
    def test_unconstrained_warning(self):
        self.sta(warning='Warning: unconstrained endpoint\n')
        with self.assertRaises(RuntimeError):timing_result(self.p)
    def test_sta_error_even_with_marker(self):
        self.sta();self.p.write_text(self.p.read_text()+'Error: link failed\n')
        with self.assertRaises(RuntimeError):timing_result(self.p)
    def synth(self,kinds=None,memory=0):
        self.p.write_text(json.dumps(dict(modules={'\\cnn_engine':dict(num_cells=1,area=10,num_memories=memory,num_cells_by_type=kinds or {'sg13g2_inv_1':1})})))
    def test_mapped_area(self):self.synth();self.assertEqual(synthesis_result(self.p)['engine_cell_area_um2'],10)
    def test_generic_cell(self):
        self.synth({'$mul':1})
        with self.assertRaises(RuntimeError):synthesis_result(self.p)
    def test_memory_not_hidden(self):
        self.synth(memory=1)
        with self.assertRaises(RuntimeError):synthesis_result(self.p)

if __name__=='__main__':unittest.main()
