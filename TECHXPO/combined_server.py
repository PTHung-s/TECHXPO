"""Unified FastAPI app combining kiosk web server and dashboard.

Located inside package root so 'uvicorn TECHXPO.combined_server:app' works
both locally and on platforms expecting a module path.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .web.server import app as kiosk_app  # web token/static
from .Dashboard.server import app as dashboard_app  # dashboard APIs & static

main_app = FastAPI(title="Unified Kiosk + Dashboard")

main_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@main_app.get("/dashboard", include_in_schema=False)
def _redir():
    return RedirectResponse("/dashboard/")

# Order: specific mount first
main_app.mount("/dashboard", dashboard_app)
main_app.mount("/", kiosk_app)

@main_app.get("/healthz-unified")
def healthz_unified():
    return {"ok": True, "components": ["kiosk", "dashboard"]}

# Expose as 'app' for uvicorn
app = main_app