import tempfile
import unittest
import json
import hashlib
from pathlib import Path
from unittest.mock import patch, Mock

from agent.tooling.workspace import Workspace
from tools.build_image import source_checks
from serve.image_check import check
from serve.prepare_image import prepare


class ImagePreparationTests(unittest.TestCase):
    def test_image_records_immutable_base_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'serve').mkdir()
            (root/'baseline.py').write_bytes(b'pass\n')
            (root/'manifest.json').write_text('{}')
            (root/'serve/runtime.json').write_text(json.dumps({'baseline_source': {
                'canonical_sha256': {'baseline.py': hashlib.sha256(b'pass\n').hexdigest()}}}))
            with self.assertRaisesRegex(ValueError, 'immutable'):
                prepare(root, 'example:latest')
            image = 'example@sha256:'+'a'*64
            prepare(root, image)
            self.assertEqual(json.loads((root/'serve/runtime.json').read_text())['base_image'], image)
            self.assertEqual(json.loads((root/'manifest.json').read_text()), {})

    def test_registered_storage_api_preserves_existing_methods(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            text = 'module TopModule; endmodule'
            workspace.write_text('src/solution.sv', text)
            self.assertEqual(workspace.read_text('src/solution.sv'), text)
            self.assertEqual(workspace.read('src/solution.sv')['content'], text)
            with self.assertRaises(ValueError):
                workspace.write_text('../escape.sv', text)
            workspace.set_contract({'checks': [{'id': 'smoke', 'statement': 'matches public specification'}]})
            with self.assertRaisesRegex(ValueError, 'immutable'):
                workspace.write_text('notes/contract.json', '{}')

    def test_source_inputs_are_consistent(self):
        checks = source_checks()
        self.assertTrue(all(checks.values()), checks)

    def test_missing_shared_libraries_fail_software_check(self):
        result = Mock(returncode=0, stdout='libamdhip64.so => not found\n', stderr='')
        with patch('serve.image_check.shutil.which', return_value='/opt/bin/tool'), \
             patch('serve.image_check.os.access', return_value=True), \
             patch('serve.image_check.subprocess.run', return_value=result):
            report = check()
        self.assertFalse(report['checks']['llama-server_libraries'])
        self.assertFalse(report['checks']['slang_libraries'])
        self.assertTrue(report['checks']['workspace_api'])
