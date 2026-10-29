"""Owned process groups, also recoverable by the outer request supervisor."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def enable_subreaper():
    """Linux 下接管本服务失去父进程的后代，避免交给不回收僵尸的 PID 1。"""
    if not sys_linux():
        return False
    import ctypes
    return ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0


def sys_linux():
    """仅 Linux 提供本实现使用的 prctl 和进程组回收行为。"""
    import sys
    return sys.platform.startswith('linux')


def reap_group(pgid, timeout=5):
    """只回收指定任务进程组的子进程，不抢占其他任务的退出状态。"""
    if os.name != 'posix':
        return
    end = time.monotonic()+timeout
    while True:
        try:
            pid, _ = os.waitpid(-pgid, os.WNOHANG)
        except ChildProcessError:
            return
        if pid:
            continue
        if time.monotonic() >= end:
            return
        time.sleep(0.05)


def terminate(pid):
    if os.name == 'posix':
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)


def register(out, pid, add):
    file = Path(out) / 'children.json'
    active = json.loads(file.read_text()) if file.exists() else []
    active = active + [pid] if add else [item for item in active if item != pid]
    temporary = file.with_suffix('.tmp')
    temporary.write_text(json.dumps(active), encoding='utf-8')
    temporary.replace(file)


def cleanup(out):
    file = Path(out) / 'children.json'
    if file.exists():
        deadline = time.monotonic()+5
        for pid in json.loads(file.read_text()):
            terminate(pid)
            reap_group(pid,max(0,deadline-time.monotonic()))
        file.write_text('[]', encoding='utf-8')
