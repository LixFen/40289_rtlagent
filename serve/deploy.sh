#!/usr/bin/env bash
# Host deployment: load persistent configuration, then start model and HTTP.
set -eo pipefail
cd "$(dirname "$0")/.."
env_file="${1:-/workspace/env.sh}"
if [ ! -f "$env_file" ]; then
  if [ "$#" -gt 0 ]; then
    echo "Environment file not found: $env_file" >&2
    exit 1
  fi
else
  source "$env_file"
fi
source serve/env.sh
python3 -m serve.prepare_image
exec python3 -m serve.deploy
