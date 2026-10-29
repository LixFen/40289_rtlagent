import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from serve.watchdog import supervise


class WatchdogTests(unittest.TestCase):
    def test_api_crash_restarts_and_stop_terminates_live_child(self):
        stopped = threading.Event()
        first = Mock()
        first.poll.return_value = 7
        first.wait.return_value = 7
        second = Mock()
        second.poll.return_value = None
        def launch(*args, **kwargs):
            if launch.count == 0:
                launch.count += 1
                return first
            stopped.set()
            return second
        launch.count = 0
        with patch('serve.watchdog.subprocess.Popen', side_effect=launch) as popen:
            supervise(['api-command'], stopped, restart_delay=0)
        self.assertEqual(popen.call_count, 2)
        second.terminate.assert_called_once()
        second.wait.assert_called_with()

    def test_unresponsive_child_is_killed_on_stop(self):
        stopped = threading.Event()
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('api', 10), 0]
        def launch(*args, **kwargs):
            stopped.set()
            return child
        with patch('serve.watchdog.subprocess.Popen', side_effect=launch):
            supervise(['api-command'], stopped)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux watchdog lifecycle uses flock and signals')
    def test_detached_api_restarts_after_kill_and_stops_without_restart(self):
        import socket
        import urllib.error
        import urllib.request
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)/'run_api'
            token = Path(directory)/'token.txt'
            token.write_text('watchdog-test-token')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            env = dict(os.environ, AGENT_TMP=directory, PORT=str(port), FPGACHINA_TOKEN_FILE=str(token))
            env.pop('FPGACHINA_TOKEN', None)
            command = [sys.executable, '-m', 'serve.watchdog']
            def unauthorized():
                try:
                    urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/v1/health', timeout=0.3)
                except urllib.error.HTTPError as exc:
                    return exc.code == 401
                except OSError:
                    pass
                return False
            def wait_for(predicate):
                end = time.monotonic()+10
                while time.monotonic() < end:
                    if predicate():
                        return
                    time.sleep(0.05)
                self.fail('Timed out waiting for watchdog lifecycle transition')
            try:
                subprocess.run(command+['start'], cwd=root, env=env, check=True, capture_output=True, timeout=10)
                wait_for(unauthorized)
                pid = int((state/'run_api.pid').read_text())
                # Linux procfs exposes the API child owned by the watchdog.
                children = Path('/proc/'+str(pid)+'/task/'+str(pid)+'/children')
                child = int(children.read_text().split()[0])
                os.kill(child, signal.SIGKILL)
                wait_for(lambda: bool(children.read_text().strip()) and str(child) not in children.read_text().split())
                wait_for(unauthorized)
                subprocess.run(command+['start'], cwd=root, env=env, check=True, capture_output=True, timeout=10)
                self.assertEqual(int((state/'run_api.pid').read_text()), pid)
            finally:
                subprocess.run(command+['stop'], cwd=root, env=env, check=True, capture_output=True, timeout=15)
            self.assertFalse((state/'run_api.pid').exists())
            self.assertFalse(unauthorized())
