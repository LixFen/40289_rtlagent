"""EDA 临时目录兜底清理：只处理本程序创建且没有活动所有者的目录。"""
import json
import os
from pathlib import Path
import shutil
import sys
import time


def alive(pid):
    """检查目录所有者是否仍在运行；不向未知进程发送终止信号。"""
    if os.name != 'posix':
        return True  # Windows 不使用 kill(pid, 0)，兜底清理仅在目标 Linux 环境启用。
    try:
        os.kill(pid,0)
        if sys.platform.startswith('linux'):
            # Container PID 1 may leave stopped services as unreaped zombies.
            state = Path('/proc')/str(pid)/'stat'
            if state.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                return False
        return True
    except (ProcessLookupError, FileNotFoundError):
        return False
    except PermissionError:
        return True


def sweep(root, age_s=7200):
    """依据整棵子树最新修改时间清理，活动任务和链接目录始终跳过。"""
    root = Path(root).resolve()
    if not root.exists():
        return
    for work in root.glob('rtl-eda-*'):
        if work.is_symlink() or not work.is_dir() or not work.resolve().is_relative_to(root):
            continue
        try:
            owner = work/'owner.json'
            if owner.exists() and alive(int(json.loads(owner.read_text())['pid'])):
                continue
            newest = max([work.stat().st_mtime]+[p.stat().st_mtime for p in work.rglob('*')])
            if time.time()-newest > age_s:
                shutil.rmtree(work)
        except (OSError,ValueError,KeyError):
            continue


if __name__ == '__main__':
    while True:
        sweep(os.environ['EDA_TMP'])
        time.sleep(600)
