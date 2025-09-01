## Root Dockerfile delegating build to TECHXPO/ subfolder so platforms (DigitalOcean, etc.)
## that always look for a root Dockerfile can still build without custom context config.
## If your platform lets you point to TECHXPO/Dockerfile directly you can also delete this file.

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates && rm -rf /var/lib/apt/lists/*

ARG APP_DIR=TECHXPO
WORKDIR /app

# Copy requirements from subfolder
COPY ${APP_DIR}/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy only needed app subtree (speeds up cache if repo large)
COPY ${APP_DIR}/ ./

# Non-root user
RUN useradd -m appuser && chown -R appuser /app
USER appuser

ENV AGENT_NAME=kiosk \
    PYTHONPATH=/app \
    RUN_AGENT=1 \
    PORT=8080

# Ensure entrypoint present
RUN [ -f entrypoint.sh ] || (echo '#!/usr/bin/env bash\nset -e\npython gemini_kiosk.py & exec uvicorn web.server:app --host 0.0.0.0 --port ${PORT:-8080}' > entrypoint.sh && chmod +x entrypoint.sh)

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 CMD curl -f http://localhost:${PORT}/healthz || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
