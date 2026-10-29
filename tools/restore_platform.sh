#!/usr/bin/env bash
# Offline, repeatable recovery after the platform replaces its root filesystem.
set -eo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ "${1:-}" != "" ] && [ "${1:-}" != "--software-only" ]; then
  echo "Usage: bash tools/restore_platform.sh [--software-only]" >&2
  exit 2
fi
BUNDLE="${RTL_RECOVERY_BUNDLE:-/workspace/40289-recovery/runtime-20261028.tar.gz}"
ENV_FILE="${RTL_ENV_FILE:-/workspace/40289.env.sh}"
if [ -x /usr/bin/python3 ]; then
  BOOTSTRAP_PYTHON=/usr/bin/python3
else
  BOOTSTRAP_PYTHON="$(command -v python3)"
fi
"$BOOTSTRAP_PYTHON" tools/runtime_capsule.py restore --bundle "$BUNDLE" --manifest "${BUNDLE%.gz}.json"
if [ ! -f "$ENV_FILE" ]; then
  cp serve/platform.env.example.sh "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
export RTL_AGENT_ROOT="$ROOT"
source "$ENV_FILE"
python3 -B -c 'from serve.credentials import load_token; load_token()'
bash serve/check_image.sh
python3 -B tools/build_image.py
if [ "${1:-}" = "--software-only" ]; then
  exit 0
fi
bash serve/deploy.sh "$ENV_FILE"
python3 -B tools/platform_tunnel.py ensure
echo "Recovery complete. If the platform issued a new URL, report it to the organizers."
