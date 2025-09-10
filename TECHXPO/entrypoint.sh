#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8080}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8090}"
RUN_AGENT="${RUN_AGENT:-1}"
RUN_DASHBOARD="${RUN_DASHBOARD:-1}"

echo "[entrypoint] PYTHONPATH=${PYTHONPATH} PORT=${PORT} DASHBOARD_PORT=${DASHBOARD_PORT} RUN_AGENT=${RUN_AGENT} RUN_DASHBOARD=${RUN_DASHBOARD}"

if [ "$RUN_AGENT" = "1" ]; then
  echo "[entrypoint] starting agent worker: python -u gemini_kiosk.py dev"
  python -u gemini_kiosk.py dev &
fi

if [ "$RUN_DASHBOARD" = "1" ]; then
  echo "[entrypoint] starting dashboard: uvicorn Dashboard.server:app --port ${DASHBOARD_PORT}"
  uvicorn Dashboard.server:app --host 0.0.0.0 --port ${DASHBOARD_PORT} &
fi

echo "[entrypoint] starting web: uvicorn web.server:app --port ${PORT}"
exec uvicorn web.server:app --host 0.0.0.0 --port ${PORT}
