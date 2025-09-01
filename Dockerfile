ARG PYTHON_VERSION=3.11-slim
FROM python:${PYTHON_VERSION} AS builder
LABEL stage=builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates git && rm -rf /var/lib/apt/lists/*

# Requirements first for cache
COPY TECHXPO/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && \
    python -c "import pkgutil,sys;print('[builder] deps ok, total modules',len(list(pkgutil.iter_modules())))"

# Runtime stage (slim, no build-essential)
FROM python:${PYTHON_VERSION} AS runtime
LABEL maintainer="techxpo" org.opencontainers.image.source="https://example/repo" \
      org.opencontainers.image.description="TECHXPO kiosk (agent + web)"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends bash curl ca-certificates procps && rm -rf /var/lib/apt/lists/*

# Copy site-packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY TECHXPO/ ./

# Normalize line endings & ensure shell scripts executable (if any)
RUN find . -maxdepth 1 -name '*.sh' -exec sed -i 's/\r$//' {} + -exec chmod 755 {} + || true

# Create app user
RUN useradd -m appuser && chown -R appuser /app
USER appuser

ENV AGENT_NAME=kiosk RUN_AGENT=1 PORT=8080 PYTHONPATH=/app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 CMD curl -f http://localhost:${PORT}/healthz || exit 1

# Start script
RUN printf '#!/usr/bin/env bash\nset -euo pipefail\nPORT=${PORT:-8080}\nRUN_AGENT=${RUN_AGENT:-1}\necho "[start] RUN_AGENT=$RUN_AGENT PORT=$PORT"\nif [ "$RUN_AGENT" = "1" ]; then\n  echo "[start] launching agent"\n  if python gemini_kiosk.py --help 2>/dev/null | grep -q " start"; then\n    (python -u gemini_kiosk.py start 2>&1 | sed "s/^/[agent] /") &\n  else\n    (python -u gemini_kiosk.py 2>&1 | sed "s/^/[agent] /") &\n  fi\n  sleep 2\n  pgrep -f gemini_kiosk.py >/dev/null || echo "[start] WARNING agent not running" >&2\nfi\nexec uvicorn web.server:app --host 0.0.0.0 --port $PORT\n' > /app/start.sh && chmod +x /app/start.sh

CMD ["/app/start.sh"]
