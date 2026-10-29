"""Detached submission API supervisor; restarts the API after a two-second delay."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from agent.paths import ROOT
from serve.credentials import load_token
from serve.eda_cleanup import alive


def state_directory():
    return Path(os.environ.get('AGENT_TMP', '/var/tmp/rtl-agent'))/'run_api'


def running(state):
    try:
        pid = int((state/'run_api.pid').read_text())
        return pid if alive(pid) else None
    except (OSError, ValueError):
        return None


def supervise(command, stopped, restart_delay=2):
    while not stopped.is_set():
        child = subprocess.Popen(command, cwd=ROOT)
        try:
            while child.poll() is None and not stopped.wait(0.1):
                pass
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
            code = child.wait()
        if not stopped.is_set():
            print('Submission API exited (rc='+str(code)+'); restarting in 2 seconds.', flush=True)
            stopped.wait(restart_delay)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('start', 'stop', 'status', '_loop'):
        raise SystemExit('Usage: python -m serve.watchdog start|stop|status|_loop')
    mode = sys.argv[1]
    state = state_directory()
    state.mkdir(parents=True, exist_ok=True)
    if mode == '_loop':
        import fcntl
        with (state/'run_api.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            load_token()
            stopped = threading.Event()
            previous = {}
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, lambda *_: stopped.set())
            pidfile = state/'run_api.pid'
            pidfile.write_text(str(os.getpid()))
            try:
                supervise([sys.executable, '-m', 'serve.api'], stopped)
            finally:
                pidfile.unlink(missing_ok=True)
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
        return
    pid = running(state)
    if mode == 'status':
        print('Submission API watchdog '+('running (pid '+str(pid)+')' if pid else 'not running'))
        raise SystemExit(0 if pid else 1)
    if mode == 'stop':
        if pid:
            os.kill(pid, signal.SIGTERM)
            for _ in range(120):
                if not running(state):
                    break
                time.sleep(0.1)
            if running(state):
                raise SystemExit('Submission API watchdog did not stop; inspect its log')
        return
    if pid:
        print('Submission API watchdog already running (pid '+str(pid)+')')
        return
    load_token()
    with (state/'serve_api.log').open('ab') as log:
        child = subprocess.Popen([sys.executable, '-m', 'serve.watchdog', '_loop'],
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=log, start_new_session=True)
    for _ in range(50):
        if running(state):
            print('Submission API watchdog started; log: '+str(state/'serve_api.log'))
            return
        if child.poll() is not None:
            break
        time.sleep(0.1)
    raise SystemExit('Submission API watchdog failed to start; inspect '+str(state/'serve_api.log'))


if __name__ == '__main__':
    main()
