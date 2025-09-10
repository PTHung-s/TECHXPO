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

# Copy full source to /app/TECHXPO (for runtime)
COPY TECHXPO/ /app/TECHXPO/

# Optional: remove build tools if không cần runtime native compile
RUN apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* || true

# Add entrypoint script to run services and make executable
COPY TECHXPO/entrypoint.sh /app/TECHXPO/entrypoint.sh
COPY TECHXPO/entrypoint-dashboard.sh /app/TECHXPO/entrypoint-dashboard.sh
RUN chmod +x /app/TECHXPO/entrypoint.sh && chmod +x /app/TECHXPO/entrypoint-dashboard.sh

# Non‑root
RUN useradd -m appuser && \
    chown -R appuser /app && \
    mkdir -p /data && \
    chown -R appuser /data
USER appuser

ENV AGENT_NAME=kiosk \
    RUN_AGENT=1 \
    PORT=8080 \
    PYTHONPATH=/app  

WORKDIR /app/TECHXPO

EXPOSE 8080
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:${PORT}/healthz || exit 1

# RUN_AGENT=1 => khởi động worker; RUN_DASHBOARD=1 => bật dashboard
CMD ["/app/TECHXPO/entrypoint.sh"]