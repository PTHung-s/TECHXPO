#!/usr/bin/env bash
set -euo pipefail

# Allow dynamic model / runtime config
: "${PORT:=8080}"
: "${RUN_AGENT:=1}"
: "${AGENT_NAME:=kiosk}"

# If we want to run the LiveKit agent worker inside same container we background it
if [ "${RUN_AGENT}" = "1" ]; then
  echo "[entrypoint] Starting LiveKit agent worker (AGENT_NAME=${AGENT_NAME})" >&2
  # Use python -m for clearer module path; adjust if entry changes
  python gemini_kiosk.py &
fi

# Start FastAPI token/static server (serves web/) using uvicorn
exec "$@"
