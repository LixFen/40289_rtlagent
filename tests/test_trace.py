"""Per-turn trace assembly includes interrupted replies and ordered aliases."""
import json
from pathlib import Path
import tempfile
import unittest

from agent.trace import assemble_sse, export_trace


def event(delta, finish=None):
    return ('data: '+json.dumps({'choices':[{'index':0, 'delta':delta, 'finish_reason':finish}]},
                               ensure_ascii=False)+'\n\n').encode()


class TraceTests(unittest.TestCase):
    def test_aliases_are_joined_in_order_and_partial_tail_is_preserved_as_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)/'llm-0002.sse'
            raw.write_bytes(event({'reasoning_content':'先'}) + event({'reasoning':'后'})
                            + event({'content':'{"tool":'}) + b'data: {"choices":')
            result = assemble_sse(raw)
            self.assertFalse(result['completed'])
            message = result['response']['choices'][0]['message']
            self.assertEqual(message['reasoning'], '先后')
            self.assertEqual(message['content'], '{"tool":')
            self.assertIsNone(result['response']['usage'])

    def test_hard_kill_recovery_uses_raw_sse_and_keeps_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = [{'tool':'agent_start'}, {'tool':'llm_request', 'call':1, 'request':{'model':'test'}},
                      {'tool':'llm_stream_event', 'call':1, 'event':{'ignored':'duplicate'}},
                      {'tool':'action', 'call':1, 'decision':{'tool':'write_file'}},
                      {'tool':'llm_request', 'call':2, 'request':{'model':'test'}}]
            (root/'trace.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in source)+'{"partial":')
            (root/'llm-0001.sse').write_bytes(event({'content':'{"tool":"write_file"}'}, 'stop')+b'data: [DONE]\n\n')
            (root/'llm-0002.sse').write_bytes(event({'reasoning':'still working'}))
            rows = export_trace(root, interrupted=True)
            turns = [r for r in rows if r['tool']=='llm']
            self.assertEqual(len(turns), 2)
            self.assertTrue(turns[0]['completed'])
            self.assertEqual(turns[1]['status'], 'interrupted')
            self.assertEqual(turns[1]['response']['choices'][0]['message']['reasoning'], 'still working')
            self.assertEqual(sum(r['tool']=='action' for r in rows), 1)
            self.assertNotIn('llm_stream_event', [r['tool'] for r in rows])
            self.assertEqual(rows[-1]['status'], 'timeout')
            readable = (root/'trace.md').read_text(encoding='utf-8')
            self.assertIn('still working', readable)
            self.assertIn('未收到正文', readable)
            saved = [json.loads(line) for line in (root/'trace.merged.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(saved, rows)

    def test_duplicate_reasoning_alias_in_same_event_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)/'raw.sse'
            raw.write_bytes(event({'reasoning':'same','reasoning_content':'same'}, 'stop')+b'data: [DONE]\n\n')
            result = assemble_sse(raw)
            self.assertTrue(result['completed'])
            self.assertEqual(result['response']['choices'][0]['message']['reasoning'], 'same')
