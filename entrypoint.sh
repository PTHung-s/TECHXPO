#!/usr/bin/env bash
set -euo pipefail

# Allow dynamic model / runtime config
: "${PORT:=8080}"
: "${RUN_AGENT:=1}"
: "${AGENT_NAME:=kiosk}"

# If we want to run the LiveKit agent worker inside same container we background it
if [ "${RUN_AGENT}" = "1" ]; then
  echo "[entrypoint] Starting LiveKit agent worker (AGENT_NAME=${AGENT_NAME})" >&2
  # run production worker subcommand if available
  if python gemini_kiosk.py --help 2>/dev/null | grep -q '^start'; then
    python gemini_kiosk.py start &
  else
    python gemini_kiosk.py &
  fi
fi

# Default command if none supplied
if [ "$#" -eq 0 ]; then
  set -- uvicorn web.server:app --host 0.0.0.0 --port "${PORT}"
fi

echo "[entrypoint] Launching web: $*" >&2
exec "$@"
