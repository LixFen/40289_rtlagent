"""单题入口：只复制公开任务文本，在调用者进程组中运行 Agent。"""
import os
from pathlib import Path
import signal
import sys
import tempfile

from agent.autonomous import solve
from agent.processes import enable_subreaper


def main():
    task, out = Path(sys.argv[1]), Path(sys.argv[2])
    interface = task / "interface.txt"
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    enable_subreaper()
    os.environ.setdefault('DEADLINE_S','900')
    local = '/var/tmp/rtl-agent' if os.name == 'posix' else tempfile.gettempdir()
    root = Path(os.environ.get('AGENT_TMP',local))
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rtl-input-',dir=root) as staging:
        public = Path(staging)
        (public/'prompt.txt').write_text((task/'prompt.txt').read_text(encoding='utf-8'),encoding='utf-8')
        if interface.exists():
            (public/'interface.txt').write_text(interface.read_text(encoding='utf-8'),encoding='utf-8')
        solve(public,out)


if __name__ == "__main__":
    main()
