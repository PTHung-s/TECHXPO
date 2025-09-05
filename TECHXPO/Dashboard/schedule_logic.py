"""Scheduling logic & storage (20‑minute slots) – reads doctor data from Booking_data (priority) or Data.
DB (schedule.db) only stores bookings (NOT doctor definitions).
"""
from __future__ import annotations
import os, json, sqlite3, threading, datetime as dt, unicodedata
from pathlib import Path
from typing import List, Dict, Any, Iterable, Optional, Tuple
import time

BASE_DIR = Path(__file__).resolve().parents[1]        # TECHXPO root
DATA_DIR_PRIMARY = BASE_DIR / "Booking_data"
DATA_DIR_SECONDARY = BASE_DIR / "Data"
DATA_DIRS = [p for p in [DATA_DIR_PRIMARY, DATA_DIR_SECONDARY] if p.is_dir()]
if not DATA_DIRS:
    DATA_DIR_PRIMARY.mkdir(parents=True, exist_ok=True)
    DATA_DIRS = [DATA_DIR_PRIMARY]

CATALOG_DIR = BASE_DIR / "catalog"
DB_PATH = BASE_DIR / "Dashboard" / "schedule.db"

WORK_START = dt.time(7, 40)
WORK_END = dt.time(16, 40)         # last slot START at 16:40
SLOT_MINUTES = 20

_DB_LOCK = threading.Lock()
SCHEDULE_DEBUG = os.getenv("SCHEDULE_DEBUG", "0") == "1"


def _dlog(msg: str):
    if SCHEDULE_DEBUG:
        print(f"[schedule_debug] {msg}")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Initialize DB (idempotent) and ensure new department_code columns exist.

    Migration notes:
    - Legacy schema lacked department_code (bookings & holds). We add nullable columns.
    - We DO NOT change primary keys to avoid table rebuild complexity during live sessions.
    - New writes will populate department_code when provided; reads remain backward compatible.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK:
        with _connect() as c:
            # Base bookings table
            c.execute("""CREATE TABLE IF NOT EXISTS bookings(
                hospital_code TEXT NOT NULL,
                department    TEXT NOT NULL,
                doctor_name   TEXT NOT NULL,
                date          TEXT NOT NULL,   -- YYYY-MM-DD
                slot_time     TEXT NOT NULL,   -- HH:MM
                department_code TEXT,          -- NEW (nullable for legacy rows)
                PRIMARY KEY (hospital_code, doctor_name, date, slot_time)
            )""")
            # Legacy deployments may not have department_code column; attempt ALTER defensively
            try:
                c.execute("ALTER TABLE bookings ADD COLUMN department_code TEXT")
            except Exception:
                pass
            c.execute("CREATE INDEX IF NOT EXISTS idx_book_dept ON bookings(hospital_code, department, date)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_book_dept_code ON bookings(hospital_code, department_code, date)")
            # Holds table created lazily elsewhere; ensure new column if table already exists
            try:
                c.execute("ALTER TABLE holds ADD COLUMN department_code TEXT")
            except Exception:
                pass
            c.commit()
    _dlog("DB initialized / migrated")


def generate_slots(start: dt.time = WORK_START,
                   end: dt.time = WORK_END,
                   step_minutes: int = SLOT_MINUTES) -> List[str]:
    today = dt.date.today()
    cur = dt.datetime.combine(today, start)
    end_dt = dt.datetime.combine(today, end)
    out: List[str] = []
    delta = dt.timedelta(minutes=step_minutes)
    while cur <= end_dt:
        out.append(cur.strftime("%H:%M"))
        cur += delta
    return out


ALL_SLOTS = generate_slots()
ALLOWED_SLOT_SET = set(ALL_SLOTS)

# ---------------- Caching (doctor metadata) ----------------
_HOSPITAL_META_CACHE: Dict[str, Dict[str, Any]] = {}
_META_TTL = 60  # seconds (re-validate mtimes every TTL)

