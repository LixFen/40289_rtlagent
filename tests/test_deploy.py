import io
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.config import load_settings
from serve.deploy import prerequisites, verify_model, warmup


class DeployTests(unittest.TestCase):
    def test_verified_unchanged_model_does_not_read_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)/'model.gguf'
            cache = Path(directory)/'cache.json'
            model.write_bytes(b'weights')
            expected = hashlib.sha256(b'weights').hexdigest()
            verify_model(model, expected, cache)
            with patch('serve.deploy.hashlib.sha256', side_effect=AssertionError('Read weights again')):
                verify_model(model, expected, cache)

    @unittest.skipIf(os.name == 'nt', 'Windows ctime records creation, not Linux status changes')
    def test_same_size_edit_with_restored_mtime_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)/'model.gguf'
            cache = Path(directory)/'cache.json'
            model.write_bytes(b'weights')
            expected = hashlib.sha256(b'weights').hexdigest()
            verify_model(model, expected, cache)
            previous = model.stat()
            # Separate writes across filesystem timestamp ticks.
            time.sleep(0.02)
            model.write_bytes(b'changed')
            os.utime(model, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            with self.assertRaisesRegex(RuntimeError, 'SHA-256 mismatch'):
                verify_model(model, expected, cache)

    def test_force_and_broken_cache_both_hash_entire_file(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)/'model.gguf'
            cache = Path(directory)/'cache.json'
            model.write_bytes(b'weights')
            expected = hashlib.sha256(b'weights').hexdigest()
            verify_model(model, expected, cache)
            with patch('serve.deploy.hashlib.sha256', wraps=hashlib.sha256) as digest:
                verify_model(model, expected, cache, force=True)
                self.assertEqual(digest.call_count, 1)
                cache.write_text('broken')
                verify_model(model, expected, cache)
                self.assertEqual(digest.call_count, 2)

    def test_expected_hash_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)/'model.gguf'
            cache = Path(directory)/'cache.json'
            model.write_bytes(b'weights')
            verify_model(model, hashlib.sha256(b'weights').hexdigest(), cache)
            with self.assertRaisesRegex(RuntimeError, 'SHA-256 mismatch'):
                verify_model(model, '0'*64, cache)

    def test_missing_token_fails_before_starting_or_reading_model(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'FPGACHINA_TOKEN_FILE':str(Path(directory)/'missing-token')}, clear=True), \
             patch('serve.deploy.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'FPGACHINA_TOKEN'):
                prerequisites()
            run.assert_not_called()

    def test_warmup_requires_real_text_not_just_reasoning(self):
        response = io.BytesIO(json.dumps({'choices':[{'message':{
            'content':'', 'reasoning_content':'thinking'}}]}).encode())
        with patch('serve.deploy.urllib.request.urlopen', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'no text'):
                warmup(load_settings(environ={}))

    def test_warmup_uses_configured_model_and_small_output_budget(self):
        response = io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')
        settings = load_settings(environ={})
        with patch('serve.deploy.urllib.request.urlopen', return_value=response) as request:
            warmup(settings)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['model'], settings.model_name)
        self.assertEqual(payload['max_tokens'], 1024)
