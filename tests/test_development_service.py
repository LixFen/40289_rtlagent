"""Cloud development bridge preserves requests and keeps baseline model selection exact."""
import http.client
from http.server import ThreadingHTTPServer
import io
import json
import threading
import unittest
from unittest.mock import patch

from tools.serve_development import bridge_handler
from agent.llm.client import output_limit
from agent.llm.tokens import estimate_input


class OutputBudgetTest(unittest.TestCase):
    def test_production_default_and_cloud_context_reserve(self):
        messages = [{'role': 'user', 'content': '中文 RTL specification'}]
        self.assertEqual(output_limit(messages, 8192), 8192)
        reserved = estimate_input(messages)
        self.assertEqual(output_limit(messages, 131072, 131072), 131072 - reserved)
        self.assertEqual(output_limit(messages, 8192, 131072), 8192)
        self.assertEqual(output_limit(messages, 131072, reserved), 0)


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0),
            bridge_handler('https://example.invalid/api/v1', 'test-secret', 'selected'))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_common_generation_is_applied_to_all_upstream_requests(self):
        self.server.RequestHandlerClass=bridge_handler('https://example.invalid/api/v1','test-secret','selected',{'reasoning_effort':'low'})
        for extra in ({},{'reasoning_effort':'xhigh'}):
            payload=json.dumps(dict({'model':'selected','messages':[]},**extra)).encode()
            with patch('tools.serve_development.urllib.request.urlopen',return_value=io.BytesIO(b'{"choices":[]}')) as upstream:
                self.assertEqual(self.request('POST','/v1/chat/completions',payload)[0],200)
                self.assertEqual(json.loads(upstream.call_args.args[0].data)['reasoning_effort'],'low')

    def test_models_selects_only_real_requested_model(self):
        for advertised, expected in [(['other', 'selected'], ['selected']), (['other'], [])]:
            with self.subTest(advertised=advertised), patch('tools.serve_development.urllib.request.urlopen',
                return_value=io.BytesIO(json.dumps({'data': [{'id': name} for name in advertised]}).encode())) as upstream:
                status, body = self.request('GET', '/v1/models')
                self.assertEqual(status, 200)
                self.assertEqual([item['id'] for item in json.loads(body)['data']], expected)
                request = upstream.call_args.args[0]
                self.assertEqual(request.full_url, 'https://example.invalid/api/v1/models')
                self.assertEqual(request.get_header('Authorization'), 'Bearer test-secret')

    def test_generation_preserves_payload_and_response(self):
        payload = json.dumps({'model': 'selected', 'messages': [{'role': 'user', 'content': 'spec'}],
                              'temperature': 0, 'max_tokens': 8192}).encode()
        result = b'{"choices":[{"message":{"content":"OK"}}]}'
        with patch('tools.serve_development.urllib.request.urlopen', return_value=io.BytesIO(result)) as upstream:
            self.assertEqual(self.request('POST', '/v1/chat/completions', payload), (200, result))
            self.assertEqual(upstream.call_args.args[0].data, payload)

    def test_invalid_requests_and_errors_do_not_expose_secret(self):
        with patch('tools.serve_development.urllib.request.urlopen', side_effect=OSError('test-secret')) as upstream:
            self.assertEqual(self.request('GET', '/other')[0], 404)
            self.assertEqual(self.request('POST', '/v1/chat/completions', b'{"model":"other"}')[0], 400)
            self.assertEqual(self.request('POST', '/v1/chat/completions', b'[]')[0], 400)
            upstream.assert_not_called()
            status, body = self.request('GET', '/v1/models')
            self.assertEqual(status, 502)
            self.assertNotIn(b'test-secret', body)

    def test_stream_forwards_first_event_before_upstream_completes(self):
        first = b'data: {"choices":[{"delta":{"reasoning":"thinking"}}]}\n\n'
        rest = b'data: [DONE]\n\n'
        release = threading.Event()
        class SlowStream(io.BytesIO):
            headers = {'Content-Type': 'text/event-stream'}
            def readline(self, size=-1):
                if self.tell() >= len(first):
                    if not release.wait(3):
                        raise TimeoutError('test stream blocked')
                return super().readline(size)
        payload = json.dumps({'model': 'selected', 'stream': True, 'messages': []}).encode()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            with patch('tools.serve_development.urllib.request.urlopen', return_value=SlowStream(first+rest)) as upstream:
                connection.request('POST', '/v1/chat/completions', payload)
                response = connection.getresponse()
                self.assertEqual(response.getheader('Content-Type'), 'text/event-stream')
                self.assertEqual(response.readline(), first.splitlines(keepends=True)[0])
                self.assertFalse(release.is_set())
                release.set()
                self.assertEqual(response.read(), b'\n'+rest)
                self.assertEqual(upstream.call_args.args[0].data, payload)
        finally:
            release.set()
            connection.close()


if __name__ == '__main__':
    unittest.main()
