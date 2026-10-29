"""Public runtime defaults shared by host and image entry points; no credentials."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import sysconfig

from agent.paths import ROOT
from agent.config import load_runtime


def assigned_cards(dev_root=Path('/dev/dri'), sys_root=Path('/sys/class/drm')):
    cards = []
    for node in dev_root.glob('card[0-9]*'):
        if not re.fullmatch(r'card\d+', node.name):
            continue
        device = sys_root/node.name/'device'
        try:
            if (device/'vendor').read_text().strip() == '0x1002' and (device/'mem_info_vram_used').exists():
                cards.append(node.name)
        except OSError:
            continue
    return sorted(cards)


def runtime_environment(environ=None, root=ROOT, dev_root=Path('/dev/dri'), sys_root=Path('/sys/class/drm')):
    env = os.environ if environ is None else environ
    root = Path(root)
    model_path = env.get('MODEL_PATH')
    if not model_path:
        manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        filename = manifest['model'].get('file')
        if filename is None:
            filename = load_runtime(root)['model']['file']
        model_path = str(Path('/models')/filename)
    agent_tmp = env.get('AGENT_TMP') or '/var/tmp/rtl-agent'
    eda_tmp = env.get('EDA_TMP') or '/var/tmp/eda'
    defaults = {
        'MODEL_PATH':model_path,
        'LLAMA_SERVER':shutil.which('llama-server', path=env.get('PATH','')) or '/opt/llamacpp/llama-server',
        'SLANG_BIN':shutil.which('slang', path=env.get('PATH','')) or '/opt/slang/12.0/bin/slang',
        'INFERENCE_START':(root/'serve/llamacpp.sh').as_posix(),
        'EDA_TMP':eda_tmp, 'AGENT_TMP':agent_tmp,
        'RUN_ARCHIVE_DIR':str(Path(agent_tmp)/'rtl-runs'),
        'RUN_ARCHIVE_MB':'8192',
        'GPU_TELEMETRY_FILE':str(Path(eda_tmp)/'rtl-gpu.json'),
        'PORT':'7860', 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1',
    }
    version = load_runtime(root).get('final_target', {}).get('vivado')
    if not env.get('XILINX_VIVADO') and version:
        vivado = Path(env.get('XILINX_ROOT', '/tools/Xilinx'))/version/'Vivado'
        if (vivado/'bin/vivado').is_file():
            defaults['XILINX_VIVADO'] = str(vivado)
    sdk = Path(sysconfig.get_path('purelib'))/'_rocm_sdk_devel/lib'
    if sdk.is_dir():
        defaults['LD_LIBRARY_PATH'] = str(sdk)
    tensile = Path('/opt/llamacpp/rocblas/library')
    if tensile.is_dir():
        defaults['ROCBLAS_TENSILE_LIBPATH'] = str(tensile)
    cards = assigned_cards(dev_root, sys_root)
    if env.get('GPU_DRM_CARD'):
        defaults['GPU_DRM_CARD'] = env['GPU_DRM_CARD']
    elif len(cards) == 1:
        defaults['GPU_DRM_CARD'] = cards[0]
    defaults['GPU_ASSIGNED_COUNT'] = str(len(cards))
    # Preserve explicit deployment values, including the base image's library paths.
    # Only these public keys are projected; tokens, API keys and licenses are never printed.
    return {key:env.get(key) or value for key,value in defaults.items()}


def main():
    values = runtime_environment()
    if sys.argv[1:] == ['--shell']:
        for key,value in values.items():
            print('export '+key+'='+shlex.quote(value))
    elif not sys.argv[1:]:
        print(json.dumps(values, indent=2))
    else:
        raise SystemExit('Usage: python -m serve.environment [--shell]')


if __name__ == '__main__':
    main()
