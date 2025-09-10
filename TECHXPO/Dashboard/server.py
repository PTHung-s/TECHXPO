"""FastAPI server exposing scheduling overview & booking endpoints.

Run: uvicorn Dashboard.server:app --reload --port 8090
Then open Dashboard/static/index.html (it will fetch from http://localhost:8090)
"""
from __future__ import annotations
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import datetime as dt
from pathlib import Path

from .schedule_logic import (
    get_overview,
    book_slot,
    init_db,
    list_hospitals,
    get_hospital_meta,
    get_bookings_snapshot,
    get_bookings_version,
    get_bookings_snapshot_by_codes,
    backfill_department_codes,
)
from ..storage import find_visit_by_booking, get_or_create_customer  # reuse if needed

app = FastAPI(title="Doctor Schedule Dashboard", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"] ,
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = BASE_DIR / "Dashboard" / "static"

# Mount tại /dashboard (có thể đổi thành "/")
app.mount("/dashboard", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")

# Tuỳ chọn: chuyển root "/" về index
@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


class BookRequest(BaseModel):
    hospital_code: str
    department: str  # display name (legacy / UI)
    doctor_name: str
    date: str  # YYYY-MM-DD
    slot_time: str  # HH:MM
    department_code: Optional[str] = None  # NEW (preferred identifier)

class BookByCodeRequest(BaseModel):
    hospital_code: str
    department_code: str
    doctor_name: str
    date: str
    slot_time: str
    # department display name optional if client wants to pass
    department: Optional[str] = None


@app.get("/api/departments")
def api_departments(hospital_code: str = Query(...)):
    data = list_hospitals()
    hospitals = data.get("hospitals", {})
    depts = hospitals.get(hospital_code)
    if not depts:
        raise HTTPException(status_code=404, detail="hospital_not_found_or_no_departments")
    return {"hospital_code": hospital_code, "departments": depts}


@app.get("/api/overview")
def api_overview(
    hospital_code: str = Query(...),
    departments: Optional[str] = Query(None, description="Comma separated department names (optional)"),
    date: Optional[str] = Query(None),
):
    if not hospital_code:
        raise HTTPException(status_code=400, detail="missing_hospital_code")
    all_map = list_hospitals().get("hospitals", {})
    all_depts = all_map.get(hospital_code)
    if not all_depts:
        raise HTTPException(status_code=404, detail="hospital_not_found_or_no_departments")

    if departments:
        dep_list = [d.strip() for d in departments.split(",") if d.strip()]
    else:
        dep_list = all_depts  # dùng toàn bộ khoa

    if not dep_list:
        raise HTTPException(status_code=400, detail="no_departments_available")

    return get_overview(hospital_code, dep_list, date)


@app.get("/api/meta")
def api_meta(
    hospital_code: str = Query(...),
):
    meta = get_hospital_meta(hospital_code)
    if not meta:
        raise HTTPException(status_code=404, detail="hospital_not_found")
    return {"hospital_code": hospital_code, **meta, "slots": {
        "start": "07:40", "end": "16:40", "slot_minutes": 20
    }}


@app.get("/api/bookings")
def api_bookings(
    hospital_code: str = Query(...),
    departments: str = Query(..., description="Comma separated departments"),
    date: Optional[str] = Query(None),
    since: Optional[int] = Query(None, description="Return only if version changed (comparison server-side)")
):
    if not hospital_code:
        raise HTTPException(status_code=400, detail="missing_hospital_code")
    dep_list = [d.strip() for d in departments.split(",") if d.strip()]
    if not dep_list:
        raise HTTPException(status_code=400, detail="no_departments")
    current_version = get_bookings_version()
    if since is not None and since == current_version:
        return {"hospital_code": hospital_code, "date": date or dt.date.today().isoformat(), "version": current_version, "bookings": {}, "unchanged": True}
    snap = get_bookings_snapshot(hospital_code, dep_list, date)
    return snap


@app.post("/api/book")
def api_book(req: BookRequest):
    # basic date + slot validation
    try:
        dt.datetime.strptime(req.date, "%Y-%m-%d")
        dt.datetime.strptime(req.slot_time, "%H:%M")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_date_or_slot_format")

    ok, msg = book_slot(
        req.hospital_code,
        req.department,
        req.doctor_name,
        req.date,
        req.slot_time,
        department_code=req.department_code,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}

@app.post("/api/book_by_code")
def api_book_by_code(req: BookByCodeRequest):
    try:
        dt.datetime.strptime(req.date, "%Y-%m-%d")
        dt.datetime.strptime(req.slot_time, "%H:%M")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_date_or_slot_format")
    # Need a department display name for legacy validation fallback
    dep_display = req.department or req.department_code
    ok, msg = book_slot(req.hospital_code, dep_display, req.doctor_name, req.date, req.slot_time, department_code=req.department_code)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}

@app.get("/api/bookings_by_code")
def api_bookings_by_code(
    hospital_code: str = Query(...),
    department_codes: str = Query(..., description="Comma separated department codes"),
    date: Optional[str] = Query(None),
    since: Optional[int] = Query(None)
):
    if not hospital_code:
        raise HTTPException(status_code=400, detail="missing_hospital_code")
    codes = [c.strip() for c in department_codes.split(",") if c.strip()]
    if not codes:
        raise HTTPException(status_code=400, detail="no_department_codes")
    current_version = get_bookings_version()
    if since is not None and since == current_version:
        return {"hospital_code": hospital_code, "date": date or dt.date.today().isoformat(), "version": current_version, "bookings": {}, "unchanged": True}
    snap = get_bookings_snapshot_by_codes(hospital_code, codes, date)
    return snap

@app.post("/api/backfill_department_codes")
def api_backfill_department_codes(hospital_code: Optional[str] = None):
    res = backfill_department_codes(hospital_code)
    return res

@app.get("/api/visit_detail")
def api_visit_detail(
    hospital_code: str = Query(...),
    date: str = Query(...),
    doctor_name: str = Query(...),
    slot_time: str = Query(...)
):
    """Fetch visit (wrap-up) detail linked to a booking slot.

    Client clicks a booked cell -> query by hospital, date, doctor, slot.
    Returns 404 if not found yet (wrap-up thread may still be persisting).
    """
    try:
        v = find_visit_by_booking(hospital_code, date, doctor_name, slot_time)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"lookup_error: {e}")
    if not v:
        # Second attempt: relax (pass empty hospital/date so matching allows missing)
        try:
            v = find_visit_by_booking(hospital_code="", date="", doctor_name=doctor_name, slot_time=slot_time)
        except Exception:
            v = None
    if not v:
        raise HTTPException(status_code=404, detail="visit_not_found")
    return v


@app.on_event("startup")
def _startup():
    init_db()

@app.get("/api/hospitals")
def api_hospitals():
    return list_hospitals()

# ---- Static serving ----
BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = BASE_DIR / "Dashboard" / "static"
if STATIC_DIR.is_dir():
    # Mount at root so /styles.css, /app.js accessible
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    @app.get("/index.html")
    def _index_alias():
        return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/hospitals")
def api_hospitals():
    return list_hospitals()
