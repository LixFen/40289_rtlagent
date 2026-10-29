import hashlib
import io
import json
from pathlib import Path
import platform
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.runtime_capsule import payload_path, restore


class RuntimeCapsuleTests(unittest.TestCase):
    def capsule(self, root, name='/opt/llamacpp/llama-server', content=b'validated-software'):
        bundle = root/'runtime.tar.gz'
        with tarfile.open(bundle, 'w:gz') as archive:
            info = tarfile.TarInfo(name.lstrip('/'))
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        manifest = {'machine':platform.machine(), 'os_release':'test', 'system_files':{},
                    'archive_sha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),
                    'files':{name:{'sha256':hashlib.sha256(content).hexdigest(),
                                   'bytes':len(content), 'mode':0o755}}}
        metadata = root/'runtime.json'
        metadata.write_text(json.dumps(manifest))
        return bundle, metadata

    def test_restore_is_offline_verified_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('tools.runtime_capsule.platform.freedesktop_os_release', return_value={'VERSION_ID':'test'}, create=True):
            root = Path(directory)
            bundle, metadata = self.capsule(root)
            prefix = root/'fresh-root'
            first = restore(bundle, metadata, prefix)
            self.assertEqual(first['restored_files'], 1)
            self.assertEqual((prefix/'opt/llamacpp/llama-server').read_bytes(), b'validated-software')
            second = restore(bundle, metadata, prefix)
            self.assertEqual(second['already_verified_files'], 1)
            self.assertEqual(second['restored_files'], 0)

    def test_corrupt_archive_and_existing_mismatch_are_not_installed(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('tools.runtime_capsule.platform.freedesktop_os_release', return_value={'VERSION_ID':'test'}, create=True):
            root = Path(directory)
            bundle, metadata = self.capsule(root)
            prefix = root/'fresh-root'
            target = prefix/'opt/llamacpp/llama-server'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'another-version')
            with self.assertRaisesRegex(RuntimeError, 'refusing to overwrite'):
                restore(bundle, metadata, prefix)
            self.assertEqual(target.read_bytes(), b'another-version')
            bundle.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'archive SHA-256'):
                restore(bundle, metadata, prefix)

    def test_private_configuration_and_path_traversal_cannot_be_restored(self):
        for name in ('/etc/shadow', '/opt/llamacpp/../../etc/shadow',
                     '/workspace/40289_rtlagent/serve/fpga-token.local.txt',
                     '/root/.local/state/rc-tunnel/frpc.toml'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                payload_path(name, Path('/fresh-root'))
