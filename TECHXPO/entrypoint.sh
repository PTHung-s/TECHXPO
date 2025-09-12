#!/bin/bash
set -e

# Entrypoint script for TECHXPO Docker container
# Handles both agent and web server startup based on environment variables

echo "=== TECHXPO Entrypoint Starting ==="
echo "RUN_AGENT: ${RUN_AGENT:-0}"
echo "RUN_DASHBOARD: ${RUN_DASHBOARD:-0}"
echo "PORT: ${PORT:-8080}"
echo "PYTHONPATH: ${PYTHONPATH}"
echo "Working directory: $(pwd)"

# Initialize Booking_data volume if needed (similar to entrypoint-dashboard.sh)
DATA_SRC="/app/TECHXPO_init/Booking_data"
DATA_DEST="/app/TECHXPO/Booking_data"

if [ -d "$DATA_SRC" ] && [ -d "$DATA_DEST" ] && [ ! "$(ls -A $DATA_DEST)" ]; then
  echo "Initializing Booking_data volume from image..."
  cp -a $DATA_SRC/* $DATA_DEST/
fi

# Ensure data directories exist
mkdir -p /data/out
mkdir -p /app/TECHXPO/Booking_data
mkdir -p /app/TECHXPO/KMS

# Set proper permissions for data directories
chown -R $(id -u):$(id -g) /data 2>/dev/null || true
chmod 755 /data /data/out 2>/dev/null || true

# Initialize database if needed
echo "Initializing database..."
python -c "
try:
    from storage import init_db
    init_db()
    print('Database initialized successfully')
except Exception as e:
    print(f'Database init warning: {e}')
" 2>/dev/null || echo "Database init skipped (module not found)"

# Cleanup function
cleanup() {
    echo "Shutting down services..."
    jobs -p | xargs -r kill 2>/dev/null || true
    wait
    echo "Cleanup completed"
    exit 0
}

# Set up signal handlers
trap cleanup SIGTERM SIGINT

# Start services based on environment variables
if [ "${RUN_AGENT}" = "1" ]; then
    echo "Starting LiveKit Agent..."
    python gemini_kiosk.py &
    AGENT_PID=$!
    echo "Agent started (PID: $AGENT_PID)"
    
    echo "Starting Web Server on port ${PORT:-8080}..."
    cd web
    uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080} &
    WEB_PID=$!
    echo "Web server started (PID: $WEB_PID)"
    cd ..
    
    if [ "${RUN_DASHBOARD}" = "1" ]; then
        echo "Starting Dashboard on port 8090..."
        uvicorn Dashboard.server:app --host 0.0.0.0 --port 8090 &
        DASHBOARD_PID=$!
        echo "Dashboard started (PID: $DASHBOARD_PID)"
    fi
else
    # If not running agent, start a basic health check server
    echo "Starting health check server on port ${PORT:-8080}..."
    python -c "
import uvicorn
from fastapi import FastAPI

app = FastAPI()

@app.get('/healthz')
def health():
    return {'status': 'ok', 'service': 'techxpo'}

@app.get('/')
def root():
    return {'message': 'TECHXPO service ready', 'agent_enabled': False}

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=${PORT:-8080})
" &
    HEALTH_PID=$!
    echo "Health check server started (PID: $HEALTH_PID)"
fi

# Keep the container running and handle signals
echo "=== All services started, waiting for signals ==="
wait