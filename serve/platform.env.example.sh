# Copy to /workspace/40289.env.sh before deployment. Contains no credentials.
export PATH="/opt/python/bin:$PATH"
export RTL_AGENT_ROOT="${RTL_AGENT_ROOT:-/workspace/40289_rtlagent}"
export XILINX_VIVADO="${XILINX_VIVADO:-/tools/Xilinx/2026.1/Vivado}"
if [ -f "$XILINX_VIVADO/.settings64-Vivado.sh" ]; then
  source "$XILINX_VIVADO/.settings64-Vivado.sh"
else
  source "$XILINX_VIVADO/settings64.sh"
fi
export MODEL_PATH=/workspace/models/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf
export LLAMA_SERVER=/opt/llamacpp/llama-server
export SLANG_BIN=/opt/slang/12.0/bin/slang
# The capsule can restore native SDK libraries even if only system Python remains.
_rtl_sdk_lib=/opt/python/lib/python3.14/site-packages/_rocm_sdk_devel/lib
for _rtl_lib in "$_rtl_sdk_lib" "$_rtl_sdk_lib/llvm/lib" "$_rtl_sdk_lib/rocm_sysdeps/lib"; do
  if [ -d "$_rtl_lib" ]; then
    case ":${LD_LIBRARY_PATH:-}:" in
      *":$_rtl_lib:"*) ;;
      *) export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$_rtl_lib" ;;
    esac
  fi
done
unset _rtl_sdk_lib _rtl_lib
export INFERENCE_START="$RTL_AGENT_ROOT/serve/llamacpp.sh"
export FPGACHINA_TOKEN_FILE="$RTL_AGENT_ROOT/serve/fpga-token.local.txt"
unset FPGACHINA_TOKEN GPU_DRM_CARD GPU_ASSIGNED_COUNT
export HIP_VISIBLE_DEVICES=0
export OMP_NUM_THREADS=16
export EDA_TMP=/var/tmp/eda
export AGENT_TMP=/var/tmp/40289-rtl-agent
export RUN_ARCHIVE_DIR=/workspace/40289-deployment-20261028/runs
export RUN_ARCHIVE_MB=8192
export GPU_TELEMETRY_FILE=/var/tmp/eda/40289-gpu.json
export AGENT_STREAM=1
export NO_PROXY=localhost,127.0.0.1,::1
# Supplies model identity, loopback endpoint, public license and detected GPU card.
source "$RTL_AGENT_ROOT/serve/config.sh"
