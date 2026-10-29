"""Autonomous engineering tests; scripted decisions are not model accuracy evidence."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

from agent.main import solve
from agent.config import load_settings
from agent.loop.context import model_observation
from tests.protocol import completion
from agent.tooling.eda import canonical_lint, lint_rows
from agent.tooling.runtime import Runtime
from agent.tooling.workspace import Workspace, safe_hdl
from serve.api import SubmissionServer
from tests.http_client import solve_remote

PROMPT = '''Implement a module named TopModule.
 - input clk
 - input reset
 - input en
 - output q (4 bits)
At each rising clock edge, synchronous active-high reset sets q to zero.
Otherwise if en is high, increment q, saturating at 15. Otherwise hold q.
Reset has priority over en.'''
BAD = '''module TopModule(input clk, input reset, input en, output reg [3:0] q);
always @(posedge clk) begin
  if (reset) q <= 0;
  else if (en) q <= q + 1'b1;
end
endmodule
'''
GOOD = BAD.replace("else if (en)", "else if (en && q != 4'hf)")
CONTRACT = {'checks': [
    {'id': 'reset', 'statement': 'Reset wins even with enable high', 'source_quote': 'Reset has priority over en.'},
    {'id': 'hold', 'statement': 'Disabled cycles hold the value', 'source_quote': 'Otherwise hold q.'},
    {'id': 'saturation', 'statement': 'Increment through boundary and hold at 15', 'source_quote': 'saturating at 15.'}]}
TB = '''`timescale 1ns/1ps
module tb;
reg clk=0, reset=0, en=0;
wire [3:0] q;
integer count=0;
integer i;
string token;
TopModule dut(.clk(clk),.reset(reset),.en(en),.q(q));
always #5 clk=~clk;
task check(input integer expected);
begin
  #1;
  count=count+1;
  if(q !== expected[3:0]) begin
    $display("RTL_SELFTEST_FAIL check=%0d expected=%0d actual=%0d reset=%0d en=%0d",count,expected,q,reset,en);
    $fatal(1,"mismatch");
  end
end
endtask
initial begin
  if (!$value$plusargs("RTL_TOKEN_%s",token)) $fatal(1,"missing token");
  reset=1; en=1;
  @(posedge clk); check(0);
  $display("RTL_CHECK:reset");
  @(negedge clk); reset=0; en=0;
  @(posedge clk); check(0);
  $display("RTL_CHECK:hold");
  for(i=1;i<=18;i=i+1) begin
    @(negedge clk); en=1;
    @(posedge clk); check(i<15 ? i : 15);
  end
  $display("RTL_CHECK:saturation");
  @(negedge clk); en=0;
  @(posedge clk); check(15);
  @(negedge clk); reset=1; en=1;
  @(posedge clk); check(0);
  $display("RTL_SELFTEST_PASS token=%s checks=%0d",token,count);
  $finish;
end
initial begin #1000; $fatal(1,"watchdog"); end
endmodule
'''


class DecisionPeer(BaseHTTPRequestHandler):
    """Adaptive acceptance fixture, served over the real production HTTP transport."""
    requests = []
    observations = []
    stage = 0
    batch_size = 1

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        state = json.loads(body['messages'][-1]['content'])
        cls = type(self)
        cls.requests.append(body)
        cls.observations.append(state['last_observation'])
        script = [
            ('set_contract', {'contract': CONTRACT}),
            ('write_file', {'path': 'notes/plan.md', 'content': 'Check reset priority, hold and saturation; repair only from evidence.'}),
            ('write_file', {'path': 'src/solution.sv', 'content': BAD}),
            ('write_file', {'path': 'tests/tb.sv', 'content': TB}),
            ('compile', {}),
            ('simulate', {}),
        ]
        replies = []
        for _ in range(cls.batch_size):
            if cls.stage < len(script):
                tool, args = script[cls.stage]
            elif cls.stage == 6:
                observation = state['last_observation']
                if observation.get('status') != 'selftest_fail':
                    tool, args = 'submit', {}
                else:
                    tool, args = 'analyze_log', {'path': observation['runs'][-1]['log_path']}
            elif cls.stage == 7:
                tool, args = 'patch_file', {'path': 'src/solution.sv', 'old': 'else if (en)', 'new': "else if (en && q != 4'hf)"}
            elif cls.stage == 8:
                tool, args = 'simulate', {}
            elif cls.stage == 9:
                tool, args = 'lint', {}
            elif cls.stage == 10:
                tool, args = 'synth', {}
            else:
                tool, args = 'submit', {}
            cls.stage += 1
            replies.append({'tool':tool, 'args':args})
            if tool == 'submit':break
        data = json.dumps(completion(replies, f'call_{cls.stage}',
                           {'prompt_tokens':100, 'completion_tokens':100, 'total_tokens':200})).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.out = Path(self.directory.name)
        self.events = []
        self.runtime = Runtime(PROMPT, '', self.out, time.monotonic()+30,
                               lambda tool, **fields: self.events.append((tool, fields)))

    def tearDown(self):
        self.directory.cleanup()

    def action(self, tool, **args):
        return self.runtime.dispatch({'tool': tool, 'args': args})

    def test_traversal_absolute_and_report_writes_rejected(self):
        for name in ('../reference.sv', '/etc/passwd', 'C:/secret', 'src/../../secret', 'src\\x', 'reports/fake.log'):
            with self.assertRaises(ValueError):
                self.action('write_file', path=name, content='secret')

    def test_hdl_external_access_cannot_hide_behind_string_comments(self):
        for text in ('` include "secret"', '$fopen("secret")', 'import "DPI-C" function void a();',
                     '$display("//"); $system("echo secret");', '$display("/*"); $readmemh("secret",a);',
                     '`define HIDDEN sys``tem\n$`HIDDEN("secret");'):
            with self.assertRaises(ValueError):
                safe_hdl(text)
        safe_hdl(TB)

    def test_exact_patch_preserves_other_versions(self):
        self.action('write_file', path='src/solution.sv', content=BAD)
        self.action('patch_file', path='src/solution.sv', old='else if (en)', new="else if (en && q != 4'hf)")
        self.assertEqual(len(self.runtime.candidates.entries), 2)
        self.assertEqual(len(list((self.out/'candidates').glob('*/solution.sv'))), 2)
        with self.assertRaises(ValueError):
            self.action('patch_file', path='src/solution.sv', old='not present', new='x')

    def test_equal_evidence_selects_newest_candidate(self):
        self.action('write_file', path='src/solution.sv', content=BAD)
        first = self.runtime.candidates.current()
        self.action('write_file', path='src/solution.sv', content=GOOD)
        latest = self.runtime.candidates.current()
        self.assertEqual((self.out/'solution.v').read_text(), GOOD)
        for item in (first, latest):
            item['evidence']['simulate'] = {'status': 'passed'}
        self.assertIs(self.runtime.candidates.select_best(), latest)
        self.assertEqual((self.out/'solution.v').read_text(), GOOD)

    def test_newest_candidate_does_not_override_stronger_evidence(self):
        self.action('write_file', path='src/solution.sv', content=GOOD)
        first = self.runtime.candidates.current()
        first['evidence']['simulate'] = {'status': 'passed'}
        self.action('write_file', path='src/solution.sv', content=BAD)
        self.assertIs(self.runtime.candidates.select_best(), first)
        self.assertEqual((self.out/'solution.v').read_text(), GOOD)

    def test_contract_needs_statements_and_is_immutable_without_quotes(self):
        with self.assertRaises(ValueError):
            self.action('set_contract', contract={'checks': [{'id': 'empty', 'statement': ' '}]})
        checks = [{'id': item['id'], 'statement': item['statement']} for item in CONTRACT['checks']]
        self.action('set_contract', contract={'checks': checks})
        with self.assertRaises(ValueError):
            self.action('set_contract', contract=CONTRACT)
        with self.assertRaises(ValueError):
            self.action('write_file', path='notes/contract.json', content='{}')

    def test_test_amendment_invalidates_all_old_oracles(self):
        self.action('set_contract', contract=CONTRACT)
        self.action('write_file', path='src/solution.sv', content=BAD)
        self.action('write_file', path='tests/tb.sv', content=TB)
        self.runtime.candidates.current()['evidence']['simulate'] = {'status': 'passed'}
        self.runtime.ws.tests_locked = True
        self.runtime.verification.last_simulation = {'status': 'tb_error'}
        with self.assertRaises(ValueError):
            self.action('write_file', path='tests/tb.sv', content=TB)
        with self.assertRaises(ValueError):
            self.action('amend_test', path='tests/tb.sv', content=TB, reason=' ')
        self.action('amend_test', path='tests/tb.sv', content=TB+'\n', reason='correct sampling race')
        self.assertNotIn('simulate', self.runtime.candidates.current()['evidence'])
        self.assertTrue(self.runtime.ws.tests_locked)

    def test_simulation_registers_checks_without_separate_contract_turn(self):
        self.action('write_file', path='src/solution.sv', content=GOOD)
        checks = [{'id': item['id'], 'statement': item['statement']} for item in CONTRACT['checks']]
        with patch.object(self.runtime.eda, 'operation', return_value={'status': 'passed', 'checks': 22}) as eda:
            self.action('simulate', checks=checks)
        self.assertEqual(self.runtime.ws.contract, {'checks': checks})
        self.assertEqual(eda.call_args.args[3], {'checks': checks})
        with self.assertRaises(ValueError):
            self.action('set_contract', contract=CONTRACT)

    def test_inline_simulation_cannot_replace_existing_contract(self):
        self.action('set_contract', contract=CONTRACT)
        self.action('write_file', path='src/solution.sv', content=GOOD)
        with patch.object(self.runtime.eda, 'operation', return_value={'status': 'passed'}) as eda:
            self.action('simulate', checks=[{'id': 'weaker', 'statement': 'Ignore failures'}])
        self.assertEqual(eda.call_args.args[3], CONTRACT)

    def test_simulation_creates_tb_and_runs_without_extra_write_or_contract(self):
        saved = self.action('write_file', path='src/solution.sv', content=GOOD)
        self.assertEqual(saved['source_content'], GOOD)
        self.assertFalse(saved['source_truncated'])
        self.assertIn('Test this RTL now', saved['next_step'])
        checks = [{'id': item['id'], 'statement': item['statement']} for item in CONTRACT['checks']]
        def simulate(kind, args, source, contract):
            self.assertEqual(kind, 'simulate')
            self.assertEqual(self.runtime.ws.path('tests/tb.sv').read_text(), TB)
            self.assertEqual(contract, {'checks': checks})
            self.runtime.ws.tests_locked = True
            return {'status': 'passed', 'checks': 22}
        with patch.object(self.runtime.eda, 'operation', side_effect=simulate):
            result = self.action('simulate', testbench_content=TB, checks=checks)
        self.assertEqual(result['status'], 'passed')
        with self.assertRaises(ValueError):
            self.action('simulate', testbench_content=TB.replace('check(15)', 'check(0)'))

    def test_simulation_fragments_use_validated_contract_and_preserve_freeze(self):
        self.action('write_file', path='src/solution.sv', content=GOOD)
        checks=[{'id':'reset', 'statement':'Reset sets q to zero'}]
        declarations='reg clk=0; always #5 clk=~clk;'
        stimulus='@(posedge clk); #1; check(1, "reset");'
        def operation(kind, args, source, contract):
            self.assertEqual(contract, {'checks':checks})
            text=self.runtime.ws.path('tests/tb.sv').read_text()
            self.assertIn(declarations, text)
            self.assertIn(stimulus, text)
            self.assertIn('task automatic check', text)
            self.assertEqual(args['test_top'], 'tb')
            self.runtime.ws.tests_locked=True
            return {'status':'passed', 'checks':1}
        with patch.object(self.runtime.eda, 'operation', side_effect=operation):
            self.action('simulate', testbench_declarations=declarations, testbench_stimulus=stimulus, checks=checks)
        with self.assertRaises(ValueError):
            self.action('simulate', testbench_declarations=declarations, testbench_stimulus=stimulus)

    def test_simulation_rejects_ambiguous_full_and_fragment_input(self):
        self.action('write_file', path='src/solution.sv', content=GOOD)
        with self.assertRaisesRegex(ValueError, 'not both'):
            self.action('simulate', testbench_content=TB, testbench_declarations='reg x;', testbench_stimulus='x=0;')

    def test_selftest_failure_allows_test_correction_and_requires_revalidation(self):
        self.action('set_contract', contract=CONTRACT)
        self.action('write_file', path='src/solution.sv', content=GOOD)
        self.action('write_file', path='tests/tb.sv', content=TB)
        self.runtime.ws.tests_locked = True
        self.runtime.verification.last_simulation = {'status': 'selftest_fail'}
        self.runtime.verification.pending_counterexample = {'testbench_sha256': 'old-tb'}
        self.runtime.candidates.current()['evidence']['simulate'] = {'status': 'selftest_fail'}
        self.action('amend_test', path='tests/tb.sv', old='integer count=0;', new='integer count = 0;',
                    reason='correct testbench diagnostics')
        self.assertIsNone(self.runtime.verification.pending_counterexample)
        self.assertNotIn('simulate', self.runtime.candidates.current()['evidence'])
        self.assertTrue(any(tool == 'counterexample_invalidated' for tool, _ in self.events))
        with patch.object(self.runtime.eda, 'operation', return_value={'status': 'passed', 'runs': []}):
            self.assertEqual(self.action('simulate')['status'], 'passed')

    def test_passed_simulation_allows_full_test_revision(self):
        self.action('set_contract', contract=CONTRACT)
        self.action('write_file', path='src/solution.sv', content=GOOD)
        self.action('write_file', path='tests/tb.sv', content=TB)
        self.runtime.ws.tests_locked = True
        self.runtime.verification.last_simulation = {'status': 'passed'}
        self.runtime.candidates.current()['evidence']['simulate'] = {'status': 'passed'}
        self.action('amend_test', path='tests/tb.sv', content=TB+'\n', reason='add boundary coverage')
        self.assertEqual(self.runtime.ws.read_text('tests/tb.sv'), TB+'\n')
        self.assertNotIn('simulate', self.runtime.candidates.current()['evidence'])

    def test_tb_error_allows_only_unique_patch_and_preserves_lock(self):
        self.action('set_contract', contract=CONTRACT)
        self.action('write_file', path='tests/tb.sv', content=TB)
        self.runtime.ws.tests_locked=True
        self.runtime.verification.last_simulation={'status':'tb_error'}
        with self.assertRaises(ValueError):
            self.action('amend_test', path='tests/tb.sv', old='not present', new='', reason='correct syntax')
        self.assertTrue(self.runtime.ws.tests_locked)
        self.assertEqual(self.runtime.ws.path('tests/tb.sv').read_text(),TB)
        self.action('amend_test', path='tests/tb.sv', old='integer count=0;', new='integer count = 0;', reason='confirmed syntax repair')
        self.assertTrue(self.runtime.ws.tests_locked)
        self.assertIn('integer count = 0;',self.runtime.ws.path('tests/tb.sv').read_text())

    def test_tb_error_returns_source_and_repair_hint(self):
        self.action('write_file', path='src/solution.sv', content=GOOD)
        with patch.object(self.runtime.eda,'operation',return_value={'status':'tb_error','runs':[]}):
            result=self.action('simulate',testbench_content=TB,checks=CONTRACT['checks'])
        self.assertEqual(result['testbench_source'],TB)
        self.assertIn('amend_test',result['repair_hint'])

    def test_counterexample_and_environment_results_do_not_erase_good_candidate(self):
        self.action('write_file', path='src/solution.sv', content=GOOD)
        first = self.runtime.candidates.current()
        first['evidence']['simulate'] = {'status': 'passed'}
        self.action('write_file', path='src/solution.sv', content=BAD)
        with patch.object(self.runtime.eda, 'operation', return_value={'status': 'selftest_fail'}):
            self.action('simulate')
        with patch.object(self.runtime.eda, 'operation', return_value={'status': 'passed'}):
            self.action('simulate')
        self.assertEqual(self.runtime.candidates.current()['evidence']['simulate']['status'], 'selftest_fail')
        self.assertEqual((self.out/'solution.v').read_text(), GOOD)

    def test_local_docs_and_action_budget(self):
        result = self.action('search_docs', query='reset nonblocking')
        self.assertTrue(result['hits'])
        self.assertFalse(result['network'])
        self.runtime.max_tools = self.runtime.tool_calls
        self.assertTrue(self.action('list_files')['done'])

    def test_unavailable_vivado_is_not_success(self):
        self.action('write_file', path='src/solution.sv', content=GOOD)
        with patch.object(self.runtime.eda, 'executable', side_effect=FileNotFoundError()):
            result = self.action('compile')
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('compile', self.runtime.candidates.current()['evidence'])

    def test_headerless_lint_keeps_first_and_quoted_rows(self):
        file = self.out/'lint.csv'
        file.write_text('INFER-1,"Latch, inferred",q,N/A,TopModule,candidate.sv,4\nASSIGN-5 (T1),Unassigned,q,0,TopModule,candidate.sv,5\n')
        rows = lint_rows(file)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['rule_id'], 'INFER-1')
        self.assertEqual(rows[0]['description'], 'Latch, inferred')

    def test_pinned_ross_parser_handles_real_zero_report(self):
        file = self.out/'lint.rpt'
        file.write_text('RTL Linter Report\n2. Expanded\n+---------+-------------+----------+------+---------------+----------+-------------+\n| Rule ID | description | rtl_name | info | rtl_hierarchy | rtl_file | Line number |\n+---------+-------------+----------+------+---------------+----------+-------------+\n* create_waiver can be used to apply waiver to linter violations.\n** For example, create_waiver -type LINT -id RULE_ID.\n')
        canonical, parser = canonical_lint(file)
        self.assertEqual(lint_rows(canonical), [])
        self.assertEqual(parser, 'AMD-Ross-pinned-ASCII-to-CSV')


class DecisionLoopTests(unittest.TestCase):
    def test_diagnostic_cascade_is_compacted_without_losing_raw_observation(self):
        original={'status':'tb_error','runs':[{'tool':'xvlog','diagnostics':[{'message':str(i)} for i in range(20)],'excerpt':'x'*5000}]}
        compact=model_observation(original)
        self.assertEqual(len(compact['runs'][0]['diagnostics']),4)
        self.assertEqual(len(original['runs'][0]['diagnostics']),20)

    def test_model_inputs_describe_task_and_evidence(self):
        _,summary,calls=self.run_decisions([{'tool':'list_files'},{'tool':'submit'}])
        self.assertEqual(summary['reason'],'submitted')
        for request in calls:
            content=json.loads(request['messages'][-1]['content'])
            self.assertIn('current',content['state'])
            self.assertIn('files',content['state'])
            serialized=json.dumps(request['messages']).lower()
            for term in ('budget','remaining_s','generation_advice','model_calls_used','model_calls_limit',
                         'total_tokens_used','actions_used','actions_limit','eda_runs_used','eda_seconds_used'):
                self.assertNotIn(term,serialized)

    def test_internal_limits_still_return_saved_candidate(self):
        solution,summary,calls=self.run_decisions([[{'tool':'write_file','args':{'path':'src/solution.sv','content':GOOD}},
                                            {'tool':'read_file','args':{'path':'src/solution.sv'}},
                                            {'tool':'submit'}]], {'AGENT_MAX_ACTIONS':'1'})
        self.assertEqual(summary['reason'],'resource_budget')
        self.assertEqual(solution,GOOD)
        self.assertEqual(summary['tool_actions'],1)
        self.assertEqual(len(calls),1)

    def test_model_feedback_omits_controller_quota_error(self):
        original={'status':'tool_error','message':'Workspace file/byte budget exhausted'}
        self.assertNotIn('budget',model_observation(original)['message'])
        self.assertIn('budget',original['message'])

    def test_success_feedback_preserves_evidence_without_log_bulk(self):
        run={'tool':'xsim','status':'completed','rc':0,'log_path':'reports/run.log','diagnostics':[],'excerpt':'x'*5000}
        original={'status':'passed','checks':22,'covered':['reset'],'candidate_sha256':'candidate','runs':[run]}
        compact=model_observation(original)
        self.assertNotIn('excerpt',compact['runs'][0])
        self.assertEqual(compact['checks'],22)
        self.assertEqual(compact['covered'],['reset'])
        self.assertEqual(compact['runs'][0]['log_path'],'reports/run.log')
        self.assertEqual(len(original['runs'][0]['excerpt']),5000)
        failed=model_observation(dict(original,status='tb_error'))
        self.assertIn('excerpt',failed['runs'][0])

    def run_decisions(self, actions, updates=None):
        with tempfile.TemporaryDirectory() as directory:
            task, out = Path(directory)/'task', Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text(PROMPT)
            requests = []
            def respond(request, **_):
                requests.append(json.loads(request.data))
                action = actions.pop(0)
                if isinstance(action, Exception):
                    raise action
                return io.BytesIO(json.dumps(completion(action, f'call_{len(requests)}')).encode())
            with patch.dict(os.environ, dict({'DEADLINE_S': '10', 'AGENT_MAX_MODEL_CALLS': '12'}, **(updates or {}))), \
                 patch('agent.llm.client.urllib.request.urlopen', side_effect=respond):
                solve(task, out)
            return (out/'solution.v').read_text(), json.loads((out/'summary.json').read_text()), requests

    def test_more_than_three_decisions_and_recommended_thinking_sampling(self):
        actions = [{'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': GOOD}},
                   {'tool': 'check_interface'}, {'tool': 'list_files'},
                   {'tool': 'read_file', 'args': {'path': 'src/solution.sv'}}, {'tool': 'submit'}]
        solution, summary, calls = self.run_decisions(actions)
        self.assertEqual(solution, GOOD)
        self.assertEqual(summary['model_calls'], 5)
        self.assertEqual(summary['reason'], 'submitted')
        self.assertEqual(json.loads(calls[-1]['messages'][-1]['content'])['last_observation']['content'], GOOD)
        for call in calls:
            self.assertEqual((call['temperature'], call['top_p'], call['top_k']), (1.0, 0.95, 20))
            self.assertEqual((call['min_p'], call['presence_penalty'], call['repeat_penalty']), (0.0, 0.0, 1.0))
            self.assertGreater(call['max_tokens'], 0)
            self.assertLessEqual(call['max_tokens'], load_settings().max_output_tokens)
            self.assertEqual(call.get('reasoning_effort'), 'xhigh')

    def test_declared_reasoning_effort_takes_precedence_over_environment(self):
        _, summary, calls = self.run_decisions([{'tool': 'submit'}], {'AGENT_REASONING_EFFORT': 'low'})
        self.assertEqual(summary['reason'], 'submitted')
        self.assertEqual(calls[0]['reasoning_effort'], 'xhigh')
        _, _, calls = self.run_decisions([{'tool': 'submit'}], {'AGENT_REASONING_EFFORT': ''})
        self.assertEqual(calls[0]['reasoning_effort'], 'xhigh')

    def test_agent_and_baseline_share_declared_generation(self):
        from baseline import load_generation
        _,_,calls=self.run_decisions([{'tool':'submit'}])
        for key,value in load_generation().items():
            self.assertEqual(calls[0][key],value)

    def test_model_failure_retains_checkpoint(self):
        actions = [{'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': GOOD}}] + [OSError('offline failure')]*3
        solution, summary, _ = self.run_decisions(actions)
        self.assertEqual(solution, GOOD)
        self.assertEqual(summary['reason'], 'model_errors')

    def test_code_first_retains_unverified_candidate(self):
        solution, summary, calls = self.run_decisions([
            {'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': GOOD}}],
            {'AGENT_CODE_FIRST': '1', 'AGENT_MAX_MODEL_CALLS': '1'})
        self.assertEqual(solution, GOOD)
        self.assertEqual(summary['reason'], 'model_budget')
        self.assertNotIn('response_format', calls[0])
        self.assertIn('save a complete rtl candidate', calls[0]['messages'][0]['content'].lower())
        self.assertNotIn('preferably before writing RTL', calls[0]['messages'][0]['content'])
        self.assertTrue(calls[0]['tools'])
        self.assertEqual(calls[0]['tool_choice'], 'required')

    def test_code_first_guidance_stops_after_candidate_saved(self):
        solution, summary, calls = self.run_decisions([
            {'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': GOOD}},
            {'tool': 'submit'}], {'AGENT_CODE_FIRST': '1'})
        self.assertEqual(solution, GOOD)
        self.assertEqual(summary['reason'], 'submitted')
        self.assertIn('save a complete rtl candidate', calls[0]['messages'][0]['content'].lower())
        self.assertNotIn('save a complete rtl candidate', calls[1]['messages'][0]['content'].lower())
        self.assertTrue(json.loads(calls[1]['messages'][-1]['content'])['state']['current'])
        self.assertIn('testbench_declarations', calls[1]['messages'][0]['content'])

    def test_tool_call_retry_receives_previous_error(self):
        solution, summary, calls = self.run_decisions(['not a function call',
            {'tool':'write_file','args':{'path':'src/solution.sv','content':GOOD}}, {'tool':'submit'}])
        self.assertEqual(solution, GOOD)
        self.assertEqual(summary['reason'], 'submitted')
        self.assertEqual(json.loads(calls[1]['messages'][-1]['content'])['last_observation']['status'], 'model_error')
        self.assertIn('completed function call', calls[1]['messages'][-1]['content'])

    def test_text_content_never_dispatches(self):
        for text in (GOOD, json.dumps({'tool':'write_file','args':{'path':'src/solution.sv','content':GOOD}})):
            with self.subTest(text=text):
                solution, summary, _ = self.run_decisions([text]*3)
                self.assertEqual(solution, '')
                self.assertEqual(summary['tool_actions'], 0)
                self.assertEqual(summary['reason'], 'model_errors')

    def test_recent_reads_survive_a_second_file_read(self):
        _,summary,calls=self.run_decisions([
            {'tool':'write_file','args':{'path':'src/solution.sv','content':GOOD}},
            {'tool':'write_file','args':{'path':'notes/log.md','content':'independent diagnostic'}},
            {'tool':'read_file','args':{'path':'src/solution.sv'}},
            {'tool':'read_file','args':{'path':'notes/log.md'}}, {'tool':'submit'}])
        context=json.loads(calls[-1]['messages'][-1]['content'])
        self.assertEqual(summary['reason'],'submitted')
        self.assertEqual(context['last_observation']['content'],'independent diagnostic')
        observations = [json.loads(m['content']) for m in calls[-1]['messages'] if m['role']=='tool']
        self.assertTrue(any(x.get('path')=='src/solution.sv' and x.get('content')==GOOD for x in observations))

    def test_different_windows_of_same_file_remain_in_context(self):
        _,_,calls=self.run_decisions([
            {'tool':'write_file','args':{'path':'src/solution.sv','content':GOOD}},
            {'tool':'read_file','args':{'path':'src/solution.sv','offset':0,'limit':40}},
            {'tool':'read_file','args':{'path':'src/solution.sv','offset':40,'limit':40}}, {'tool':'submit'}])
        context=json.loads(calls[-1]['messages'][-1]['content'])
        self.assertEqual(context['last_observation']['content'],GOOD[40:80])
        observations = [json.loads(m['content']) for m in calls[-1]['messages'] if m['role']=='tool']
        self.assertTrue(any(x.get('content')==GOOD[:40] for x in observations))

    def test_input_budget_trims_complete_history_pairs(self):
        # Long read observations force trimming regardless of small prompt wording changes.
        code = GOOD+'\n'+'// padding for context regression\n'*200
        actions = [{'tool':'write_file','args':{'path':'src/solution.sv','content':code}}]
        actions += [{'tool':'read_file','args':{'path':'src/solution.sv','offset':n,'limit':16000}} for n in (0,40,80,120,160)]
        actions.append({'tool':'submit'})
        _, _, reference_calls = self.run_decisions(list(actions))
        limit=max(len(json.dumps(request,ensure_ascii=False)) for request in reference_calls[:2])
        _, summary, calls = self.run_decisions(actions, {'AGENT_INPUT_CHAR_LIMIT':str(limit)})
        self.assertEqual(summary['reason'], 'submitted')
        self.assertTrue(any(len(actual['messages']) < len(reference['messages'])
                            for actual, reference in zip(calls, reference_calls)))
        for request in calls:
            self.assertLessEqual(len(json.dumps(request, ensure_ascii=False)), limit)
            history = request['messages'][2:-1]
            self.assertEqual(len(history)%2, 0)
            for i in range(0,len(history),2):
                self.assertEqual(history[i]['role'],'assistant')
                self.assertEqual(history[i+1]['role'],'tool')
                self.assertEqual(history[i]['tool_calls'][0]['id'], history[i+1]['tool_call_id'])

    def test_budget_exhaustion_retains_checkpoint(self):
        solution, summary, _ = self.run_decisions([
            {'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': GOOD}}],
            {'AGENT_MAX_MODEL_CALLS': '1'})
        self.assertEqual(solution, GOOD)
        self.assertEqual(summary['reason'], 'model_budget')


def real_vivado_smoke(out, vivado, batch_size=1):
    """One real HTTP request with scripted decisions, actual tools and behavioral repair."""
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    DecisionPeer.stage, DecisionPeer.requests, DecisionPeer.observations = 0, [], []
    DecisionPeer.batch_size = batch_size
    model = ThreadingHTTPServer(('127.0.0.1', 0), DecisionPeer)
    api = SubmissionServer(('127.0.0.1', 0))
    for server in (model, api):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = {'XILINX_VIVADO': str(Path(vivado).resolve()),
                   'LLM_BASE_URL': f'http://127.0.0.1:{model.server_port}/v1',
                   'MODEL_NAME': 'scripted-acceptance-peer', 'FPGACHINA_TOKEN': 'local-smoke-only',
                   'RUN_ARCHIVE_DIR': str(out/'runs'), 'AGENT_TOOL_SECONDS': '260',
                   'AGENT_TOOL_TIMEOUT_S': '70', 'AGENT_ARTIFACT_MB': '128'}
    started = time.monotonic()
    try:
        with patch.dict(os.environ, environment):
            body = solve_remote(f'http://127.0.0.1:{api.server_port}',
                dict(task_id='fresh-saturating-counter',nonce='local',mode='agent',prompt=PROMPT,interface='',deadline_s=300),
                'local-smoke-only',poll_s=0.01)
        elapsed = time.monotonic()-started
        (out/'response.json').write_text(json.dumps(body, indent=2), encoding='utf-8')
        trace = [json.loads(line) for line in body['trace'].splitlines()]
        actions = [row for row in trace if row['tool'] == 'action']
        simulations = [row['observation'] for row in actions if row['decision']['tool'] == 'simulate']
        final = trace[-1]
        batches = {}
        for action in actions:
            batches[action['call']] = batches.get(action['call'],0)+1
        report = {'fixture': 'scripted model decisions, real Vivado tools; not LLM accuracy',
                  'elapsed_s': elapsed, 'deadline_s': 300, 'model_calls': len(DecisionPeer.requests),
                  'tools_per_turn':list(batches.values()),
                  'simulation_statuses': [item.get('status') for item in simulations],
                  'selected_code_matches_repair': body['solution'] == GOOD,
                  'final_evidence': final.get('evidence', {}), 'run_root': str(out/'runs')}
        report['passed'] = (elapsed < 300 and body['solution'] == GOOD and
            any(size==batch_size for size in batches.values()) and
            report['simulation_statuses'] == ['selftest_fail', 'passed'] and
            all(report['final_evidence'].get(stage, {}).get('status') == 'passed' for stage in ('simulate', 'lint', 'synth')))
        (out/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
    finally:
        for server in (api, model):
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
