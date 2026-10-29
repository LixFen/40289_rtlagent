import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from serve.credentials import load_token
from tools.freeze import inventory


class CredentialsTests(unittest.TestCase):
    def test_token_file_is_loaded_without_export_or_shell_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'token.txt'
            path.write_text('\ufeffliteral-$(secret)\r\n', encoding='utf-8')
            env = {'FPGACHINA_TOKEN_FILE':str(path)}
            load_token(env)
            self.assertEqual(env['FPGACHINA_TOKEN'], 'literal-$(secret)')

    def test_saved_file_overrides_old_environment_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'serve').mkdir()
            (root/'serve/fpga-token.local.txt').write_text('saved-token\n')
            env = {}
            load_token(env, root)
            self.assertEqual(env['FPGACHINA_TOKEN'], 'saved-token')
            env['FPGACHINA_TOKEN'] = 'existing-token'
            load_token(env, root)
            self.assertEqual(env['FPGACHINA_TOKEN'], 'saved-token')

    def test_environment_token_remains_supported_when_no_file_is_filled(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {'FPGACHINA_TOKEN':'existing-token'}
            load_token(env, Path(directory))
            self.assertEqual(env['FPGACHINA_TOKEN'], 'existing-token')

    def test_empty_missing_or_multiline_token_fails_without_leaking_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'token.txt'
            for content in (None, '', 'first-secret\nsecond-secret', 'Bearer secret'):
                if content is not None:
                    path.write_text(content)
                with self.assertRaisesRegex(RuntimeError, 'FPGACHINA_TOKEN') as caught:
                    load_token({'FPGACHINA_TOKEN_FILE':str(path)})
                self.assertNotIn('first-secret', str(caught.exception))

    def test_private_file_never_enters_source_freeze(self):
        self.assertNotIn('serve/fpga-token.local.txt', inventory())

    def test_api_and_health_tool_use_saved_file_without_token_environment(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory)/'token.txt'
            token.write_text('file-authentication-test-token')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            env = dict(os.environ, PORT=str(port), FPGACHINA_TOKEN_FILE=str(token),
                       GPU_TELEMETRY_FILE=str(Path(directory)/'missing-telemetry.json'))
            env.pop('FPGACHINA_TOKEN', None)
            with (Path(directory)/'api.log').open('wb') as log:
                child = subprocess.Popen([sys.executable, '-B', '-m', 'serve.api'],
                                         cwd=root, env=env, stdout=log, stderr=log)
            url = 'http://127.0.0.1:'+str(port)+'/v1/health'
            try:
                end = time.monotonic()+10
                while True:
                    try:
                        urllib.request.urlopen(url, timeout=0.2)
                    except urllib.error.HTTPError as exc:
                        self.assertEqual(exc.code, 401)
                        break
                    except OSError:
                        if child.poll() is not None or time.monotonic() >= end:
                            self.fail('API did not start using the pre-filled token file')
                        time.sleep(0.05)
                request = urllib.request.Request(url, headers={
                    'Authorization':'Bearer file-authentication-test-token'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                output = Path(directory)/'health.json'
                result = subprocess.run([sys.executable, '-B', 'tools/final_health.py', '--output', str(output)],
                                        cwd=root, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1)  # No GPU/model readiness is claimed.
                self.assertFalse(json.loads(output.read_text())['ready'])
                self.assertNotIn('file-authentication-test-token', result.stdout+result.stderr)
            finally:
                child.terminate()
                child.wait(timeout=10)
