#!/usr/bin/env bash
# 仅由可信配置读取器生成 export；不输出或读取密钥。
_rtl_config_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$_rtl_config_root/serve/env.sh" || return 1
_rtl_config_exports="$(cd "$_rtl_config_root" && python3 -m agent.config --shell)"
eval "$_rtl_config_exports"
unset _rtl_config_root _rtl_config_exports
