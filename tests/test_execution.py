"""重构后的异常边界和退出归档回归。"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.autonomous import solve
from agent.llm.client import ModelClient
from agent.loop.tools import execute_tools
from agent.processes import cleanup
from tests.protocol import completion


class ExecutionTests(unittest.TestCase):
    def test_task_continues_after_exceeding_old_cumulative_limit(self):
        code='module TopModule; endmodule'
        responses=[completion({'tool':'write_file','args':{'path':'src/solution.sv','content':code}},
                              'call_1', usage={'total_tokens':200000}),
                   completion({'tool':'submit'}, 'call_2', usage={'total_tokens':50})]
        with tempfile.TemporaryDirectory() as directory:
            task,out=Path(directory)/'task',Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text('Implement module TopModule.')
            with patch.dict('os.environ',{'AGENT_STREAM':'0','DEADLINE_S':'10',
                                         'AGENT_MAX_TOTAL_TOKENS':'65536','AGENT_MAX_MODEL_CALLS':'2'}), \
                 patch('agent.llm.tokens.TokenCounter.count_input',return_value=(1000,'test')), \
                 patch('agent.llm.client.urllib.request.urlopen',
                       side_effect=[io.BytesIO(json.dumps(response).encode()) for response in responses]):
                solve(task,out)
            summary=json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['reason'],'submitted')
            self.assertEqual(summary['model_calls'],2)
            self.assertEqual(summary['total_tokens'],200050)
            self.assertEqual((out/'solution.v').read_text(),code)
            start=json.loads((out/'trace.jsonl').read_text(encoding='utf-8').splitlines()[0])
            self.assertIsNone(start['max_total_tokens'])

    def test_unexpected_tool_runner_error_finalizes_saved_candidate(self):
        code='module TopModule; endmodule'
        action={'tool':'write_file','args':{'path':'src/solution.sv','content':code}}
        with tempfile.TemporaryDirectory() as directory:
            task,out=Path(directory)/'task',Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text('Implement module TopModule.')
            def fail_after_write(*args,**kwargs):
                execute_tools(*args,**kwargs)
                raise RuntimeError('unexpected tool runner failure')
            with patch.dict('os.environ',{'AGENT_STREAM':'0','DEADLINE_S':'10'}), \
                 patch('agent.llm.client.urllib.request.urlopen',side_effect=lambda *_a,**_k:io.BytesIO(json.dumps(completion(action)).encode())), \
                 patch('agent.autonomous.execute_tools',side_effect=fail_after_write), \
                 patch('agent.loop.lifecycle.cleanup',wraps=cleanup) as clean:
                with self.assertRaisesRegex(RuntimeError,'unexpected tool runner'):
                    solve(task,out)
            self.assertEqual((out/'solution.v').read_text(),code)
            self.assertEqual(json.loads((out/'summary.json').read_text())['tool_actions'],1)
            self.assertTrue((out/'trace.md').exists())
            clean.assert_called_once_with(out)

    def test_truncated_response_is_accounted_but_not_dispatched(self):
        response=completion({'tool':'write_file','args':{'path':'src/solution.sv','content':'module TopModule; endmodule'}},
                            usage={'total_tokens':17})
        response['choices'][0]['finish_reason']='length'
        with tempfile.TemporaryDirectory() as directory:
            task,out=Path(directory)/'task',Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text('Implement module TopModule.')
            with patch.dict('os.environ',{'AGENT_STREAM':'0','DEADLINE_S':'10'}), \
                 patch('agent.llm.client.urllib.request.urlopen',side_effect=lambda *_a,**_k:io.BytesIO(json.dumps(response).encode())):
                solve(task,out)
            summary=json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['reason'],'model_errors')
            self.assertEqual(summary['total_tokens'],51)
            self.assertEqual(summary['tool_actions'],0)
            self.assertEqual((out/'solution.v').read_text(),'')

    def test_endpoint_validation_accepts_only_local_http_without_credentials(self):
        for url,expected in [('http://127.0.0.1:8000/v1',True),('http://localhost/v1',True),
                             ('http://[::1]/v1',True),('https://example.com/v1',False),
                             ('http://127.0.0.1.example.com/v1',False),('http://user:secret@localhost/v1',False)]:
            with self.subTest(url=url),patch.dict('os.environ',{'LLM_BASE_URL':url}):
                self.assertEqual(ModelClient().is_local(),expected)
