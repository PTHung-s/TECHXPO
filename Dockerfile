FROM python:3.11.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl ca-certificates build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first (layer cache)
COPY TECHXPO/requirements.txt /app/TECHXPO/requirements.txt
RUN pip install --no-cache-dir -r /app/TECHXPO/requirements.txt

# Copy full source
COPY TECHXPO/ /app/TECHXPO/

# Optional: remove build tools if không cần runtime native compile
RUN apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* || true

# Non‑root
RUN useradd -m appuser && chown -R appuser /app
USER appuser

ENV AGENT_NAME=kiosk \
    RUN_AGENT=1 \
    PORT=8080 \
    PYTHONPATH=/app/TECHXPO:/app/TECHXPO/TECHXPO

WORKDIR /app/TECHXPO

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:${PORT}/healthz || exit 1

# RUN_AGENT=1 => khởi động worker; nếu 0 chỉ chạy web
CMD ["bash","-lc","echo '[docker] RUN_AGENT='${RUN_AGENT}' AGENT_NAME='${AGENT_NAME}; \
if [ \"$RUN_AGENT\" = '1' ]; then echo '[docker] launching agent worker'; python -u gemini_kiosk.py dev & fi; \
echo '[docker] unified server starting on ' ${PORT}; exec uvicorn combined_server:app --host 0.0.0.0 --port ${PORT}"]