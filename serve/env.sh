#!/usr/bin/env bash
# Source public image defaults. API/deploy load the pre-filled private token file.
_rtl_env_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v python3 >/dev/null 2>&1 && [ -x /opt/python/bin/python3 ]; then
  export PATH="/opt/python/bin:$PATH"
fi
_rtl_env_exports="$(cd "$_rtl_env_root" && python3 -m serve.environment --shell)" || return 1
eval "$_rtl_env_exports"
# Reuse a synthesis-verified selection without probing again for every task.
if [ -f "$AGENT_TMP/services/license-probe/success.json" ]; then
  _rtl_license_exports="$(cd "$_rtl_env_root" && python3 -m serve.license_probe --cached-shell)" || return 1
  eval "$_rtl_license_exports"
  unset _rtl_license_exports
fi
unset _rtl_env_root _rtl_env_exports
