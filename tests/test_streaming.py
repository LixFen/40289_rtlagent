"""Received stream data survives interruption; partial actions never run."""
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.llm.streaming import read_stream
from agent.autonomous import solve
from tests.protocol import tool_delta


def event(delta=None, finish=None, usage=None):
    value = {'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}]}
    if usage:
        value['usage'] = usage
    return ('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode()


class StreamTests(unittest.TestCase):
    def read(self, source, limit=1000000):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'raw.sse'
            records = []
            def record(tool, **fields):
                records.append(dict(tool=tool, **fields))
            result, size = read_stream(source, path, record, 1, time.monotonic()+5, limit)
            return result, size, path.read_bytes(), records

    def test_reasoning_content_usage_and_raw_capture(self):
        data = (b': keepalive\n\n' + event({'reasoning': '分析'}) + event({'reasoning_content': 'more'})
                + event({'content': '{"tool":'}) + event({'content': '"submit"}'}, 'stop')
                + b'data: {"choices":[],"usage":{"total_tokens":42}}\n\n' + b'data: [DONE]\n\n')
        result, size, raw, records = self.read(io.BytesIO(data))
        self.assertEqual(raw, data)
        self.assertEqual(size, len(data))
        self.assertEqual(result['choices'][0]['message']['content'], '{"tool":"submit"}')
        self.assertEqual(result['choices'][0]['message']['reasoning'], '分析')
        self.assertEqual(result['choices'][0]['message']['reasoning_content'], 'more')
        self.assertEqual(result['usage']['total_tokens'], 42)
        self.assertTrue(records[-1]['completed'])
        self.assertEqual(sum(r['tool']=='llm_stream_event' for r in records), 6)

    def test_timeout_preserves_received_reasoning_and_raw_bytes(self):
        data = event({'reasoning': 'already received'})
        class Interrupted(io.BytesIO):
            def readline(self, size=-1):
                if self.tell() == len(data):
                    raise TimeoutError('interrupted')
                return super().readline(size)
        with tempfile.TemporaryDirectory() as directory:
            path, records = Path(directory)/'raw.sse', []
            with self.assertRaises(TimeoutError):
                read_stream(Interrupted(data), path,
                            lambda tool, **fields: records.append(dict(tool=tool, **fields)),
                            1, time.monotonic()+5, 10000)
            self.assertEqual(path.read_bytes(), data)
            self.assertFalse(records[-1]['completed'])
            self.assertEqual(records[-1]['reasoning_chars'], len('already received'))
            self.assertEqual(records[1]['event']['choices'][0]['delta']['reasoning'], 'already received')

    def test_truncated_decision_never_dispatches(self):
        decision = {'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': 'module TopModule; endmodule'}}
        data = event(tool_delta(decision), 'tool_calls')  # Missing [DONE].
        with tempfile.TemporaryDirectory() as directory:
            task, out = Path(directory)/'task', Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text('Implement module TopModule.')
            with patch.dict('os.environ', {'AGENT_STREAM':'1', 'DEADLINE_S':'10'}), \
                 patch('agent.llm.client.urllib.request.urlopen', side_effect=lambda *_a, **_kw: io.BytesIO(data)):
                solve(task, out)
            self.assertEqual((out/'solution.v').read_text(), '')
            summary = json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['tool_actions'], 0)
            self.assertEqual(summary['reason'], 'model_errors')
            self.assertEqual((out/'llm-0001.sse').read_bytes(), data)

    def test_complete_stream_dispatches_and_records_request_before_events(self):
        actions = [{'tool': 'write_file', 'args': {'path': 'src/solution.sv', 'content': 'module TopModule; endmodule'}},
                   {'tool': 'submit'}]
        with tempfile.TemporaryDirectory() as directory:
            task, out = Path(directory)/'task', Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text('Implement module TopModule.')
            def respond(request, **kwargs):
                body = json.loads(request.data)
                self.assertTrue(body['stream'])
                self.assertTrue(body['stream_options']['include_usage'])
                delta = tool_delta(actions.pop(0))
                return io.BytesIO(event({'reasoning': 'metadata only'}) + event(delta, 'tool_calls',
                    {'total_tokens':42}) + b'data: [DONE]\n\n')
            with patch.dict('os.environ', {'AGENT_STREAM':'1', 'DEADLINE_S':'10'}), \
                 patch('agent.llm.client.urllib.request.urlopen', side_effect=respond):
                solve(task, out)
            summary = json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['reason'], 'submitted')
            self.assertEqual(summary['tool_actions'], 2)
            rows = [json.loads(line) for line in (out/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
            tools = [row['tool'] for row in rows]
            self.assertLess(tools.index('llm_request'), tools.index('llm_stream_event'))
            self.assertEqual(next(row for row in rows if row['tool']=='llm')['response']['choices'][0]['message']['reasoning'], 'metadata only')

    def test_large_lines_and_byte_limit(self):
        data = event({'content':'x'*70000}, 'stop') + b'data: [DONE]\n\n'
        result, _, raw, _ = self.read(io.BytesIO(data))
        self.assertEqual(raw, data)
        self.assertEqual(len(result['choices'][0]['message']['content']), 70000)
        with self.assertRaisesRegex(ValueError, 'byte budget'):
            self.read(io.BytesIO(data), 500)

    def test_sse_framing_is_not_charged_as_tokens_when_usage_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            task, out = Path(directory)/'task', Path(directory)/'out'
            task.mkdir()
            (task/'prompt.txt').write_text('Implement module TopModule.')
            data = event({'role':'assistant'})*400 + event(tool_delta({'tool':'submit'}), 'tool_calls') + b'data: [DONE]\n\n'
            with patch.dict('os.environ', {'AGENT_STREAM':'1', 'DEADLINE_S':'10'}), \
                 patch('agent.llm.client.urllib.request.urlopen', return_value=io.BytesIO(data)):
                solve(task, out)
            summary = json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['reason'], 'submitted')
            self.assertLess(summary['total_tokens'], len(data))

