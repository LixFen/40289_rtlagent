import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.autonomous import solve
from agent.agent_tool_registry import Directive
from agent.loop.context import Conversation
from agent.loop.tools import execute_tools
from agent.tooling.runtime import Runtime
from tests.protocol import completion


GOOD = 'module TopModule; wire [7:0] q; assign q=8\'d1; endmodule'
BAD = 'module TopModule; reg [8:0] n; wire [16:0] ext = {8{n[8]}}, n; endmodule'


class SlangCheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.rows = []
        self.runtime = Runtime('Implement module TopModule.', '', Path(self.directory.name),
                               time.monotonic()+30, lambda kind, **fields:self.rows.append(dict(tool=kind, **fields)))

    def tearDown(self):
        self.directory.cleanup()

    def call(self, name, **args):
        return self.runtime.invoke({'tool':name,'args':args})

    def test_save_runs_slang_and_preserves_source_caret_diagnostics(self):
        log = 'src/solution.sv:1:57: error: redefinition of n\nwire ext={8{n[8]}}, n;\n                    ^\n'
        with patch.dict(os.environ, {'SLANG_BIN':'test-slang'}), \
             patch('agent.agent_tool_registry.rtl_check.subprocess.run', return_value=subprocess.CompletedProcess([],1,log.encode())) as run:
            result = self.call('write_file', path='src/solution.sv', content=BAD)
        self.assertEqual(result.directive, Directive.RETURN_TO_MODEL)
        self.assertEqual(result.data['precheck']['diagnostics'], log)
        self.assertTrue(result.data['precheck']['checked'])
        self.assertIn('src/solution.sv', run.call_args.args[0])
        self.assertLessEqual(run.call_args.kwargs['timeout'],3)
        report=self.runtime.ws.root/result.data['precheck']['log_path']
        self.assertEqual(report.read_text(encoding='utf-8'),log)
        self.assertEqual(self.runtime.candidates.current()['evidence']['slang']['status'],'compile_error')
        self.assertTrue(any(row['tool']=='rtl_precheck' for row in self.rows))

    def test_warning_does_not_interrupt_normal_flow_and_patch_also_checks(self):
        with patch.dict(os.environ, {'SLANG_BIN':'test-slang'}), \
             patch('agent.agent_tool_registry.rtl_check.subprocess.run', return_value=subprocess.CompletedProcess([],0,b'warning: unused q')) as run:
            first=self.call('write_file', path='src/solution.sv', content=GOOD)
            second=self.call('patch_file', path='src/solution.sv', old="8'd1", new="8'd2")
        self.assertEqual(run.call_count,2)
        for result in (first,second):
            self.assertEqual(result.directive,Directive.CONTINUE)
            self.assertEqual(result.data['precheck']['status'],'passed')

    def test_notes_and_tests_do_not_trigger_rtl_check(self):
        with patch('agent.agent_tool_registry.tools.files.check_saved_rtl') as check:
            self.call('write_file',path='notes/plan.md',content='plan')
            self.call('write_file',path='tests/tb.sv',content='module tb; endmodule')
        check.assert_not_called()

    def test_tb_save_checks_dut_and_tb_without_penalizing_rtl(self):
        self.call('write_file', path='src/solution.sv', content=GOOD)
        with patch.dict(os.environ, {'SLANG_BIN':'test-slang'}), \
             patch('agent.agent_tool_registry.rtl_check.subprocess.run', return_value=subprocess.CompletedProcess([],1,b'error: bad TB')) as run:
            result = self.call('write_file', path='tests/tb.sv', content='module tb; endmodule')
        self.assertEqual(result.directive, Directive.RETURN_TO_MODEL)
        self.assertEqual(result.data['precheck']['status'], 'compile_error')
        self.assertIn('src/solution.sv', run.call_args.args[0])
        self.assertIn('tests/tb.sv', run.call_args.args[0])
        self.assertNotIn('--lint-only', run.call_args.args[0])
        self.assertNotIn('slang', self.runtime.candidates.current()['evidence'])
        self.assertTrue(any(row['tool']=='testbench_precheck' for row in self.rows))

    def test_inline_tb_failure_skips_vivado_and_can_be_amended(self):
        self.call('write_file', path='src/solution.sv', content=GOOD)
        checks=[{'id':'value', 'statement':'Check the public output'}]
        with patch.dict(os.environ, {'SLANG_BIN':'test-slang'}), \
             patch('agent.agent_tool_registry.rtl_check.subprocess.run', return_value=subprocess.CompletedProcess([],1,b'error: wrong check arguments')), \
             patch.object(self.runtime.eda, 'operation') as eda:
            result=self.call('simulate', testbench_declarations='reg x;',
                             testbench_stimulus='check(1, "value", 2);', checks=checks)
        eda.assert_not_called()
        self.assertEqual(result.data['status'], 'tb_error')
        self.assertEqual(result.directive, Directive.RETURN_TO_MODEL)
        self.assertNotIn('simulate', self.runtime.candidates.current()['evidence'])
        with patch.dict(os.environ, {'SLANG_BIN':'test-slang'}), \
             patch('agent.agent_tool_registry.rtl_check.subprocess.run', return_value=subprocess.CompletedProcess([],0,b'')), \
             patch.object(self.runtime.eda, 'operation', return_value={'status':'passed','runs':[]}) as eda:
            edited=self.call('amend_test', path='tests/tb.sv', old='check(1, "value", 2);',
                             new='check(1, "value");', reason='correct check signature')
            passed=self.call('simulate')
        self.assertEqual(edited.data['precheck']['status'], 'passed')
        self.assertEqual(passed.data['status'], 'passed')
        eda.assert_called_once()

    def test_tb_precheck_timeout_is_not_design_failure(self):
        self.call('write_file', path='src/solution.sv', content=GOOD)
        self.call('write_file', path='tests/tb.sv', content='module tb; endmodule')
        self.call('set_contract', contract={'checks':[{'id':'value','statement':'Check output'}]})
        with patch.dict(os.environ, {'SLANG_BIN':'test-slang'}), \
             patch('agent.agent_tool_registry.rtl_check.subprocess.run', side_effect=subprocess.TimeoutExpired('slang',3)), \
             patch.object(self.runtime.eda, 'operation') as eda:
            result=self.call('simulate')
        eda.assert_not_called()
        self.assertEqual(result.data['status'], 'timeout')
        self.assertNotIn('simulate', self.runtime.candidates.current()['evidence'])
        self.assertIsNone(self.runtime.verification.pending_counterexample)

    def test_missing_tb_checker_still_uses_vivado(self):
        self.call('write_file', path='src/solution.sv', content=GOOD)
        with patch.dict(os.environ, {'SLANG_BIN':''}), \
             patch('agent.agent_tool_registry.rtl_check.shutil.which', return_value=None), \
             patch.object(self.runtime.eda, 'operation', return_value={'status':'passed','runs':[]}) as eda:
            result=self.call('simulate', testbench_content='module tb; endmodule',
                             checks=[{'id':'value','statement':'Check output'}])
        eda.assert_called_once()
        self.assertEqual(result.data['precheck']['status'], 'unavailable')
        self.assertEqual(result.data['status'], 'passed')

    def test_missing_checker_is_not_reported_as_passed(self):
        with patch.dict(os.environ, {'SLANG_BIN':''}), \
             patch('agent.agent_tool_registry.rtl_check.shutil.which',return_value=None):
            result=self.call('write_file',path='src/solution.sv',content=GOOD)
        self.assertEqual(result.directive,Directive.CONTINUE)
        self.assertEqual(result.data['precheck']['status'],'unavailable')
        self.assertFalse(result.data['precheck']['checked'])
        self.assertNotIn('slang',self.runtime.candidates.current()['evidence'])

    def test_timeout_is_feedback_without_false_compile_evidence(self):
        with patch.dict(os.environ, {'SLANG_BIN':'test-slang'}), \
             patch('agent.agent_tool_registry.rtl_check.subprocess.run',side_effect=subprocess.TimeoutExpired('slang',3)):
            result=self.call('write_file',path='src/solution.sv',content=GOOD)
        self.assertEqual(result.directive,Directive.RETURN_TO_MODEL)
        self.assertEqual(result.data['precheck']['status'],'timeout')
        self.assertFalse(result.data['precheck']['checked'])
        self.assertNotIn('slang',self.runtime.candidates.current()['evidence'])

    def test_batch_pauses_without_terminating_and_keeps_call_result_pairs(self):
        response=completion([{'tool':'write_file','args':{'path':'src/solution.sv','content':BAD}},
                             {'tool':'submit'},{'tool':'write_file','args':{'path':'notes/late.md','content':'late'}}])
        from agent.llm.protocol import parse_tool_actions
        parsed=parse_tool_actions(response['choices'][0])
        with patch.dict(os.environ, {'SLANG_BIN':'test-slang'}), \
             patch('agent.agent_tool_registry.rtl_check.subprocess.run',return_value=subprocess.CompletedProcess([],1,b'error: duplicate n')):
            batch=execute_tools(self.runtime,parsed,time.monotonic()+30,self.runtime.events.record,1)
        self.assertIsNone(batch.stop_reason)
        self.assertEqual(len(batch.observations),3)
        self.assertEqual(batch.observations[1]['status'],'not_executed')
        self.assertFalse((self.runtime.ws.root/'notes/late.md').exists())
        conversation=Conversation('task','',False)
        conversation.remember(parsed,batch.observations,batch.feedback)
        self.assertEqual(conversation.observation['precheck']['status'],'compile_error')
        history=conversation.history[0]
        self.assertEqual(len(history),4)
        self.assertEqual([call['id'] for call in history[0]['tool_calls']],
                         [message['tool_call_id'] for message in history[1:]])

    def test_model_receives_diagnostics_then_repairs_and_submits(self):
        task=Path(self.directory.name)/'task'
        out=Path(self.directory.name)/'solve'
        task.mkdir()
        (task/'prompt.txt').write_text('Implement module TopModule.')
        actions=[[{'tool':'write_file','args':{'path':'src/solution.sv','content':BAD}}, {'tool':'submit'}],
                 [{'tool':'write_file','args':{'path':'src/solution.sv','content':GOOD}}, {'tool':'submit'}]]
        requests=[]
        def respond(request,**_):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps(completion(actions.pop(0),str(len(requests)))).encode())
        with patch.dict(os.environ, {'SLANG_BIN':'test-slang','AGENT_STREAM':'0','DEADLINE_S':'30'}), \
             patch('agent.llm.tokens.TokenCounter.count_input',return_value=(1000,'test')), \
             patch('agent.llm.client.urllib.request.urlopen',side_effect=respond), \
             patch('agent.agent_tool_registry.rtl_check.subprocess.run',side_effect=[
                 subprocess.CompletedProcess([],1,b'error: duplicate n'),subprocess.CompletedProcess([],0,b'')]):
            solve(task,out)
        self.assertEqual(len(requests),2)
        last=json.loads(requests[1]['messages'][-1]['content'])['last_observation']
        self.assertEqual(last['precheck']['diagnostics'],'error: duplicate n')
        self.assertEqual((out/'solution.v').read_text(),GOOD)
        self.assertEqual(json.loads((out/'summary.json').read_text())['reason'],'submitted')

    @unittest.skipUnless(os.environ.get('TEST_SLANG_BIN') or os.environ.get('SLANG_BIN') or shutil.which('slang'), 'real slang not installed')
    def test_real_slang_checks_tb_ports_and_check_signature_before_vivado(self):
        executable=os.environ.get('TEST_SLANG_BIN') or os.environ.get('SLANG_BIN') or shutil.which('slang')
        self.call('write_file', path='src/solution.sv', content=GOOD)
        with patch.dict(os.environ, {'SLANG_BIN':executable}), \
             patch.object(self.runtime.eda, 'operation') as eda:
            bad=self.call('simulate', testbench_declarations='TopModule dut();',
                          testbench_stimulus='check(1, "value", 2);',
                          checks=[{'id':'value','statement':'Check output'}])
            eda.assert_not_called()
            self.assertEqual(bad.data['status'], 'tb_error')
            self.assertIn('tests/tb.sv', bad.data['precheck']['diagnostics'].replace('\\', '/'))
            fixed=self.call('amend_test', path='tests/tb.sv', old='check(1, "value", 2);',
                            new='check(1, "value");', reason='fix check arguments')
            self.assertEqual(fixed.data['precheck']['status'], 'passed')
            self.call('amend_test', path='tests/tb.sv', old='TopModule dut();',
                      new='TopModule dut(.missing(1\'b0));', reason='test port diagnostics')
            mismatch=self.call('simulate')
            self.assertEqual(mismatch.data['status'], 'tb_error')
            self.assertIn('missing', mismatch.data['precheck']['diagnostics'])
            eda.assert_not_called()

    @unittest.skipUnless(os.environ.get('TEST_SLANG_BIN') or os.environ.get('SLANG_BIN') or shutil.which('slang'), 'real slang not installed')
    def test_real_slang_rejects_duplicate_and_accepts_valid_rtl(self):
        executable=os.environ.get('TEST_SLANG_BIN') or os.environ.get('SLANG_BIN') or shutil.which('slang')
        with patch.dict(os.environ, {'SLANG_BIN':executable}):
            failed=self.call('write_file',path='src/solution.sv',content=BAD)
            passed=self.call('write_file',path='src/solution.sv',content=GOOD)
        self.assertEqual(failed.directive,Directive.RETURN_TO_MODEL)
        self.assertIn('redefinition',failed.data['precheck']['diagnostics'])
        self.assertIn('^',failed.data['precheck']['diagnostics'])
        self.assertEqual(passed.data['precheck']['status'],'passed')
        self.assertEqual(passed.directive,Directive.CONTINUE)
