import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from agent.tooling.runtime import Runtime


class FileToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = Runtime('', '', Path(self.directory.name), time.monotonic()+30, lambda *_a, **_k:None)

    def tearDown(self):
        self.directory.cleanup()

    def call(self, name, **args):
        return self.runtime.dispatch({'tool':name, 'args':args})

    def test_read_window_and_write_metadata_preserve_unicode_character_semantics(self):
        text = '第一行\nsecond line\n'
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        written = self.call('write_file', path='notes/input.txt', content=text)
        self.assertEqual(written, {'path':'notes/input.txt', 'sha256':digest, 'bytes':len(text.encode('utf-8'))})
        read = self.call('read_file', path='notes/input.txt', offset=1, limit=2)
        self.assertEqual(read, {'path':'notes/input.txt', 'content':'一行', 'total_chars':len(text), 'sha256':digest})
        self.assertEqual(self.call('read_file', path='notes/input.txt', offset=len(text)+1)['content'], '')

    def test_tool_default_window_does_not_limit_shared_text_storage(self):
        text = 'x'*13000
        self.runtime.ws.write_text('notes/long.txt', text)
        self.assertEqual(self.runtime.ws.read_text('notes/long.txt'), text)
        result = self.call('read_file', path='notes/long.txt')
        self.assertEqual(len(result['content']), 12000)
        self.assertEqual(result['total_chars'], 13000)
        self.assertEqual(result['sha256'], hashlib.sha256(text.encode()).hexdigest())

    def test_range_validation_and_file_access_still_apply(self):
        self.call('write_file', path='notes/input.txt', content='content')
        for args in ({'offset':-1}, {'offset':True}, {'limit':0}, {'limit':16001}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.call('read_file', path='notes/input.txt', **args)
        for path in ('../secret.txt','/etc/passwd','notes/../../secret'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call('read_file', path=path)

    def test_patch_uses_same_write_result_and_cannot_write_reports(self):
        self.call('write_file', path='notes/input.txt', content='old 中文')
        patched = self.call('patch_file', path='notes/input.txt', old='old', new='new')
        self.assertEqual(patched['sha256'], hashlib.sha256('new 中文'.encode()).hexdigest())
        self.assertEqual(patched['bytes'], len('new 中文'.encode()))
        with self.assertRaises(ValueError):
            self.call('patch_file', path='reports/log.txt', old='old', new='new')

    def test_contract_tool_owns_result_format_and_preserves_immutable_contract(self):
        contract = {'checks':[{'id':'hold', 'statement':'保持输出'}]}
        result = self.call('set_contract', contract=contract)
        digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
        self.assertEqual(result, {'contract_sha256':digest, 'checks':['hold']})
        with self.assertRaises(ValueError):
            self.call('write_file', path='notes/contract.json', content='{}')
