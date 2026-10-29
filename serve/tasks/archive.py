"""按请求保存产物，并在串行执行锁内核算档案容量。"""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile

ARCHIVE_BYTES = {}

@contextmanager
def request_directory():
    """分配随机请求目录，保留失败产物并累计本次归档大小。"""
    # Preserve HTTP artifacts, including failures. Never interpret task_id as a path.
    local = '/var/tmp/rtl-agent' if os.name == 'posix' else tempfile.gettempdir()
    default = Path(os.environ.get('AGENT_TMP',local))/'rtl-runs'
    root = Path(os.environ.get('RUN_ARCHIVE_DIR', str(default))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cap = int(os.environ.get('RUN_ARCHIVE_MB', '1024')) * 1024**2
    if root not in ARCHIVE_BYTES:
        ARCHIVE_BYTES[root] = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    if ARCHIVE_BYTES[root] >= cap:
        raise OSError('Run archive budget exhausted; provision a fresh archive directory')
    run = Path(tempfile.mkdtemp(prefix='request-', dir=root))
    try:
        yield str(run)
    finally:
        # Increment only this completed run, rather than rescanning all prior
        # artifacts on every request (quadratic and deadline-breaking on Windows).
        ARCHIVE_BYTES[root] += sum(p.stat().st_size for p in run.rglob('*') if p.is_file())
