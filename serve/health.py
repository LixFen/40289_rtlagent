"""就绪检查：核对模型声明、推理配置与 GPU 实测遥测。"""
import json
import os
from pathlib import Path
import time
import urllib.request

from baseline import load_generation
from agent.config import load_settings, local_address

def declared_model():
    """读取提交清单中的模型声明，健康检查据此核对实际服务别名。"""
    return load_settings(environ={}).model_name


def health():
    model = ''
    ready, vram = False, None
    try:
        settings = load_settings()
        model = settings.model_name
        local_address(settings.base_url)
        url = settings.base_url + '/models'
        with urllib.request.urlopen(url, timeout=2) as result:
            ready = model in [item["id"] for item in json.load(result)["data"]]
        ready = ready and model == declared_model()
        effort = os.environ.get('AGENT_REASONING_EFFORT','')
        ready = ready and (not effort or effort == load_generation().get('reasoning_effort',''))
        # Telemetry is sampled separately on the GPU node; stale data is not readiness.
        telemetry = os.environ.get('GPU_TELEMETRY_FILE',str(Path(os.environ.get('EDA_TMP','/var/tmp/eda'))/'rtl-gpu.json'))
        measured = json.loads(Path(telemetry).read_text(encoding="utf-8"))
        vram = float(measured["vram_gb"])
        ready = ready and 0 < vram <= 32 and 0 <= time.time()-measured["ts"] < 15 and measured["device_count"] == 1
    except (OSError, KeyError, ValueError, TypeError):
        ready = False
    return dict(ready=ready, track="rtl", model=model, vram_gb=vram)
