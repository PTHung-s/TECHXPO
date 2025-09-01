FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl ca-certificates build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency manifests first (better layer cache if code changes)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy rest of application
COPY . ./

# Create non-root user for runtime
RUN useradd -m appuser && chown -R appuser /app
USER appuser

ENV AGENT_NAME=kiosk \
    RUN_AGENT=1 \
    PORT=8080 \
    PYTHONPATH=/app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 CMD curl -f http://localhost:${PORT}/healthz || exit 1

# Run worker (background) + uvicorn in one container (simple). If RUN_AGENT=0 skip worker.
CMD ["bash","-lc","set -e; echo '[docker] starting (RUN_AGENT='${RUN_AGENT}')'; if [ \"$RUN_AGENT\" = \"1\" ]; then python gemini_kiosk.py start & fi; exec uvicorn web.server:app --host 0.0.0.0 --port ${PORT}"]
