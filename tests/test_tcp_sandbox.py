from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

from tools.tcp_sandbox import landlock_abi, restrict_tcp


class TcpSandboxTests(unittest.TestCase):
    def test_invalid_port_is_rejected_before_enforcement(self):
        with self.assertRaises(ValueError):
            restrict_tcp([65536])
        with self.assertRaises(ValueError):
            restrict_tcp([], (60000, 30000))

    def test_unsupported_platform_does_not_run_unrestricted(self):
        with patch('tools.tcp_sandbox.platform.system', return_value='unsupported'):
            with self.assertRaises(RuntimeError):
                landlock_abi()

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Landlock is Linux-only')
    def test_allowed_connection_and_denied_connection_inherit_across_exec(self):
        try:
            landlock_abi()
        except RuntimeError as exc:
            self.skipTest(str(exc))
        root = Path(__file__).resolve().parents[1]
        with socket.socket() as allowed, socket.socket() as denied:
            allowed.bind(('127.0.0.1', 0)); allowed.listen()
            denied.bind(('127.0.0.1', 0)); denied.listen()
            a, b = allowed.getsockname()[1], denied.getsockname()[1]
            code = '''import errno,json,socket,subprocess,sys
with socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=1):pass
def denied():
 try:socket.create_connection(('127.0.0.1',int(sys.argv[2])),timeout=1)
 except OSError as exc:
  assert exc.errno==errno.EACCES,exc
  return
 raise AssertionError('Unlisted port was allowed')
denied()
child="import socket,errno; s=socket.socket(); assert s.connect_ex(('127.0.0.1',"+sys.argv[2]+"))==errno.EACCES"
subprocess.run([sys.executable,'-c',child],check=True)
print('TCP_POLICY_AND_EXEC_INHERITANCE_PASSED')
'''
            result = subprocess.run([sys.executable, '-B', 'tools/tcp_sandbox.py', '--allow-tcp', str(a),
                                     '--', sys.executable, '-c', code, str(a), str(b)],
                                    cwd=root, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('TCP_POLICY_AND_EXEC_INHERITANCE_PASSED', result.stdout)
