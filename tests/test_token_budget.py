import io
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.llm.client import ModelClient, output_limit
from agent.llm.tokens import TokenCounter, account_usage, estimate_input, estimate_text
from agent.loop.lifecycle import RunControl


class TokenBudgetTests(unittest.TestCase):
    def test_large_json_does_not_exhaust_actual_remaining_tokens(self):
        body = {'messages':[{'role':'user', 'content':'module example;\n'*3200}],
                'tools':[], 'max_tokens':65535}
        control = RunControl()
        control.total_tokens = 16703
        self.assertGreater(len(json.dumps(body).encode()), 48833)
        self.assertTrue(control.accept_request(body))
        self.assertEqual(body['max_tokens'], 65535)

    def test_cumulative_usage_does_not_limit_requests_or_output(self):
        with patch.dict(os.environ, {'AGENT_MAX_TOTAL_TOKENS':'65536'}):
            control = RunControl()
        control.account(1000000, 'usage')
        runtime = SimpleNamespace(tool_calls=0, max_tools=64)
        self.assertTrue(control.can_continue(runtime))
        body = {'messages':[], 'max_tokens':output_limit({}, 9999, 1600, input_tokens=300)}
        self.assertEqual(body['max_tokens'], 1300)
        self.assertTrue(control.accept_request(body))
        self.assertEqual(body['max_tokens'], 1300)
        self.assertEqual(control.total_tokens, 1000000)

    def test_context_window_still_limits_output_and_rejects_full_input(self):
        control = RunControl()
        self.assertEqual(output_limit({}, 9999, 1600, input_tokens=1599), 1)
        for input_tokens in (1600, 1601):
            body = {'max_tokens':output_limit({}, 9999, 1600, input_tokens=input_tokens)}
            self.assertFalse(control.accept_request(body))
            self.assertEqual(control.reason, 'context_budget')

    def test_unicode_estimate_ignores_json_escape_and_transport_fields(self):
        body = {'messages':[{'role':'user', 'content':'中文 RTL'}], 'tools':[]}
        count = estimate_input(body)
        decoded = json.loads(json.dumps(body, ensure_ascii=True))
        decoded.update(stream=True, max_tokens=65535, temperature=0, model='x'*10000)
        self.assertEqual(estimate_input(decoded), count)

    def test_native_counter_applies_template_including_tools(self):
        counter = TokenCounter('http://127.0.0.1:8000/v1')
        body = {'messages':[{'role':'user', 'content':'中文'}], 'tools':[{'name':'test'}],
                'chat_template_kwargs':{'enable_thinking':False}}
        with patch.object(counter, 'request', side_effect=[{'chat_template':'template'},
                          {'prompt':'rendered with tools'}, {'tokens':[1,2,3]}]) as request:
            self.assertEqual(counter.count_input(body), (3, 'llamacpp_template_tokenizer'))
        self.assertEqual(request.call_args_list[1].args, ('/apply-template', body))
        self.assertEqual(request.call_args_list[2].args[1]['content'], 'rendered with tools')

    def test_unsupported_tokenizer_uses_labelled_estimate(self):
        counter = TokenCounter('http://127.0.0.1:8000/v1')
        body = {'messages':[{'role':'user', 'content':'中文'}]}
        with patch.object(counter, 'request', side_effect=OSError('unsupported')) as request:
            self.assertEqual(counter.count_input(body), (estimate_input(body), 'estimated_chars_v1'))
            counter.count_input(body)
        self.assertEqual(request.call_count, 1)

    def test_missing_usage_ignores_response_metadata_and_sse(self):
        result = {'choices':[{'message':{'content':'OK', 'reasoning_content':'Plan',
                  'tool_calls':[{'function':{'name':'submit', 'arguments':'{}'}}]}}]}
        count, source = account_usage(result, 100)
        self.assertEqual(count, 100+estimate_text('OKPlansubmit{}'))
        self.assertEqual(source, 'estimated_missing_usage')
        result.update(id='x'*100000, timings={'unused':'y'*100000}, usage=None)
        self.assertEqual(account_usage(result, 100), (count, source))

    def test_partial_usage_and_actual_usage_remain_token_counts(self):
        result = {'choices':[{'message':{'content':'abcdef'}}], 'usage':{'prompt_tokens':20}}
        self.assertEqual(account_usage(result, 9999), (22, 'estimated_missing_usage'))
        result['usage']['completion_tokens'] = 5
        self.assertEqual(account_usage(result, 9999), (25, 'usage'))
        result['usage']['total_tokens'] = 26
        self.assertEqual(account_usage(result, 9999), (26, 'usage'))
        control = RunControl()
        control.account(26, 'usage')
        control.account(22, 'estimated_missing_usage')
        self.assertEqual((control.total_tokens, control.actual_tokens, control.estimated_tokens), (48,26,22))

    def test_missing_usage_is_labelled_in_client_trace(self):
        response = {'choices':[{'message':{'content':'OK'}}]}
        records = []
        model = ModelClient()
        model.input_tokens, model.input_token_source = 10, 'llamacpp_template_tokenizer'
        with patch('agent.llm.client.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())), \
             patch.object(model, 'streaming', False):
            _, count = model.complete({}, None, lambda tool, **fields:records.append(fields), 1, 10, 1)
        self.assertEqual(count, 11)
        self.assertEqual(records[-1]['token_accounting_source'], 'estimated_missing_usage')

    def test_client_sends_unicode_without_ascii_escape_expansion(self):
        response = {'choices':[{'message':{'content':'OK'}}], 'usage':{'total_tokens':12}}
        model = ModelClient()
        body = {'messages':[{'role':'user', 'content':'中文 RTL'}]}
        with patch('agent.llm.client.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())) as request, \
             patch.object(model, 'streaming', False):
            model.complete(body, None, lambda *_a, **_k:None, 1, 10, 1)
        wire = request.call_args.args[0].data.decode('utf-8')
        self.assertIn('中文', wire)
        self.assertNotIn('\\u4e2d', wire)
