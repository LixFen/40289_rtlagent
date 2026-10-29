"""Read-only validation inventory; does not print credentials or download weights."""
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

report = dict(platform=platform.platform(), python=sys.version, tools={})
for name in ('vllm', 'llama-server', 'rocminfo', 'rocm-smi', 'amd-smi', 'vivado', 'xvlog', 'xelab', 'xsim', 'docker'):
    report['tools'][name] = shutil.which(name)
report['packages'] = {}
for name in ('torch', 'vllm', 'transformers', 'triton', 'pytorch-triton-rocm'):
    try:
        report['packages'][name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        report['packages'][name] = None
report['rocm_versions'] = {}
for path in Path('/opt/rocm/.info').glob('version*'):
    if path.is_file():
        report['rocm_versions'][str(path)] = path.read_text(errors='replace')[:500]
report['gpu_sysfs'] = []
for path in Path('/sys/class/drm').glob('card[0-9]*/device'):
    if (path / 'mem_info_vram_total').exists():
        item = dict(card=path.parent.name)
        for field in ('vendor', 'device', 'mem_info_vram_total', 'mem_info_vram_used', 'gpu_busy_percent'):
            file = path / field
            if file.exists():
                item[field] = file.read_text().strip()
        report['gpu_sysfs'].append(item)
probe = "import torch,json; print(json.dumps(dict(version=torch.__version__,hip=torch.version.hip,available=torch.cuda.is_available(),devices=[dict(name=torch.cuda.get_device_name(i),properties=str(torch.cuda.get_device_properties(i))) for i in range(torch.cuda.device_count())])))"
try:
    result = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, timeout=40)
    report['torch_probe'] = dict(rc=result.returncode, stdout=result.stdout[-8000:], stderr=result.stderr[-2000:])
except subprocess.TimeoutExpired:
    report['torch_probe'] = dict(error='timeout')
print(json.dumps(report, indent=2))
