"""后台求解：隔离公开输入、监督 worker、清理进程并读取保存结果。"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from agent.paths import ROOT
from agent.config import load_settings
from agent.trace import export_trace
from agent.processes import cleanup, terminate, reap_group
from serve.tasks.archive import request_directory

SLOT = threading.Lock()


def stop(proc, grace=5):
    """先终止 worker，再回收其进程组；工具进程由独立登记表清理。"""
    if os.name == 'posix':
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        terminate(proc.pid)
    else:
        # Fast worker termination; registered EDA process trees are cleaned separately.
        # taskkill /T on a worker blocked in HTTP can take seconds under Vivado load.
        proc.kill()
    proc.wait()
    reap_group(proc.pid)


def event_trace(tool, status):
    """生成监督器事件，所有退出分支使用相同的 JSONL 格式。"""
    return json.dumps(dict(ts=time.time(), tool=tool, status=status)) + '\n'


def acquire_slot(deadline, cancel):
    """等待本机唯一执行槽，排队时间也计入截止期限。"""
    while time.monotonic() < deadline and not (cancel and cancel.is_set()):
        if SLOT.acquire(timeout=min(0.1, max(0, deadline-time.monotonic()))):
            return True
    return False


def prepare_input(task, payload):
    """只写公开题面与接口；任务 ID 和 nonce 不参与文件路径。"""
    (task/'prompt.txt').write_text(payload['prompt'], encoding='utf-8')
    (task/'interface.txt').write_text(payload['interface'], encoding='utf-8')


def worker_command(mode):
    """选择操作系统对应的固定启动命令，不拼接题面为命令。"""
    if os.name == 'posix':
        return ['bash', str(ROOT/('run.sh' if mode == 'agent' else 'run_baseline.sh'))]
    if mode == 'agent':
        return [sys.executable, '-m', 'agent.main']
    return [sys.executable, str(ROOT/'baseline.py')]


def wait_for_worker(proc, deadline, duration, cancel):
    """等待退出或期限/取消信号，返回中断事件，正常退出返回空串。"""
    while True:
        left = deadline-time.monotonic()
        if left <= 0 or (cancel and cancel.is_set()):
            stop(proc, min(5, duration/10))
            status = 'canceled' if cancel and cancel.is_set() else 'timeout'
            return event_trace('budget', status)
        try:
            proc.wait(timeout=min(0.2, left))
            return ''
        except subprocess.TimeoutExpired:
            continue


def run_worker(payload, task, out, started, deadline, cancel):
    """在独立进程中运行求解器，并在任何退出路径回收其工具进程。"""
    env = os.environ.copy()
    settings = load_settings()
    env.update(LLM_BASE_URL=settings.base_url, MODEL_NAME=settings.model_name)
    env['PYTHONUTF8'] = '1'
    env['DEADLINE_S'] = str(max(0.01, deadline-time.monotonic()))
    args = worker_command(payload['mode']) + [str(task), str(out)]
    with (out/'worker.log').open('w', encoding='utf-8') as log:
        proc = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=log,
                                start_new_session=os.name == 'posix')
        try:
            interruption = wait_for_worker(proc, deadline, payload['deadline_s'], cancel)
        except BaseException:
            if proc.poll() is None:
                stop(proc)
            raise
        finally:
            cleanup(out)
            if os.name == 'posix':
                terminate(proc.pid)
                reap_group(proc.pid)
    (out/'supervisor.json').write_text(json.dumps(dict(worker_rc=proc.returncode,
        elapsed_s=time.monotonic()-started)), encoding='utf-8')
    return interruption


def valid_trace(content):
    """保留有效事件，将写到一半的记录替换为明确的中断标记。"""
    records = []
    for line in content.splitlines():
        try:
            json.loads(line)
            records.append(line + '\n')
        except ValueError:
            records.append(event_trace('worker', 'incomplete_trace_record'))
    return ''.join(records)


def collect_result(out, mode, interruption):
    """读取已保存的源码和 trace；模型超时也能交付此前保存的代码。"""
    if mode == 'agent':
        export_trace(out, interrupted=bool(interruption))
    trace_name = 'trace.merged.jsonl' if mode == 'agent' else 'trace.jsonl'
    result = dict(solution='', trace=interruption)
    source, trace = out/'solution.v', out/trace_name
    if source.exists():
        result['solution'] = source.read_text(encoding='utf-8', errors='replace')
    if trace.exists():
        result['trace'] = valid_trace(trace.read_text(encoding='utf-8', errors='replace'))
        if mode == 'baseline':
            result['trace'] += interruption
    return result


def execute(payload, started, cancel=None):
    """申请执行槽，准备隔离目录，监督 worker，最后读取可交付产物。"""
    deadline = started + payload['deadline_s'] - min(10.0, payload['deadline_s']/5)
    response = dict(task_id=payload['task_id'], solution='', trace='', elapsed_s=0.0)
    if not acquire_slot(deadline, cancel):
        response['trace'] = event_trace('budget', 'queue_timeout')
    else:
        try:
            with request_directory() as scratch:
                task, out = Path(scratch)/'task', Path(scratch)/'out'
                task.mkdir()
                out.mkdir()
                if time.monotonic() >= deadline:
                    response['trace'] = event_trace('budget', 'admission_timeout')
                else:
                    prepare_input(task, payload)
                    interruption = run_worker(payload, task, out, started, deadline, cancel)
                    response.update(collect_result(out, payload['mode'], interruption))
        finally:
            SLOT.release()
    response['elapsed_s'] = time.monotonic()-started
    return response
