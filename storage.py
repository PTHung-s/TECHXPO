# storage.py
import sqlite3, hashlib, os, json, time
from typing import Dict

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
      phone TEXT UNIQUE
    );
    CREATE TABLE IF NOT EXISTS visits(
      visit_id TEXT PRIMARY KEY,
      customer_id TEXT,
      created_at TEXT,
      payload_json TEXT
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
    conn.execute("INSERT OR REPLACE INTO customers(id,name,phone) VALUES(?,?,?)",
                 (cid, name or "", phone))
    conn.commit(); conn.close()
    return cid, True

def save_visit(customer_id: str, payload: Dict, *, final: bool = True) -> str:
    visit_id = f"VIS-{int(time.time()*1000)}"
    conn = _conn()
    conn.execute("INSERT INTO visits(visit_id,customer_id,created_at,payload_json) VALUES(?,?,datetime('now'),?)",
                 (visit_id, customer_id, json.dumps(payload, ensure_ascii=False)))
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
