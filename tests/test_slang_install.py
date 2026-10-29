import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.install_slang import install


class SlangInstallTests(unittest.TestCase):
    def test_verified_archive_installs_expected_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            content=b'test binary payload'
            platform='windows' if os.name=='nt' else 'linux'
            name='slang.exe' if platform=='windows' else 'slang'
            archive=root/('package.zip' if platform=='windows' else 'package.tar.gz')
            if platform=='windows':
                with zipfile.ZipFile(archive,'w') as package:
                    package.writestr(name,content)
            else:
                with tarfile.open(archive,'w:gz') as package:
                    info=tarfile.TarInfo(name); info.size=len(content)
                    package.addfile(info,io.BytesIO(content))
            release={'assets':{platform:{'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}}}
            with patch('tools.install_slang.Path.read_text',return_value=json.dumps(release)):
                target=install(archive,root/'installed')
            self.assertEqual(target,root/'installed'/'bin'/name)
            self.assertEqual(target.read_bytes(),content)
            if os.name=='posix':
                self.assertTrue(os.access(target,os.X_OK))

    def test_unverified_archive_is_rejected_before_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); archive=root/'invalid.tar.gz'
            archive.write_bytes(b'corrupted payload')
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                install(archive,root/'installed')
            self.assertFalse((root/'installed').exists())

    def test_docker_installs_the_pinned_offline_linux_asset(self):
        root=Path(__file__).resolve().parents[1]
        release=json.loads((root/'serve/slang-release.json').read_text())
        dockerfile=(root/'Dockerfile').read_text()
        asset=release['assets']['linux']
        archive=root/'vendor/slang-linux-v12.0.tar.gz'
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),asset['sha256'])
        self.assertIn('COPY vendor/slang-linux-v12.0.tar.gz /slang-build/slang.tar.gz',dockerfile)
        self.assertNotIn('SLANG_ARCHIVE_URL',dockerfile)
        self.assertIn('python3 tools/install_slang.py slang.tar.gz --prefix /opt/slang/12.0',dockerfile)
        self.assertIn('COPY --from=slang-build /opt/slang/12.0 /opt/slang/12.0',dockerfile)
