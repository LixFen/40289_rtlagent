#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source serve/config.sh
if [ -n "${XILINX_VIVADO:-}" ]; then
  if [ -f "$XILINX_VIVADO/.settings64-Vivado.sh" ]; then
    source "$XILINX_VIVADO/.settings64-Vivado.sh"
  else
    source "$XILINX_VIVADO/settings64.sh"
  fi
fi
python3 -m serve.image_check