def _hospital_source_paths(hospital_code: str) -> List[Path]:
    paths: List[Path] = []
    gpath = CATALOG_DIR / f"{hospital_code}.grouped.json"
    if gpath.is_file():
        paths.append(gpath)
    for ddir in DATA_DIRS:
        raw = ddir / f"{hospital_code}.json"
        if raw.is_file():
            paths.append(raw)
    return paths

def _calc_mtime_sig(paths: List[Path]) -> int:
    sig = 0
    for p in paths:
        try:
            stat = p.stat()
            sig ^= int(stat.st_mtime_ns) & 0xFFFFFFFFFFFF  # fold
        except FileNotFoundError:
            continue
    return sig

def _build_hospital_meta(hospital_code: str) -> Dict[str, Any]:
    """Build hospital meta.

    Supports both legacy (department name keyed) and new code-centric JSON where
    departments is an object: { "ABCDEF": {"name": "Khám Bệnh", "doctors": [...]}, ... }

    Returns dict with:
      - departments: {Display Name -> [doctor names]} (legacy compatibility)
      - departments_by_code: {department_code -> {"name": Display Name, "doctors": [names]}}
    """
    dept_map_raw = _load_grouped_json(hospital_code)
    # Detect new structure: if keys look like codes and values have 'name' field.
    departments_by_code: Dict[str, Dict[str, Any]] = {}
    legacy_name_map: Dict[str, List[str]] = {}
    for key, value in dept_map_raw.items():
        # Heuristic: if value is list[doctor_dict] -> legacy name keyed
        if isinstance(value, list):
            # Legacy path
            doctor_names: List[str] = []
            for d in value:
                if isinstance(d, dict):
                    nm = d.get("name") or d.get("Name")
                    if nm:
                        doctor_names.append(str(nm))
            if doctor_names:
                legacy_name_map[key] = sorted(set(doctor_names))
        elif isinstance(value, dict):
            # New path (code keyed)
            disp = value.get("name") or value.get("department") or key
            docs = value.get("doctors") or []
            doctor_names: List[str] = []
            if isinstance(docs, list):
                for d in docs:
                    if isinstance(d, dict):
                        nm = d.get("name") or d.get("Name")
                        if nm:
                            doctor_names.append(str(nm))
                    elif isinstance(d, str):
                        doctor_names.append(d)
            departments_by_code[key] = {"name": disp, "doctors": sorted(set(doctor_names))}
            # Also populate legacy map for compatibility
            if doctor_names:
                legacy_name_map[_normalize_department(str(disp))] = sorted(set(doctor_names))
    return {
        "departments": legacy_name_map,
        "departments_by_code": departments_by_code,
    }

