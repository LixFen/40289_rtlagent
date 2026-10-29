"""A tool counterexample must survive unrelated model actions and source edits."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.tooling.runtime import Runtime


class RepairMemoryTests(unittest.TestCase):
    def test_failure_survives_reads_edits_and_unrelated_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime('Implement module TopModule.', '', Path(directory), time.monotonic()+30, lambda *_a,**_k:None)
            runtime.dispatch({'tool':'write_file','args':{'path':'src/solution.sv','content':'module TopModule; endmodule'}})
            failed = {'stage':'simulate','status':'selftest_fail','testbench_sha256':'frozen-tb',
                      'runs':[{'log_path':'reports/sim.log','diagnostics':[
                          {'id':None,'message':'RTL_SELFTEST_FAIL cycle=16 expected=15 actual=0'}]}]}
            with patch.object(runtime.eda,'operation',return_value=failed):
                runtime.dispatch({'tool':'simulate','args':{}})
            digest = runtime.state()['pending_counterexample']['failed_candidate_sha256']
            with patch.object(runtime.eda,'operation',return_value={'stage':'simulate','status':'passed','runs':[], 'testbench_sha256':'frozen-tb'}):
                runtime.dispatch({'tool':'simulate','args':{}})
            self.assertIsNotNone(runtime.state()['pending_counterexample'])
            for _ in range(5):
                runtime.dispatch({'tool':'read_file','args':{'path':'src/solution.sv'}})
            runtime.dispatch({'tool':'patch_file','args':{'path':'src/solution.sv','old':'TopModule;','new':'TopModule; wire fixed;'}})
            memory = runtime.state()['pending_counterexample']
            self.assertEqual(memory['failed_candidate_sha256'],digest)
            self.assertIn('expected=15 actual=0',memory['diagnostics'][0]['message'])
            for stage, tb in [('compile',None),('simulate','different-tb')]:
                with patch.object(runtime.eda,'operation',return_value={'stage':stage,'status':'passed','runs':[], 'testbench_sha256':tb}):
                    runtime.dispatch({'tool':stage,'args':{}})
                self.assertIsNotNone(runtime.state()['pending_counterexample'])
            with patch.object(runtime.eda,'operation',return_value={'stage':'simulate','status':'passed','runs':[], 'testbench_sha256':'frozen-tb'}):
                runtime.dispatch({'tool':'simulate','args':{}})
            self.assertIsNone(runtime.state()['pending_counterexample'])
