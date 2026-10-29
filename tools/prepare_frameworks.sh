#!/usr/bin/env bash
# Independent of the running GGUF/llama.cpp process. Never changes system Python.
set -eo pipefail
ROOT=/tmp/rtl-runtime
UV="$ROOT/uv-package/bin/uv"
export UV_HTTP_TIMEOUT=60 UV_HTTP_RETRIES=2 UV_CONCURRENT_DOWNLOADS=16
if [ ! -x "$ROOT/framework-venv/bin/python" ]; then
  "$UV" venv --python /root/.local/share/uv/python/cpython-3.14-linux-x86_64-gnu/bin/python3.14 "$ROOT/framework-venv"
fi
"$UV" pip install --python "$ROOT/framework-venv/bin/python" \
  --index-url https://stable.repo.amd.com/rocm/whl-next/ \
  'torch[device-gfx1100]==2.12.0+rocm10.0.0' 'torchvision[device-gfx1100]==0.27.0+rocm10.0.0'
export UV_CONFIG_FILE=/tmp/rtl-agent-validation/tools/uv-vllm.toml
export UV_INDEX_STRATEGY=unsafe-best-match
export PYTHONPATH="$ROOT/framework-venv/lib/python3.14/site-packages/_rocm_sdk_core/share/amd_smi"
export FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE
"$UV" pip install --python "$ROOT/framework-venv/bin/python" \
  'flash-attn==2.8.3' 'amd-aiter==0.1.20.post1'
"$UV" pip install --python "$ROOT/framework-venv/bin/python" \
  'https://rocm.frameworks.amd.com/whl-multi-arch/vllm/vllm/vllm-0.27.1.dev5%2Brocm10.0.0.gf46a9dfe2.d20260826-cp314-cp314-linux_x86_64.whl'
# AMD documents upgrading tensorizer AFTER vLLM installation. The wheel metadata
# pins 2.10.1, so requesting 2.12.1 in the same resolver transaction is unsatisfiable.
"$UV" pip install --python "$ROOT/framework-venv/bin/python" 'tensorizer==2.12.1' 'torch==2.12.0+rocm10.0.0'
"$ROOT/framework-venv/bin/python" -c 'import torch,vllm; print("torch",torch.__version__,"HIP",torch.version.hip,"vllm",vllm.__version__); print(torch.cuda.get_device_name(0)); print((torch.ones(4,device="cuda")*2).cpu())'
