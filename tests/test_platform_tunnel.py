from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.platform_tunnel import ensure, platform_environment


class PlatformTunnelTests(unittest.TestCase):
    def test_ensure_preserves_active_tunnel_url(self):
        binary = Mock()
        binary.is_file.return_value = True
        result = Mock(returncode=0, stdout='URL: https://existing\nStatus: active\nFRPC running: true\n')
        with patch('tools.platform_tunnel.subprocess.run', return_value=result) as run:
            ensure(binary, {})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][1], 'status')

    def test_ensure_waits_for_pending_client_without_new_expose(self):
        binary = Mock()
        binary.is_file.return_value = True
        pending = Mock(returncode=0, stdout='Status: pending\nFRPC running: true\n')
        active = Mock(returncode=0, stdout='Status: active\nFRPC running: true\n')
        with patch('tools.platform_tunnel.subprocess.run', side_effect=[pending, active]) as run, \
             patch('tools.platform_tunnel.time.sleep'):
            ensure(binary, {})
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ['status', 'status'])
    def test_ssh_uses_only_required_notebook_fields_and_disables_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'notebook-environ'
            path.write_bytes(b'FRP_BROKER_URL=https://broker\0FRP_BROKER_TLS_SERVER_NAME=broker\0OTHER_SECRET=hidden\0')
            env = platform_environment({'HTTPS_PROXY':'http://proxy','ALL_PROXY':'http://proxy'}, path)
            self.assertEqual(env['FRP_BROKER_URL'], 'https://broker')
            self.assertEqual(env['FRP_BROKER_TLS_SERVER_NAME'], 'broker')
            self.assertNotIn('OTHER_SECRET', env)
            self.assertNotIn('HTTPS_PROXY', env)
            self.assertNotIn('ALL_PROXY', env)
            self.assertEqual(env['TMPDIR'], '/var/tmp')

    def test_existing_notebook_configuration_needs_no_proc_access(self):
        env = platform_environment({'FRP_BROKER_URL':'https://explicit','TMPDIR':'/custom'}, Path('/missing'))
        self.assertEqual(env['FRP_BROKER_URL'], 'https://explicit')
        self.assertEqual(env['TMPDIR'], '/custom')
