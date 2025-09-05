# storage.py
import sqlite3, hashlib, os, json, time
from typing import Dict, List

DB_PATH = os.getenv("KIOSK_DB", "kiosk.db")
OUT_DIR = os.getenv("KIOSK_OUT", "out")
# SAVE_VISIT_FILES modes:
#  - "always" (default current behavior)
#  - "final"  (only when explicitly flagged final=True)
#  - "none"   (never write files, still store DB)
SAVE_MODE = os.getenv("SAVE_VISIT_FILES", "always").lower()

def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = _conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS customers(
      id TEXT PRIMARY KEY,
      name TEXT,
      phone TEXT UNIQUE,
      facts TEXT,
      last_summary TEXT
    );
    CREATE TABLE IF NOT EXISTS visits(
      visit_id TEXT PRIMARY KEY,
      customer_id TEXT,
      created_at TEXT,
      payload_json TEXT,
      summary TEXT,
      facts_extracted TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_visits_customer_created ON visits(customer_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
    """)
    conn.commit()
    conn.close()

def _normalize_phone(p: str) -> str:
    return "".join(ch for ch in (p or "") if ch.isdigit()) or "unknown"

def _stable_id_from_phone(phone: str) -> str:
    norm = _normalize_phone(phone)
    h = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10]
    return f"CUS-{h}"

def get_or_create_customer(name: str, phone: str):
    phone = _normalize_phone(phone)
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM customers WHERE phone=?", (phone,))
    row = cur.fetchone()
    if row:
        cid = row[0]
        conn.execute("UPDATE customers SET name=? WHERE id=?", (name or "", cid))
        conn.commit(); conn.close()
        return cid, False
    cid = _stable_id_from_phone(phone)
    conn.execute("INSERT OR REPLACE INTO customers(id,name,phone,facts,last_summary) VALUES(?,?,?,?,?)",
                 (cid, name or "", phone, "", ""))
    conn.commit(); conn.close()
    return cid, True

def get_customer_by_phone(phone: str):
    """Lookup existing customer id by phone without creating a new record. Returns id or None."""
    phone = _normalize_phone(phone)
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM customers WHERE phone=?", (phone,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def save_visit(customer_id: str, payload: Dict, *, final: bool = True, summary: str = "", facts: str = "") -> str:
    visit_id = f"VIS-{int(time.time()*1000)}"
    conn = _conn()
    conn.execute("INSERT INTO visits(visit_id,customer_id,created_at,payload_json,summary,facts_extracted) VALUES(?,?,datetime('now'),?,?,?)",
                 (visit_id, customer_id, json.dumps(payload, ensure_ascii=False), summary, facts))
    conn.commit(); conn.close()
    write_files = False
    if SAVE_MODE == "always":
        write_files = True
    elif SAVE_MODE == "final" and final:
        write_files = True
    elif SAVE_MODE == "none":
        write_files = False

    if write_files:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, f"{visit_id}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        with open(os.path.join(OUT_DIR, f"{visit_id}.txt"), "w", encoding="utf-8") as f:
            f.write(pretty_txt(payload))
    return visit_id

def get_recent_visits(customer_id: str, limit: int = 5):
    """Return recent visit rows (newest first) for a customer as list of dicts with parsed payload."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT visit_id, created_at, payload_json, summary, facts_extracted FROM visits WHERE customer_id=? ORDER BY created_at DESC LIMIT ?", (customer_id, limit))
    rows = []
    for vid, created_at, payload_json, summary, facts in cur.fetchall():
        try:
            payload = json.loads(payload_json)
        except Exception:
            payload = {"raw": payload_json}
        rows.append({
            "visit_id": vid, 
            "created_at": created_at, 
            "payload": payload,
            "summary": summary or "",
            "facts": facts or ""
        })
    conn.close()
    return rows

def get_customer_facts_summary(customer_id: str):
    """Get accumulated facts and last summary for a customer."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT facts, last_summary FROM customers WHERE id=?", (customer_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"facts": row[0] or "", "last_summary": row[1] or ""}
    return {"facts": "", "last_summary": ""}

def update_customer_facts_summary(customer_id: str, facts: str, summary: str):
    """Update customer's accumulated facts and last summary."""
    conn = _conn()
    conn.execute("UPDATE customers SET facts=?, last_summary=? WHERE id=?", (facts, summary, customer_id))
    conn.commit()
    conn.close()

def find_visit_by_booking(hospital_code: str, date: str, doctor_name: str, slot_time: str):
    """Search latest visit whose payload.booking matches booking identifiers.

    We store booking JSON inside payload_json; do a LIKE filter then parse & verify to reduce scan cost.
    Returns first matched visit dict or None.
    """
    # Quick LIKE pattern include slot_time and doctor_name
    conn = _conn(); cur = conn.cursor()
    pattern_slot = f'%"slot_time": "{slot_time}"%'
    pattern_doc = f'%"doctor_name": "{doctor_name}"%'
    # Broad filter: most recent first
    cur.execute("SELECT visit_id, created_at, payload_json, summary, facts_extracted FROM visits WHERE payload_json LIKE ? AND payload_json LIKE ? ORDER BY created_at DESC LIMIT 15", (pattern_slot, pattern_doc))
    rows = cur.fetchall(); conn.close()
    import json as _json
    for vid, created_at, payload_json, summary, facts in rows:
        try:
            payload = _json.loads(payload_json)
        except Exception:
            continue
        # Direct booking_index check
        b_index = payload.get('booking_index') if isinstance(payload.get('booking_index'), dict) else None
        if b_index:
            if (
                (not hospital_code or b_index.get('hospital_code') == hospital_code) and
                (not date or b_index.get('date') == date) and
                b_index.get('doctor_name') == doctor_name and
                b_index.get('slot_time') == slot_time
            ):
                return {
                    'visit_id': vid,
                    'created_at': created_at,
                    'payload': payload,
                    'summary': summary or '',
                    'facts': facts or ''
                }
        b = payload.get('booking') or {}
        if not isinstance(b, dict):
            b = {}
        chosen = b.get('chosen') if isinstance(b.get('chosen'), dict) else {}
        # Gather possible fields
        doc_candidates = [
            b.get('doctor_name'), chosen.get('doctor_name'), payload.get('doctor_name')
        ]
        slot_candidates = [
            b.get('slot_time'), chosen.get('slot_time'), b.get('appointment_time'), payload.get('appointment_time'), payload.get('slot_time')
        ]
        hosp_candidates = [
            b.get('hospital_code'), chosen.get('hospital_code'), payload.get('hospital_code')
        ]
        date_candidates = [
            b.get('date'), chosen.get('date'), payload.get('date'), created_at.split(' ')[0] if created_at else None
        ]
        # Normalize comparison (exact for now; could lower() later)
        match_doc = any(d == doctor_name for d in doc_candidates if d)
        match_slot = any(s == slot_time for s in slot_candidates if s)
        match_hosp = any(h == hospital_code for h in hosp_candidates if h) or not any(hosp_candidates)  # allow missing
        match_date = any(dt == date for dt in date_candidates if dt) or not any(date_candidates)       # allow missing
        if match_doc and match_slot and match_hosp and match_date:
            return {
                'visit_id': vid,
                'created_at': created_at,
                'payload': payload,
                'summary': summary or '',
                'facts': facts or ''
            }
    return None

def build_personal_context(customer_id: str = None, visits: List[dict] = None) -> str:
    """Return structured personal history blocks (WITHOUT wrapper tags).

    Structure (only emit sections that have data):

    [PATIENT_FACTS]\n...stable facts...\n[/PATIENT_FACTS]
    [LAST_SUMMARY]\n...previous visit summary...\n[/LAST_SUMMARY]
    [VISIT_HISTORY]\n- yyyy-mm-dd: brief line\n...\n[/VISIT_HISTORY]

    This string will later be wrapped by gemini_kiosk with [PERSONAL_HISTORY] ... [/PERSONAL_HISTORY].
    """
    if not customer_id:
        # fallback minimal (legacy path if only visits provided)
        if visits:
            lines = ["[VISIT_HISTORY]"]
            for v in visits[:3]:
                p = v.get("payload", {})
                symptoms = []
                for s in p.get("symptoms", []) or []:
                    nm = s.get("name") or s.get("symptom") or "?"
                    sev = s.get("severity") or s.get("level") or "?"
                    symptoms.append(f"{nm}({sev})")
                sym_txt = ", ".join(symptoms) if symptoms else "(không rõ)"
                diag_list = p.get("tentative_diagnoses") or []
                diag_txt = ", ".join(diag_list) if diag_list else "(chưa ghi)"
                appt = p.get("appointment_time") or p.get("slot_time") or "(chưa rõ)"
                lines.append(f"- {v.get('created_at','')}: {sym_txt}; chẩn đoán: {diag_txt}; lịch: {appt}")
            lines.append("[/VISIT_HISTORY]")
            return "\n".join(lines)
        return ""

    facts_data = get_customer_facts_summary(customer_id)
    if visits is None:
        visits = get_recent_visits(customer_id, limit=5)

    sections: List[str] = []

    # FACTS
    facts_txt = (facts_data.get("facts") or "").strip()
    if facts_txt:
        sections.append(f"[PATIENT_FACTS]\n{facts_txt}\n[/PATIENT_FACTS]")

    # LAST SUMMARY (take newest non-empty summary that is not today duplicate of last_summary)
    last_summary = (facts_data.get("last_summary") or "").strip()
    if not last_summary:
        # fallback: derive from latest visit summary
        for v in visits:
            if v.get("summary"):
                last_summary = v["summary"].strip()
                break
    if last_summary:
        sections.append(f"[LAST_SUMMARY]\n{last_summary}\n[/LAST_SUMMARY]")

    # (Tạm thời bỏ VISIT_HISTORY để giảm nhiễu, chỉ giữ FACTS + LAST_SUMMARY giúp model nhớ đúng)

    return "\n\n".join(sections)

def pretty_txt(p: Dict) -> str:
    def g(k, d="(không rõ)"): return p.get(k) or d
    lines = [
        "=== PHIẾU KẾT QUẢ THĂM KHÁM ===",
        f"Mã KH: {g('customer_id')}",
        f"Họ tên: {g('patient_name')}",
        f"SĐT: {g('phone')}",
        f"Bác sĩ: {g('doctor_name')}",
        f"Lịch hẹn: {g('appointment_time')}",
        "", "Triệu chứng:",
    ]
    for s in p.get("symptoms", []):
        lines.append(f" - {s.get('name','?')} | mức độ: {s.get('severity','?')} | thời gian: {s.get('duration','?')}")
    lines += [
        "", f"Chẩn đoán sơ bộ: {', '.join(p.get('tentative_diagnoses', []) or ['(không rõ)'])}",
        "Xét nghiệm khuyến nghị:",
    ]
    for t in p.get("tests_recommended", []): lines.append(f" - {t}")
    lines += ["Thuốc/điều trị đề nghị:"]
    for m in p.get("medications_advised", []): lines.append(f" - {m}")
    lines += ["", f"Chế độ ăn/kiêng: {g('diet_notes')}",
              f"Dặn dò: {g('follow_up', 'Tái khám khi bất thường')}","", "Xin cảm ơn quý khách!"]
    return "\n".join(lines)
