"""Test the selected GGUF with ROCm SDK 10.0 libraries, preserving the live service."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path('/tmp/rtl-runtime')
BIN = ROOT / 'llama/llama-b11552'
LIB = ROOT / 'compat/root/usr/lib/x86_64-linux-gnu'
SITE = ROOT / 'framework-venv/lib/python3.14/site-packages'
OUT = ROOT / 'evidence/sdk10-llama'
OUT.mkdir(exist_ok=True)
paths = [LIB, BIN, SITE / '_rocm_sdk_core/lib', SITE / '_rocm_sdk_libraries/lib', SITE / '_rocm_sdk_device_gfx1100/lib']
command = [str(LIB / 'ld-linux-x86-64.so.2'), '--library-path', ':'.join(map(str, paths)), str(BIN / 'llama-server'),
           '--model', str(ROOT / 'model-27b/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf'), '--alias', 'qwen27b-sdk10',
           '--host', '127.0.0.1', '--port', '8001', '--device', 'ROCm0', '--n-gpu-layers', '99',
           '--ctx-size', '16384', '--parallel', '1', '--jinja', '--reasoning', 'off']
env = os.environ.copy()
env['HIP_VISIBLE_DEVICES'] = '0'
env.pop('GGML_BACKEND_PATH', None)
report = dict(command=command, sdk_version='10.0.0', success=False)
with (OUT / 'server.log').open('w') as log:
    process = subprocess.Popen(command, cwd=BIN, env=env, stdout=log, stderr=log, start_new_session=True)
    try:
        for _ in range(90):
            if process.poll() is not None:
                raise RuntimeError('Server exited: '+str(process.returncode))
            try:
                with urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2) as response:
                    if json.load(response).get('status') == 'ok':
                        break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise RuntimeError('Startup timed out')
        report['library_maps'] = [line for line in Path(f'/proc/{process.pid}/maps').read_text().splitlines()
                                  if any(name in line for name in ('libamdhip64', 'librocblas', 'libhipblas'))]
        if not any('_rocm_sdk_core' in line and 'libamdhip64' in line for line in report['library_maps']):
            raise RuntimeError('Unexpected HIP runtime source')
        payload = dict(model='qwen27b-sdk10', messages=[dict(role='user',content='Reply with exactly PASS.')],
                       temperature=0, top_p=1, max_tokens=16)
        request = urllib.request.Request('http://127.0.0.1:8001/v1/chat/completions', data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=60) as response:
            report['response'] = json.load(response)
        report['success'] = report['response']['choices'][0]['message']['content'].strip() == 'PASS'
    except Exception as exc:
        report['error'] = type(exc).__name__+': '+str(exc)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
(OUT / 'result.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2), flush=True)
