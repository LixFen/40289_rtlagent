#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")/.."
source serve/config.sh
mkdir -p "$EDA_TMP" "$AGENT_TMP"
if [ -n "${XILINX_VIVADO:-}" ]; then
  # The image's aggregate settings64.sh may reference uninstalled PDM/Model Composer.
  if [ -f "$XILINX_VIVADO/.settings64-Vivado.sh" ]; then
    source "$XILINX_VIVADO/.settings64-Vivado.sh"
  else
    source "$XILINX_VIVADO/settings64.sh"
  fi
fi
_rtl_license_exports="$(python3 -m serve.license_probe --shell)"
eval "$_rtl_license_exports"
unset _rtl_license_exports
python3 -m serve.startup
