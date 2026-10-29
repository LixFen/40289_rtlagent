"""幂等启动模型、GPU 遥测及 EDA 清理服务；全部就绪后返回。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from agent.paths import ROOT
from serve.health import health
from agent.config import load_settings, local_address
from serve.eda_cleanup import alive
from agent.processes import terminate


def models_ready():
    """核对推理服务已加载声明模型；不执行额外模型生成。"""
    try:
        settings = load_settings()
        local_address(settings.base_url)
        with urllib.request.urlopen(settings.base_url+'/models',timeout=2) as response:
            return settings.model_name in [item['id'] for item in json.load(response)['data']]
    except (OSError,ValueError,KeyError):
        return False


def spawn(name, command, root, started):
    """通过 setsid 语义启动常驻进程，PID 存在且存活时不重复启动。"""
    pid_file = root/(name+'.pid')
    if pid_file.exists():
        try:
            if alive(int(pid_file.read_text())):
                return
        except ValueError:
            pass
    with (root/(name+'.log')).open('ab') as log:
        proc = subprocess.Popen(command,cwd=ROOT,env=os.environ.copy(),stdout=log,stderr=log,start_new_session=True)
    pid_file.write_text(str(proc.pid),encoding='utf-8')
    started.append(proc)


def start_services():
    """准备本地目录，启动所需常驻进程，失败时只回收本次创建的进程。"""
    root = Path(os.environ['AGENT_TMP']).resolve()/'services'
    root.mkdir(parents=True,exist_ok=True)
    Path(os.environ['EDA_TMP']).mkdir(parents=True,exist_ok=True)
    settings = load_settings()
    os.environ.setdefault('MODEL_NAME', settings.model_name)
    os.environ.setdefault('LLM_BASE_URL', settings.base_url)
    os.environ.setdefault('GPU_TELEMETRY_FILE',str(Path(os.environ['EDA_TMP'])/'rtl-gpu.json'))
    started = []
    try:
        if not models_ready():
            default = 'serve/llamacpp.sh' if os.environ.get('LLAMA_SERVER') else 'serve/llamacpp-sdk10.sh'
            script = Path(os.environ.get('INFERENCE_START',str(ROOT/default))).resolve()
            spawn('model',['bash',str(script)],root,started)
        if not health()['ready']:
            spawn('telemetry',[sys.executable,'-m','serve.telemetry'],root,started)
        spawn('eda-cleanup',[sys.executable,'-m','serve.eda_cleanup'],root,started)
        end = time.monotonic()+float(os.environ.get('STARTUP_TIMEOUT_S','300'))
        while time.monotonic() < end:
            if any(proc.poll() is not None for proc in started):
                raise RuntimeError('A service exited during startup; inspect AGENT_TMP/services/*.log')
            if health()['ready']:
                return
            time.sleep(1)
        raise TimeoutError('Model/GPU telemetry did not become ready')
    except BaseException:
        for proc in started:
            terminate(proc.pid)
            proc.wait()
        raise


def main():
    """Linux 上用文件锁串行化启动，避免并发执行创建重复常驻进程。"""
    import fcntl
    root = Path(os.environ['AGENT_TMP'])/'services'
    root.mkdir(parents=True,exist_ok=True)
    with (root/'startup.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        start_services()


if __name__ == '__main__':
    main()