def get_hospital_meta(hospital_code: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    paths = _hospital_source_paths(hospital_code)
    if not paths:
        return None
    m_sig = _calc_mtime_sig(paths)
    cached = _HOSPITAL_META_CACHE.get(hospital_code)
    if cached:
        if now - cached["cached_at"] < _META_TTL and cached["mtime_sig"] == m_sig:
            return cached["data"]
    data = _build_hospital_meta(hospital_code)
    _HOSPITAL_META_CACHE[hospital_code] = {"data": data, "mtime_sig": m_sig, "cached_at": now}
    _dlog(f"Meta (re)built for {hospital_code}: {len(data['departments'])} departments")
    return data

# ---------------- Bookings versioning ----------------
_BOOKINGS_VERSION = 0

def _bump_bookings_version():
    global _BOOKINGS_VERSION
    _BOOKINGS_VERSION += 1
    return _BOOKINGS_VERSION

def get_bookings_version() -> int:
    return _BOOKINGS_VERSION

def get_bookings_snapshot(hospital_code: str, departments: Iterable[str], date: Optional[str] = None) -> Dict[str, Any]:
    if not date:
        date = dt.date.today().isoformat()
    dep_norms = [_normalize_department(d) for d in departments]
    result: Dict[str, Any] = {
        "hospital_code": hospital_code,
        "date": date,
        "version": get_bookings_version(),
        "bookings": {},  # {department: {doctor_name: [slot,...]}}
    }
    with _connect() as conn:
        cur = conn.execute(
            """SELECT department, doctor_name, slot_time FROM bookings
                WHERE hospital_code=? AND date=? AND department IN ({})
                ORDER BY department, doctor_name, slot_time""".format(
                ",".join(["?"] * len(dep_norms))
            ),
            [hospital_code, date, *dep_norms]
        )
        for dep, doc, slot in cur.fetchall():
            result["bookings"].setdefault(dep, {}).setdefault(doc, []).append(slot)
    return result

# ---------------- Data Loading ----------------
def _normalize_department(s: str) -> str:
    return " ".join(s.strip().split()).title()


def _generic_extract_department_map(data: Any) -> Dict[str, List[Dict[str, Any]]]:
    """
    Duyệt sâu JSON để bắt mọi object có 'name' + (department|specialty|khoa...) → coi là bác sĩ.
    Truyền context department nếu object cấp trên đại diện một khoa.
    """
    dept_map: Dict[str, List[Dict[str, Any]]] = {}
    stack: List[Tuple[Any, Optional[str]]] = [(data, None)]
    MAX_DOC = 10000
    doctor_count = 0

    def norm_dep(x: str) -> str:
        return _normalize_department(x)

    def detect_dept(obj: Dict[str, Any]) -> Optional[str]:
        # ưu tiên field rõ ràng
        for k in ("department", "dept_name", "khoa", "specialty", "speciality"):
            if k in obj and isinstance(obj[k], str):
                return obj[k]
        # nếu object có key 'name' và key 'doctors' (list) → name đó là khoa
        if "name" in obj and isinstance(obj.get("doctors"), list):
            return str(obj["name"])
        return None

    def is_doctor_obj(obj: Dict[str, Any]) -> bool:
        if "name" not in obj:
            return False
        # có trường nhận dạng
        for k in ("department", "specialty", "speciality", "khoa"):
            if k in obj:
                return True
        # heuristics: có name + (position / title / phone / email) mà không có 'departments'
        if any(k in obj for k in ("position", "title", "phone", "email")) and "departments" not in obj:
            return True
        return False

    while stack:
        node, ctx_dept = stack.pop()
        if isinstance(node, dict):
            # xác định khoa mới (nếu có)
            new_dept = detect_dept(node) or ctx_dept
            # coi đây là bác sĩ?
            if is_doctor_obj(node):
                dep_name = node.get("department") or node.get("specialty") or node.get("speciality") or node.get("khoa") or new_dept
                if dep_name:
                    dep_norm = norm_dep(str(dep_name))
                    dept_map.setdefault(dep_norm, []).append(node)
                    doctor_count += 1
                    if doctor_count >= MAX_DOC:
                        break
            # duyệt con
            for v in node.values():
                if isinstance(v, (dict, list)):
                    stack.append((v, detect_dept(node) or new_dept))
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    stack.append((item, ctx_dept))
    if SCHEDULE_DEBUG:
        _dlog(f"[generic_parser] extracted {doctor_count} doctors across {len(dept_map)} departments")
    return dept_map


def _load_grouped_json(hospital_code: str) -> Dict[str, List[Dict[str, Any]]]:
    """Return {department: [doctor_objects]} with flexible schema support."""
    def from_catalog_obj(obj: Any) -> Dict[str, List[Dict[str, Any]]]:
        if not isinstance(obj, dict):
            return {}
        deps = obj.get("departments")
        out: Dict[str, List[Dict[str, Any]]] = {}
        if isinstance(deps, dict):
            for k, v in deps.items():
                if isinstance(v, list):
                    out[_normalize_department(k)] = [d for d in v if isinstance(d, dict)]
        elif isinstance(deps, list):
            for dep in deps:
                if not isinstance(dep, dict):
                    continue
                dname = dep.get("name") or dep.get("department") or dep.get("code")
                if not dname:
                    continue
                ds = dep.get("doctors") or dep.get("Doctors") or []
                if isinstance(ds, list):
                    out[_normalize_department(str(dname))] = [d for d in ds if isinstance(d, dict)]
        return out

    # 1) catalog grouped
    gpath = CATALOG_DIR / f"{hospital_code}.grouped.json"
    if gpath.is_file():
        try:
            obj = json.loads(gpath.read_text(encoding="utf-8"))
            dept_map = from_catalog_obj(obj)
            if dept_map:
                _dlog(f"Loaded grouped catalog for {hospital_code} -> {len(dept_map)} depts")
                return dept_map
        except Exception as e:
            _dlog(f"Catalog load error {hospital_code}: {e}")

    # 2) raw json in data dirs
    for ddir in DATA_DIRS:
        raw = ddir / f"{hospital_code}.json"
        if not raw.is_file():
            continue
        try:
            data = json.loads(raw.read_text(encoding="utf-8"))
        except Exception as e:
            _dlog(f"Raw parse error {raw}: {e}")
            continue

        dept_map: Dict[str, List[Dict[str, Any]]] = {}
        # a) list of doctor objects
        if isinstance(data, list):
            for doc in data:
                if not isinstance(doc, dict):
                    continue
                dep = (doc.get("department") or doc.get("Department") or
                       doc.get("specialty") or doc.get("Specialty"))
                name = doc.get("name") or doc.get("Name")
                if dep and name:
                    dep_norm = _normalize_department(str(dep))
                    dept_map.setdefault(dep_norm, []).append(doc)
        # b) object with departments list
        if isinstance(data, dict) and not dept_map:
            # New code-keyed structure: departments is a dict of code -> {name, doctors:[...]}
            if isinstance(data.get("departments"), dict):
                dep_dict = data.get("departments") or {}
                for code, dep_obj in dep_dict.items():
                    if not isinstance(dep_obj, dict):
                        continue
                    raw_name = dep_obj.get("name") or dep_obj.get("department") or code
                    try:
                        disp = unicodedata.normalize("NFC", str(raw_name)).strip()
                    except Exception:
                        disp = str(raw_name)
                    docs = dep_obj.get("doctors") or []
                    # Keep original doctor dict list shape (list[dict]) so _build_hospital_meta can detect new path via dict values later
                    if isinstance(docs, list):
                        # store as code -> {name, doctors:[dict...]}
                        clean_docs = [d for d in docs if isinstance(d, dict)]
                        dept_map[code] = {"name": disp, "doctors": clean_docs}
            # Legacy list structure
            if isinstance(data.get("departments"), list) and not dept_map:
                for dep_obj in data["departments"]:
                    if not isinstance(dep_obj, dict):
                        continue
                    dname = dep_obj.get("name") or dep_obj.get("department") or dep_obj.get("code")
                    if not dname:
                        continue
                    dname_norm = _normalize_department(str(dname))
                    docs = dep_obj.get("doctors") or dep_obj.get("Doctors") or []
                    if isinstance(docs, list):
                        dept_map[dname_norm] = [d for d in docs if isinstance(d, dict)]
            # c) object with doctors list
            if isinstance(data.get("doctors"), list) and not dept_map:
                for doc in data["doctors"]:
                    if not isinstance(doc, dict):
                        continue
                    dep = doc.get("department") or doc.get("specialty")
                    name = doc.get("name")
                    if dep and name:
                        dep_norm = _normalize_department(str(dep))
                        dept_map.setdefault(dep_norm, []).append(doc)

        if dept_map:
            _dlog(f"Built dept_map raw {hospital_code}: {len(dept_map)} depts")
            return dept_map

    # Nếu tới đây vẫn chưa parse được → thử generic
    for ddir in DATA_DIRS:
        raw = ddir / f"{hospital_code}.json"
        if raw.is_file():
            try:
                data = json.loads(raw.read_text(encoding="utf-8"))
                dept_map = _generic_extract_department_map(data)
                if dept_map:
                    _dlog(f"Generic extracted {hospital_code}: {len(dept_map)} depts")
                    return dept_map
            except Exception as e:
                _dlog(f"Generic parse error {raw}: {e}")
    return {}


def list_hospitals() -> Dict[str, Any]:
    """Scan all hospital JSONs, return {"hospitals": {code:[departments]}}."""
    result: Dict[str, List[str]] = {}
    # prefer catalog grouped codes if present (but we still scan raw below)
    if CATALOG_DIR.is_dir():
        for f in CATALOG_DIR.glob("*.grouped.json"):
            code = f.stem.replace(".grouped", "")
            dept_map = _load_grouped_json(code)
            if dept_map:
                result[code] = sorted(dept_map.keys())
    # raw
    for ddir in DATA_DIRS:
        for f in ddir.glob("*.json"):
            code = f.stem
            if code in result:
                continue
            dept_map = _load_grouped_json(code)
            if dept_map:
                result[code] = sorted(dept_map.keys())
    return {"hospitals": result, "source_dirs": [str(p) for p in DATA_DIRS]}

# ---------------- Doctor / Availability ----------------
def get_doctors_for_departments(hospital_code: str, departments: Iterable[str]) -> Dict[str, List[str]]:
    meta = get_hospital_meta(hospital_code)
    out: Dict[str, List[str]] = {}
    if not meta:
        return out
    dep_set = {_normalize_department(d) for d in departments}
    for dep, names in meta["departments"].items():
        if dep in dep_set:
            out[dep] = names
    return out

def get_doctors_for_department_codes(hospital_code: str, department_codes: Iterable[str]) -> Dict[str, List[str]]:
    """Return mapping department_code -> doctor list (new code-centric)."""
    meta = get_hospital_meta(hospital_code)
    out: Dict[str, List[str]] = {}
    if not meta:
        return out
    by_code = meta.get("departments_by_code") or {}
    for code in department_codes:
        info = by_code.get(code)
        if info:
            out[code] = info.get("doctors", [])
    return out


def _get_booked_for_doctor(conn: sqlite3.Connection,
                           hospital_code: str,
                           doctor: str,
                           date: str) -> List[str]:
    cur = conn.execute("""SELECT slot_time FROM bookings
                          WHERE hospital_code=? AND doctor_name=? AND date=?
                          ORDER BY slot_time""",
                       (hospital_code, doctor, date))
    return [r[0] for r in cur.fetchall()]


def _compress_free_slots(free_slots: List[str]) -> List[Dict[str, str]]:
    """Group contiguous free slot starts into ranges (end is last slot start, as yêu cầu ví dụ)."""
    if not free_slots:
        return []
    # ensure sorted by time
    def to_minutes(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    free_slots = sorted(free_slots, key=to_minutes)
    ranges: List[Tuple[str, str]] = []
    start = prev = free_slots[0]
    prev_min = to_minutes(prev)
    for s in free_slots[1:]:
        cur_min = to_minutes(s)
        if cur_min - prev_min == SLOT_MINUTES:
            prev = s
            prev_min = cur_min
            continue
        # break
        ranges.append((start, prev))
        start = prev = s
        prev_min = cur_min
    ranges.append((start, prev))
    return [{"start": a, "end": b} for a, b in ranges]


def _compute_availability(booked: List[str]) -> Dict[str, Any]:
    booked_set = set(booked)
    free_slots = [s for s in ALL_SLOTS if s not in booked_set]
    return {
        "booked": booked,
        "free_slots": free_slots,
        "free_intervals": _compress_free_slots(free_slots)
    }


def get_overview(hospital_code: str,
                 departments: Iterable[str],
                 date: Optional[str] = None) -> Dict[str, Any]:
    if not date:
        date = dt.date.today().isoformat()
    doctors_by_dep = get_doctors_for_departments(hospital_code, departments)
    overview: Dict[str, Any] = {
        "hospital_code": hospital_code,
        "date": date,
        "departments": [],
        "slot_window": {
            "start": WORK_START.strftime("%H:%M"),
            "end": WORK_END.strftime("%H:%M"),
            "slot_minutes": SLOT_MINUTES,
            "all_slots": ALL_SLOTS,
        }
    }
    with _connect() as conn:
        for dep, doctors in doctors_by_dep.items():
            dep_entry = {"department": dep, "doctors": []}
            for doc in doctors:
                booked = _get_booked_for_doctor(conn, hospital_code, doc, date)
                dep_entry["doctors"].append({
                    "name": doc,
                    "availability": _compute_availability(booked)
                })
            overview["departments"].append(dep_entry)
    return overview


def book_slot(hospital_code: str,
              department: str,
              doctor_name: str,
              date: str,
              slot_time: str,
              *,
              department_code: Optional[str] = None) -> Tuple[bool, str]:
    """Book a slot; prefers department_code verification when provided.

    Backward compatible: if department_code not given, falls back to legacy name-based check.
    """
    slot_time = slot_time.strip()
    if slot_time not in ALLOWED_SLOT_SET:
        return False, "invalid_slot_time"
    dep_norm = _normalize_department(department)
    # Verification
    ok_doctor = False
    if department_code:
        code_map = get_doctors_for_department_codes(hospital_code, [department_code])
        docs_list = code_map.get(department_code) or []
        ok_doctor = doctor_name in docs_list
    if not ok_doctor:
        docs_map = get_doctors_for_departments(hospital_code, [dep_norm])
        if dep_norm in docs_map and doctor_name in docs_map[dep_norm]:
            ok_doctor = True
    if not ok_doctor:
        return False, "doctor_not_found_in_department"
    with _DB_LOCK:
        with _connect() as c:
            try:
                c.execute("""INSERT INTO bookings(hospital_code, department, doctor_name, date, slot_time, department_code)
                             VALUES (?,?,?,?,?,?)""",
                          (hospital_code, dep_norm, doctor_name, date, slot_time, department_code))
                c.commit()
                _bump_bookings_version()
                return True, "booked"
            except sqlite3.IntegrityError:
                return False, "already_booked"
            except Exception as e:
                _dlog(f"Insert error: {e}")
                return False, "db_error"

# ---------------- Soft Hold Mechanism ----------------
# A lightweight temporary reservation before final confirmation.
# Table schema (created lazily): holds(hospital_code, department, doctor_name, date, slot_time, session_id, held_at, expires_at, PRIMARY KEY(...))

def _ensure_holds_table():
    with _connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS holds (
            hospital_code TEXT,
            department TEXT,
            doctor_name TEXT,
            date TEXT,
            slot_time TEXT,
            session_id TEXT,
            held_at REAL,
            expires_at REAL,
            department_code TEXT, -- NEW (nullable)
            PRIMARY KEY (hospital_code, department, doctor_name, date, slot_time)
        )""")
        # Attempt ALTER for legacy table missing department_code
        try:
            c.execute("ALTER TABLE holds ADD COLUMN department_code TEXT")
        except Exception:
            pass
        c.commit()

def create_hold(hospital_code: str,
                department: str,
                doctor_name: str,
                date: str,
                slot_time: str,
                session_id: str,
                ttl_seconds: int = 300,
                *,
                department_code: Optional[str] = None) -> Tuple[bool, str]:
    """Attempt soft hold; returns (ok,message). Uses department_code if provided for validation."""
    slot_time = slot_time.strip()
    if slot_time not in ALLOWED_SLOT_SET:
        return False, "invalid_slot_time"
    dep_norm = _normalize_department(department)
    # doctor existence
    valid = False
    if department_code:
        code_map = get_doctors_for_department_codes(hospital_code, [department_code])
        if doctor_name in (code_map.get(department_code) or []):
            valid = True
    if not valid:
        docs_map = get_doctors_for_departments(hospital_code, [dep_norm])
        if dep_norm in docs_map and doctor_name in docs_map[dep_norm]:
            valid = True
    if not valid:
        return False, "doctor_not_found_in_department"
    now = time.time()
    exp = now + max(60, ttl_seconds)
    with _DB_LOCK:
        _ensure_holds_table()
        with _connect() as c:
            # clean expired first
            try:
                c.execute("DELETE FROM holds WHERE expires_at < ?", (now,))
            except Exception:
                pass
            # check already booked
            cur = c.execute("""SELECT 1 FROM bookings WHERE hospital_code=? AND department=? AND doctor_name=? AND date=? AND slot_time=?""",
                             (hospital_code, dep_norm, doctor_name, date, slot_time))
            if cur.fetchone():
                return False, "already_booked"
            # check existing active hold
            cur = c.execute("""SELECT session_id FROM holds WHERE hospital_code=? AND department=? AND doctor_name=? AND date=? AND slot_time=?""",
                             (hospital_code, dep_norm, doctor_name, date, slot_time))
            row = cur.fetchone()
            if row and row[0] != session_id:
                return False, "held_by_other"
            try:
                c.execute("""INSERT OR REPLACE INTO holds(hospital_code, department, doctor_name, date, slot_time, session_id, held_at, expires_at, department_code)
                             VALUES (?,?,?,?,?,?,?,?,?)""",
                          (hospital_code, dep_norm, doctor_name, date, slot_time, session_id, now, exp, department_code))
                c.commit()
                return True, "held"
            except Exception as e:
                _dlog(f"Hold insert error: {e}")
                return False, "db_error"

def cancel_holds_for_session(session_id: str):
    if not session_id:
        return
    with _DB_LOCK:
        _ensure_holds_table()
        with _connect() as c:
            try:
                c.execute("DELETE FROM holds WHERE session_id=?", (session_id,))
                c.commit()
            except Exception as e:
                _dlog(f"cancel_holds_for_session error: {e}")

def promote_hold_to_booking(session_id: str,
                            hospital_code: str,
                            department: str,
                            doctor_name: str,
                            date: str,
                            slot_time: str,
                            *,
                            department_code: Optional[str] = None) -> Tuple[bool, str]:
    slot_time = slot_time.strip()
    dep_norm = _normalize_department(department)
    with _DB_LOCK:
        _ensure_holds_table()
        with _connect() as c:
            now = time.time()
            # verify hold valid
            cur = c.execute("""SELECT session_id, expires_at, department_code FROM holds WHERE hospital_code=? AND department=? AND doctor_name=? AND date=? AND slot_time=?""",
                             (hospital_code, dep_norm, doctor_name, date, slot_time))
            row = cur.fetchone()
            if not row:
                return False, "no_hold"
            hold_sess, exp, hold_code = row
            if hold_sess != session_id:
                return False, "held_by_other"
            if exp < now:
                # expired; delete and abort
                try:
                    c.execute("DELETE FROM holds WHERE hospital_code=? AND department=? AND doctor_name=? AND date=? AND slot_time=?",
                              (hospital_code, dep_norm, doctor_name, date, slot_time))
                    c.commit()
                except Exception:
                    pass
                return False, "hold_expired"
            final_code = department_code or hold_code
            # attempt booking
            try:
                c.execute("""INSERT INTO bookings(hospital_code, department, doctor_name, date, slot_time, department_code)
                             VALUES (?,?,?,?,?,?)""",
                          (hospital_code, dep_norm, doctor_name, date, slot_time, final_code))
                # remove hold
                c.execute("DELETE FROM holds WHERE hospital_code=? AND department=? AND doctor_name=? AND date=? AND slot_time=?",
                          (hospital_code, dep_norm, doctor_name, date, slot_time))
                c.commit()
                _bump_bookings_version()
                return True, "booked"
            except sqlite3.IntegrityError:
                return False, "already_booked"
            except Exception as e:
                _dlog(f"promote_hold error: {e}")
                return False, "db_error"


__all__ = [
    "generate_slots",
    "get_doctors_for_departments",
    "get_doctors_for_department_codes",
    "get_overview",
    "book_slot",
    "create_hold",
    "cancel_holds_for_session",
    "promote_hold_to_booking",
    "init_db",
    "ALL_SLOTS",
    "list_hospitals",
]

# ---------------- Code-centric snapshots & backfill ----------------
def get_bookings_snapshot_by_codes(hospital_code: str, department_codes: Iterable[str], date: Optional[str] = None) -> Dict[str, Any]:
    """Return bookings keyed strictly by department_code (no legacy name fallback).

    Rationale: We are fully code-centric now. Any legacy rows with NULL department_code
    are ignored (they should be migrated via backfill_department_codes beforehand).

    Response shape:
      { hospital_code, date, version, bookings: { CODE: { doctor_name: [slot,...] } }, legacy_rows_ignored?: int }
    """
    if not date:
        date = dt.date.today().isoformat()
    codes = [c for c in department_codes if c]
    version = get_bookings_version()
    if not codes:
        return {"hospital_code": hospital_code, "date": date, "version": version, "bookings": {}}
    result: Dict[str, Any] = {"hospital_code": hospital_code, "date": date, "version": version, "bookings": {}}
    placeholders = ",".join(["?"] * len(codes))
    with _connect() as conn:
        # Count legacy rows (NULL department_code) for visibility; do not attempt name mapping.
        try:
            legacy_count = conn.execute(
                "SELECT COUNT(*) FROM bookings WHERE hospital_code=? AND date=? AND department_code IS NULL",
                (hospital_code, date)
            ).fetchone()[0]
            if legacy_count:
                result["legacy_rows_ignored"] = int(legacy_count)
        except Exception:
            pass
        try:
            cur = conn.execute(
                f"SELECT department_code, doctor_name, slot_time FROM bookings "
                f"WHERE hospital_code=? AND date=? AND department_code IN ({placeholders}) "
                f"ORDER BY department_code, doctor_name, slot_time",
                [hospital_code, date, *codes]
            )
            for d_code, doctor, slot in cur.fetchall():
                if d_code in codes:
                    result["bookings"].setdefault(d_code, {}).setdefault(doctor, []).append(slot)
        except Exception as e:
            _dlog(f"get_bookings_snapshot_by_codes error: {e}")
    return result

def backfill_department_codes(hospital_code: Optional[str] = None) -> Dict[str, Any]:
    """Populate department_code for legacy booking rows where NULL.

    Strategy: for each hospital (or specified one) build mapping normalized(department_display_name)->code
    then UPDATE rows with department_code IS NULL.
    Returns summary counts.
    """
    targets: List[str] = []
    if hospital_code:
        targets = [hospital_code]
    else:
        # Derive from existing bookings distinct hospital_code
        with _connect() as c:
            try:
                rows = c.execute("SELECT DISTINCT hospital_code FROM bookings").fetchall()
                targets = [r[0] for r in rows if r and r[0]]
            except Exception:
                targets = []
    summary = {"updated": 0, "hospitals": {}}
    with _DB_LOCK:
        with _connect() as c:
            for h in targets:
                meta = get_hospital_meta(h) or {}
                by_code = meta.get("departments_by_code") or {}
                if not by_code:
                    continue
                name_map = { _normalize_department(info.get("name") or code): code for code, info in by_code.items() }
                updated_h = 0
                for norm_name, code in name_map.items():
                    try:
                        res = c.execute(
                            "UPDATE bookings SET department_code=? WHERE hospital_code=? AND department_code IS NULL AND department=?",
                            (code, h, norm_name)
                        )
                        updated_h += res.rowcount or 0
                    except Exception:
                        continue
                if updated_h:
                    summary["hospitals"][h] = updated_h
                    summary["updated"] += updated_h
            try:
                c.commit()
            except Exception:
                pass
    return summary

__all__.extend([
    "get_bookings_snapshot_by_codes",
    "backfill_department_codes",
])

