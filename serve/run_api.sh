#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")/.."
source serve/config.sh
exec python3 -m serve.watchdog "${1:-status}"
