#!/usr/bin/env bash
set -eo pipefail
ROOT=/tmp/rtl-runtime
UV="$ROOT/uv-package/bin/uv"
export UV_CONFIG_FILE=/tmp/rtl-agent-validation/tools/uv-vllm.toml
export UV_INDEX_STRATEGY=unsafe-best-match
export UV_HTTP_TIMEOUT=60 UV_HTTP_RETRIES=2
export PYTHONPATH="$ROOT/framework-venv/lib/python3.14/site-packages/_rocm_sdk_core/share/amd_smi"
export FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE
# Binary payloads are local; AMD's custom torchvision still needs online index
# metadata, so this is cache-assisted installation, not clean offline reproduction.
"$UV" pip install --python "$ROOT/framework-venv/bin/python" \
  --find-links "$ROOT/offline-wheels" --index https://stable.repo.amd.com/rocm/whl-next/ \
  "$ROOT/flydsl-0.3.1-cp314-cp314-manylinux_2_27_x86_64.whl" \
  "$ROOT/vllm-0.27.1.dev5+rocm10.0.0.gf46a9dfe2.d20260826-cp314-cp314-linux_x86_64.whl" \
  'torch[device-gfx1100]==2.12.0+rocm10.0.0' 'torchvision[device-gfx1100]==0.27.0+rocm10.0.0' \
  'flash-attn==2.8.3' 'amd-aiter==0.1.20.post1'
# This vendor-documented post-install workaround is a separate transaction.
"$UV" pip install --python "$ROOT/framework-venv/bin/python" 'tensorizer==2.12.1' 'torch==2.12.0+rocm10.0.0'
HIP_VISIBLE_DEVICES=0 "$ROOT/framework-venv/bin/python" /tmp/rtl-agent-validation/tools/probe_frameworks.py \
  > "$ROOT/evidence/framework-probe-final.json" 2> "$ROOT/evidence/framework-probe-final.stderr"
