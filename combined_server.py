"""Unified FastAPI app combining kiosk web server and dashboard.

Run: uvicorn combined_server:app --host 0.0.0.0 --port 8080

Resulting routes:
  /api/token, /healthz, static kiosk assets (from web.server)
  /dashboard/... (dashboard static UI)
  /dashboard/api/*  (dashboard APIs remapped with prefix)
Original dashboard endpoints like /api/bookings now available at /dashboard/api/bookings
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from TECHXPO.web.server import app as kiosk_app  # token server & static kiosk
from TECHXPO.Dashboard.server import app as dashboard_app  # existing dashboard app

app = FastAPI(title="Unified Kiosk + Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount kiosk root at '/' (kiosk_app already has its own /api + static)
app.mount("/", kiosk_app)

# Re-mount dashboard under /dashboard.
# dashboard_app already serves static at '/' internally; when mounted at /dashboard
# the static UI will be accessible at /dashboard/index.html (and /dashboard/)
app.mount("/dashboard", dashboard_app)

@app.get("/healthz-unified")
def healthz_unified():
    return {"ok": True, "components": ["kiosk", "dashboard"]}
