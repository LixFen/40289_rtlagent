#!/usr/bin/env bash
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"
: "${MODEL_PATH:?Set MODEL_PATH to an offline local snapshot}"
test -d "$MODEL_PATH"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}"
exec vllm serve "$MODEL_PATH" --served-model-name "$MODEL_NAME" \
  --host "$LLM_BIND_HOST" --port "$LLM_BIND_PORT" --dtype bfloat16 --tensor-parallel-size 1 \
  --max-model-len "$AGENT_CONTEXT_TOKENS" --max-num-seqs 1 --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.85}" \
  --chat-template-kwargs '{"enable_thinking":false}'
