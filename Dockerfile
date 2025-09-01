FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl ca-certificates build-essential procps && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies layer
COPY TECHXPO/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY TECHXPO/ ./

# Start script
RUN printf '#!/usr/bin/env bash\nset -euo pipefail\nPORT="${PORT:-8080}"\nRUN_AGENT="${RUN_AGENT:-1}"\nAGENT_NAME="${AGENT_NAME:-kiosk}"\necho "[start] RUN_AGENT=$RUN_AGENT AGENT_NAME=$AGENT_NAME PORT=$PORT"\nif [ "$RUN_AGENT" = "1" ]; then\n  echo "[start] launching agent"\n  if python gemini_kiosk.py --help 2>/dev/null | grep -q " start"; then\n    (python -u gemini_kiosk.py start 2>&1 | sed "s/^/[agent] /") &\n  else\n    (python -u gemini_kiosk.py 2>&1 | sed "s/^/[agent] /") &\n  fi\n  sleep 2\n  if ! pgrep -f "gemini_kiosk.py" >/dev/null; then\n    echo "[start] ERROR: agent not running (check LIVEKIT_* / GOOGLE_API_KEY)" >&2\n  fi\nfi\necho "[start] starting uvicorn"\nexec uvicorn web.server:app --host 0.0.0.0 --port $PORT\n' > /app/start.sh && chmod +x /app/start.sh

# Non-root
RUN useradd -m appuser && chown -R appuser /app
USER appuser

ENV AGENT_NAME=kiosk RUN_AGENT=1 PORT=8080 PYTHONPATH=/app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 CMD curl -f http://localhost:${PORT}/healthz || exit 1

CMD ["/app/start.sh"]
