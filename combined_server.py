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

# Project layout: working directory contains 'web' and 'Dashboard' packages directly.
import importlib, sys, os
MODULE_CANDIDATES = [
  ("web.server", "kiosk_app"),
  ("Dashboard.server", "dashboard_app"),
  ("TECHXPO.web.server", "kiosk_app"),
  ("TECHXPO.Dashboard.server", "dashboard_app"),
]

kiosk_app = None
dashboard_app = None

def _try_import(mod_path):
  try:
    return importlib.import_module(mod_path)
  except ModuleNotFoundError:
    return None

# Ensure both potential roots on path
cur = os.path.dirname(__file__)
sys.path.append(cur)
sys.path.append(os.path.join(cur, 'TECHXPO'))

for m, kind in MODULE_CANDIDATES:
  mod = _try_import(m)
  if not mod:
    continue
  if kind == 'kiosk_app' and kiosk_app is None and hasattr(mod, 'app'):
    kiosk_app = getattr(mod, 'app')
  if kind == 'dashboard_app' and dashboard_app is None and hasattr(mod, 'app'):
    dashboard_app = getattr(mod, 'app')
  if kiosk_app and dashboard_app:
    break

if not kiosk_app or not dashboard_app:
  raise RuntimeError("Could not locate kiosk_app or dashboard_app modules")

from fastapi.responses import RedirectResponse

# This is the main app that will host everything
app = FastAPI(title="Unified Kiosk + Dashboard")

# Add middleware to the main app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add a redirect from /dashboard to /dashboard/
@app.get("/dashboard", include_in_schema=False)
def redirect_dashboard():
    return RedirectResponse(url="/dashboard/")

# IMPORTANT: Mount the more specific path FIRST
app.mount("/dashboard", dashboard_app)

# Mount the root/catch-all path LAST
app.mount("/", kiosk_app)

@app.get("/healthz-unified")
def healthz_unified():
  return {"ok": True, "components": ["kiosk", "dashboard"]}
