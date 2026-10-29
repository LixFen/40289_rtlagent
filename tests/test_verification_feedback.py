"""Frozen reruns and report provenance use only real recorded tool evidence."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.tooling.runtime import Runtime
from agent.tooling.testbench import build_testbench
from agent.tooling.workspace import sha


class VerificationFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = Runtime('Implement module TopModule.', '', Path(self.directory.name),
                               time.monotonic()+30, lambda *_a, **_k:None)
        self.old = 'module TopModule; endmodule'
        self.new = 'module TopModule; wire changed; endmodule'
        self.call('write_file', path='src/solution.sv', content=self.old)
        self.checks = [{'id':'value','statement':'The public output matches the expected value.'}]
        self.call('set_contract', contract={'checks':self.checks})

    def tearDown(self):
        self.directory.cleanup()

    def call(self, name, **args):
        return self.runtime.dispatch({'tool':name, 'args':args})

    def simulate_failure(self, detailed=False):
        message = 'RTL_SELFTEST_FAIL check=value time=1000'
        if detailed:
            message += ' expected=7 actual=8 inputs=a=3 b=4'
        def operation(kind, args, source, contract):
            self.runtime.ws.tests_locked = True
            report = self.runtime.ws.root/'reports'/'first.log'
            report.write_text(message, encoding='utf-8')
            return {'status':'selftest_fail', 'stage':'simulate', 'top':'TopModule',
                    'candidate_sha256':sha(source.read_text(encoding='utf-8')),
                    'testbench_sha256':sha(self.runtime.ws.read_text('tests/tb.sv')),
                    'runs':[{'log_path':'reports/first.log','diagnostics':[{'message':message}]}]}
        with patch.object(self.runtime.eda, 'operation', side_effect=operation):
            return self.call('simulate', testbench_declarations='reg x;',
                             testbench_stimulus='check(0, "value");')

    def test_frozen_edit_provides_rerun_and_rejects_overwrite_before_eda(self):
        self.simulate_failure()
        before = self.runtime.ws.read_text('tests/tb.sv')
        result = self.call('write_file', path='src/solution.sv', content=self.new)
        action = result['next_action']
        self.assertEqual(action, {'tool':'simulate','args':{
            'top':'TopModule','testbench':'tests/tb.sv','test_top':'tb'}})
        self.assertNotIn('testbench_declarations', result['next_step'])
        with patch.object(self.runtime.eda, 'operation') as eda:
            for args in ({'testbench_content':'changed'}, {'testbench_stimulus':'changed'}):
                with self.assertRaisesRegex(ValueError, 'no simulation ran.*Rerun.*testbench.*amend_test'):
                    self.call('simulate', **args)
            eda.assert_not_called()
        self.assertEqual(self.runtime.ws.read_text('tests/tb.sv'), before)
        passed = {'status':'passed','stage':'simulate','candidate_sha256':sha(self.new),
                  'testbench_sha256':sha(before), 'runs':[]}
        with patch.object(self.runtime.eda, 'operation', return_value=passed) as eda:
            self.runtime.dispatch(action)
        self.assertEqual(eda.call_count, 1)
        self.assertIsNone(self.runtime.verification.pending_counterexample)
        self.assertEqual(self.runtime.ws.read_text('tests/tb.sv'), before)

    def test_log_provenance_marks_old_candidate_and_does_not_replace_evidence(self):
        self.simulate_failure()
        first = self.call('read_file', path='reports/first.log')['report']
        self.assertTrue(first['matches_current_candidate'])
        self.assertEqual(first['current_simulation_status'], 'selftest_fail')
        self.call('write_file', path='src/solution.sv', content=self.new)
        for tool in ('read_file','analyze_log'):
            result = self.call(tool, path='reports/first.log')['report']
            self.assertEqual(result['candidate_sha256'], sha(self.old))
            self.assertEqual(result['current_candidate_sha256'], sha(self.new))
            self.assertFalse(result['matches_current_candidate'])
            self.assertEqual(result['current_simulation_status'], 'not_verified')
            self.assertEqual(result['next_action']['tool'], 'simulate')
        self.assertNotIn('simulate', self.runtime.candidates.current()['evidence'])

    def test_repeated_report_windows_are_detected_across_intervening_reads(self):
        self.simulate_failure()
        first = self.call('read_file', path='reports/first.log', limit=10)['report']
        self.call('read_file', path='src/solution.sv')
        second = self.call('read_file', path='reports/first.log', limit=10)['report']
        other = self.call('read_file', path='reports/first.log', offset=10, limit=10)['report']
        self.assertFalse(first['unchanged_since_previous_read'])
        self.assertTrue(second['unchanged_since_previous_read'])
        self.assertIn('no new evidence', second['instruction'])
        self.assertFalse(other['unchanged_since_previous_read'])
        self.call('write_file', path='src/solution.sv', content=self.new)
        changed = self.call('read_file', path='reports/first.log', limit=10)['report']
        self.assertFalse(changed['unchanged_since_previous_read'])

    def test_unknown_report_does_not_invent_candidate_provenance(self):
        (self.runtime.ws.root/'reports'/'unknown.log').write_text('ERROR: unknown', encoding='utf-8')
        result = self.call('analyze_log', path='reports/unknown.log')['report']
        self.assertFalse(result['provenance_known'])
        self.assertNotIn('candidate_sha256', result)
        self.assertNotIn('matches_current_candidate', result)

    def test_failure_values_are_reported_only_when_recorded(self):
        data = self.simulate_failure()
        self.assertFalse(data['failure_values_recorded'])
        self.assertIn('no expected/actual values', data['diagnostic_note'])
        self.assertEqual(self.runtime.verification.pending_counterexample['next_action']['tool'], 'simulate')

    def test_existing_detailed_failure_keeps_actual_values(self):
        data = self.simulate_failure(detailed=True)
        self.assertTrue(data['failure_values_recorded'])
        self.assertNotIn('diagnostic_note', data)
        self.assertIn('expected=7 actual=8', self.call('analyze_log', path='reports/first.log')['diagnostics'][0]['message'])

    def test_harness_provides_value_comparison_without_replacing_boolean_checks(self):
        text = build_testbench('reg [7:0] actual=0;',
                              'check_equal(actual, 8\'d1, "value", "input=0");', self.checks)
        self.assertIn('task automatic check(input bit passed', text)
        self.assertIn('task automatic check_equal', text)
        self.assertIn('expected=%0h actual=%0h inputs=%s', text)
        self.assertIn('check(actual === expected, id);', text)

    def test_value_diagnostics_pass_workspace_hdl_validation(self):
        with patch.object(self.runtime.eda, 'operation', return_value={
                'status':'passed','stage':'simulate','candidate_sha256':sha(self.old),'runs':[]}):
            self.call('simulate', testbench_declarations='reg [7:0] actual=0;',
                      testbench_stimulus='check_equal(actual, 8\'d1, "value", $sformatf("actual_input=%0d", actual));')
        text = self.runtime.ws.read_text('tests/tb.sv')
        self.assertIn('$sformatf', text)
