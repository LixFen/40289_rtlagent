#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")/.."
source serve/start.sh
exec python3 -m serve.watchdog _loop
