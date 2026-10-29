#!/usr/bin/env bash
# Reproduce the verified isolated W7900D setup without overwriting /opt/rocm.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"
RUNTIME="${RTL_RUNTIME_ROOT:-/tmp/rtl-runtime}"
BIN="$RUNTIME/llama/llama-b11552"
COMPAT="$RUNTIME/compat/root/usr/lib/x86_64-linux-gnu"
SITE="$RUNTIME/framework-venv/lib/python3.14/site-packages"
MODEL_PATH="${MODEL_PATH:-$RUNTIME/model-27b/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf}"
test -f "$MODEL_PATH"
test -f "$SITE/_rocm_sdk_core/lib/libamdhip64.so.7"
export HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}"
unset GGML_BACKEND_PATH
cd "$BIN"
exec "$COMPAT/ld-linux-x86-64.so.2" --library-path \
  "$COMPAT:$BIN:$SITE/_rocm_sdk_core/lib:$SITE/_rocm_sdk_libraries/lib:$SITE/_rocm_sdk_device_gfx1100/lib" \
  "$BIN/llama-server" --model "$MODEL_PATH" --alias "$MODEL_NAME" \
  --host "$LLM_BIND_HOST" --port "$LLM_BIND_PORT" --device ROCm0 --n-gpu-layers 99 \
  --ctx-size "$AGENT_CONTEXT_TOKENS" --parallel 1 --jinja --temp 0 --top-p 1 --top-k 0 --min-p 0 --reasoning off
