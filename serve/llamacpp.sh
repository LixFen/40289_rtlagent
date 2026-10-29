#!/usr/bin/env bash
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"
: "${LLAMA_SERVER:?Set LLAMA_SERVER to the verified ROCm llama-server executable}"
: "${MODEL_PATH:?Set MODEL_PATH to the verified IQ3_S GGUF file}"
test -f "$MODEL_PATH"
export HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}"
exec "$LLAMA_SERVER" --model "$MODEL_PATH" --alias "$MODEL_NAME" \
  --host "$LLM_BIND_HOST" --port "$LLM_BIND_PORT" --device ROCm0 --n-gpu-layers 99 --ctx-size "$AGENT_CONTEXT_TOKENS" \
  --parallel 1 --jinja --temp 1 --top-p 0.95 --top-k 20 --min-p 0 --presence-penalty 0 --repeat-penalty 1 \
  --chat-template-kwargs "$LLAMA_CHAT_TEMPLATE_KWARGS" \
  --reasoning-budget "$LLAMA_REASONING_BUDGET" --reasoning-format deepseek --reasoning-preserve
