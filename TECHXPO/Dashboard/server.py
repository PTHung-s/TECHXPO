"""FastAPI server exposing scheduling overview & booking endpoints.

Run: uvicorn Dashboard.server:app --reload --port 8090
Then open Dashboard/static/index.html (it will fetch from http://localhost:8090)
"""
from __future__ import annotations
from fastapi import FastAPI, Query, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import asyncio
import datetime as dt
from pathlib import Path
# >>>>>>>>>> ADD THIS BLOCK TO LOAD .ENV <<<<<<<<<<
from dotenv import load_dotenv
# Load from .env in the same directory as this server file, or parent directories
# This ensures KIOSK_DB, VISIT_LOOKUP_DEBUG etc. are loaded at startup
load_dotenv()
# >>>>>>>>>>>>>>>>>>>>> END BLOCK <<<<<<<<<<<<<<<<<<<


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
    ALL_SLOTS,
)
from storage import find_visit_by_booking, get_or_create_customer  # reuse if needed

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

# ---------------- WebSocket realtime updates ----------------
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []
        self._lock = asyncio.Lock()
        self._last_broadcast_version: Optional[int] = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active.append(ws)
        # Gửi phiên bản hiện tại ngay khi kết nối
        try:
            await ws.send_json({"type": "hello", "version": get_bookings_version()})
        except Exception:
            pass

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, payload: Dict[str, Any]):
        data = None
        try:
            import json
            data = json.dumps(payload)
        except Exception:
            return
        to_remove: List[WebSocket] = []
        # copy để tránh thay đổi khi iterate
        for ws in list(self.active):
            try:
                await ws.send_text(data)
            except Exception:
                to_remove.append(ws)
        for ws in to_remove:
            await self.disconnect(ws)

manager = ConnectionManager()

@app.websocket("/ws/updates")
async def ws_updates(ws: WebSocket):
    # Allow any origin (CORS handled separately for HTTP)
    await manager.connect(ws)
    try:
        while True:
            # Keep connection alive; we don't require client messages
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws)
    except Exception:
        await manager.disconnect(ws)

async def _version_watcher():
    """Background task: quan sát version thay đổi và phát WS event.

    Lý do không hook trực tiếp trong schedule_logic: tránh circular imports.
    Poll mỗi 1s (có thể giảm nếu muốn). Khi version đổi → broadcast.
    """
    await asyncio.sleep(1.0)
    last_v = get_bookings_version()
    # DB signature (bookings + holds row counts) to catch external writes that bypass book_slot/create_hold
    import sqlite3
    db_path = (BASE_DIR / "Dashboard" / "schedule.db")
    def _db_sig():
        try:
            if not db_path.is_file():
                return 0
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            # sum counts; missing table -> 0
            total = 0
            try:
                cur.execute("SELECT COUNT(*) FROM bookings")
                total += cur.fetchone()[0]
            except Exception:
                pass
            try:
                cur.execute("SELECT COUNT(*) FROM holds")
                total += (cur.fetchone() or [0])[0]
            except Exception:
                pass
            conn.close()
            return total
        except Exception:
            return -1
    last_sig = _db_sig()
    while True:
        await asyncio.sleep(1.0)
        v = get_bookings_version()
        sig = _db_sig()
        if v != last_v:
            last_v = v
            try:
                await manager.broadcast({"type": "version", "version": v})
            except Exception:
                pass
        elif sig != last_sig:
            # External DB change without version bump -> force clients to refresh
            last_sig = sig
            try:
                await manager.broadcast({"type": "refresh", "reason": "db_sig_change", "signature": sig})
            except Exception:
                pass

@app.on_event("startup")
async def _start_ws_watcher():
    # ensure DB init already performed by earlier startup handler
    # launch watcher
    asyncio.create_task(_version_watcher())

@app.get("/api/bookings_stats")
def api_bookings_stats(
    hospital_code: str = Query(...),
    date: Optional[str] = Query(None),
    department_codes: Optional[str] = Query(None, description="Comma separated department codes (optional)"),
):
    """Return aggregated statistics for a hospital schedule day.

    Metrics:
      total_doctors: number of doctors across selected departments
      total_slots: total_doctors * len(ALL_SLOTS)
      booked: number of booked slots
      held: number of held-only slots (excluding those already booked)
      free: remaining free slots
      utilization: booked / total_slots (0-1)
    """
    if not date:
        date = dt.date.today().isoformat()
    meta = get_hospital_meta(hospital_code)
    if not meta:
        raise HTTPException(status_code=404, detail="hospital_not_found")
    by_code = meta.get("departments_by_code") or {}
    if department_codes:
        filter_codes = {c.strip() for c in department_codes.split(',') if c.strip()}
        dept_items = {code: info for code, info in by_code.items() if code in filter_codes}
    else:
        dept_items = by_code
    codes = list(dept_items.keys())
    snap = get_bookings_snapshot_by_codes(hospital_code, codes, date)
    slot_len = len(ALL_SLOTS)
    total_doctors = sum(len(info.get("doctors", [])) for info in dept_items.values())
    total_slots = total_doctors * slot_len
    booked = 0
    for code, doc_map in (snap.get("bookings") or {}).items():
        for slots in doc_map.values():
            booked += len(slots)
    held = 0
    # held-only (exclude those already booked)
    booked_lookup = set()
    for code, doc_map in (snap.get("bookings") or {}).items():
        for doc, slots in doc_map.items():
            for s in slots:
                booked_lookup.add((code, doc, s))
    for code, doc_map in (snap.get("holds") or {}).items():
        for doc, slots in doc_map.items():
            for s in slots:
                if (code, doc, s) not in booked_lookup:
                    held += 1
    free = max(0, total_slots - booked - held)
    utilization = (booked / total_slots) if total_slots else 0.0
    return {
        "hospital_code": hospital_code,
        "date": date,
        "department_codes": codes,
        "version": snap.get("version"),
        "metrics": {
            "total_doctors": total_doctors,
            "slots_per_doctor": slot_len,
            "total_slots": total_slots,
            "booked": booked,
            "held": held,
            "free": free,
            "utilization": utilization,
        }
    }

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
