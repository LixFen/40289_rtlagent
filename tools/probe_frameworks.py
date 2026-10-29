"""Actual framework import and small GPU operation, with exact versions/maps."""
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import traceback

report = dict(python=sys.version)
for package in ('torch', 'torchvision', 'triton', 'rocm-sdk-core', 'rocm-sdk-libraries', 'rocm-sdk-device-gfx1100', 'vllm'):
    try:
        report[package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        report[package] = None
try:
    import torch
    report.update(torch_version=torch.__version__, hip_version=torch.version.hip, available=torch.cuda.is_available())
    if torch.cuda.is_available():
        report['gpu'] = torch.cuda.get_device_name(0)
        report['gpu_tensor_result'] = (torch.ones(4, device='cuda')*2).cpu().tolist()
    report['hip_library_maps'] = [line for line in Path('/proc/self/maps').read_text().splitlines() if 'libamdhip64' in line]
except Exception:
    report['torch_error'] = traceback.format_exc()
if report['vllm']:
    try:
        import vllm
        report['vllm_import_version'] = vllm.__version__
        import vllm._C
        report['vllm_native_extension_loaded'] = True
        from vllm.platforms import current_platform
        report['vllm_platform'] = type(current_platform).__name__
        report['vllm_is_rocm'] = current_platform.is_rocm()
    except Exception:
        report['vllm_error'] = traceback.format_exc()
encoded = json.dumps(report, indent=2)
if os.environ.get('FRAMEWORK_REPORT_PATH'):
    Path(os.environ['FRAMEWORK_REPORT_PATH']).write_text(encoded)
print(encoded, flush=True)
