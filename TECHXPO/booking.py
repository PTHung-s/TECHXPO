# booking.py
# -*- coding: utf-8 -*-
"""
Đặt lịch khám:
- Đọc file dữ liệu (JSON/YAML) về khoa, bác sĩ, lịch rảnh.
- Dùng Gemini để chọn khoa, bác sĩ, slot.
- Trả về JSON + câu thoại ngắn để Talker phát lại.

ENV: GOOGLE_API_KEY (hoặc GEMINI_API_KEY)
"""

import os, json, re, datetime, unicodedata
from typing import Dict, Any, Optional, Tuple, List

from dotenv import load_dotenv
load_dotenv(".env.local") or load_dotenv()

from pydantic import BaseModel, Field

from google import genai as google_genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

try:
    import yaml
except Exception:
    yaml = None


# ---------------- IO helpers ----------------
def _load_clinic_data(path: str) -> Dict[str, Any]:
    ap = os.path.abspath(path)
    if not os.path.exists(ap):
        raise FileNotFoundError(f"Không thấy file dữ liệu lịch: {ap}")
    with open(ap, "r", encoding="utf-8") as f:
        raw = f.read()
    ext = os.path.splitext(ap)[1].lower()
    data = yaml.safe_load(raw) if ext in [".yaml", ".yml"] and yaml is not None else json.loads(raw)
    base_code = os.path.splitext(os.path.basename(ap))[0]
    data["hospital_name"] = data.get("hospital_name") or base_code
    data["hospital_code"] = base_code  # dùng để map ảnh
    return data

def _merge_multi(data_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"hospitals": [d for d in data_list if isinstance(d, dict)]}


def _pick_api_key() -> str:
    return os.getenv("GOOGLE_API_KEY2")


# ---------------- Debug helpers ----------------
def _json_dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=2)

## (Loại bỏ hàm debug dump chi tiết để làm gọn)


# ---------------- JSON repair helpers ----------------
_BRACE_RE = re.compile(r"\{[\s\S]*")

def _brace_balance(s: str) -> Tuple[int, int]:
    """Đếm số '{' và '}' trong chuỗi (bỏ qua phần trong string đơn giản)."""
    opens = s.count("{")
    closes = s.count("}")
    return opens, closes

def _fix_truncated_json(text: str) -> str:
    """
    Vá JSON bị cắt: nếu số '{' > '}', thêm đủ '}' vào cuối.
    Đồng thời cắt về từ dấu '{' đầu tiên để loại bỏ rác phía trước nếu có.
    """
    if not text:
        return text
    # cắt từ '{' đầu tiên
    m = _BRACE_RE.search(text)
    if m:
        text = m.group(0)
    text = text.strip()
    opens, closes = _brace_balance(text)
    if opens > closes:
        missing = opens - closes
        print(f"[booking] WARN: JSON thiếu {missing} dấu '}}' -> tự vá")
        text = text + ("}" * missing)
    return text

def _extract_json(text: str) -> Dict[str, Any]:
    """Fallback xưa cũ: tìm block {...} đầu tiên và parse (đa phần không cần nếu đã structured output)."""
    if not text:
        return {"error": "no_json_found", "raw": ""}
    text = _fix_truncated_json(text)
    try:
        return json.loads(text)
    except Exception as e:
        return {"error": f"json_parse_error: {e}", "raw": text}


def _first_json_like_from_parts(resp) -> Optional[str]:
    """Thử vớt JSON từ parts nếu .text hỏng/trống."""
    try:
        c = (resp.candidates or [None])[0]
        if not c or not getattr(c, "content", None):
            return None
        for p in c.content.parts or []:
            t = getattr(p, "text", None)
            if isinstance(t, str) and "{" in t:
                return _fix_truncated_json(t)
    except Exception:
        pass
    return None



# ---------------- Structured Output schema (Pydantic) ----------------
class BookingOption(BaseModel):
    # Vẫn giữ backward compatibility: hospital & department (name) có thể có
    hospital: Optional[str] = Field(None, description="Tên bệnh viện (tùy chọn, có thể bỏ nếu chỉ dùng mã)")
    hospital_code: Optional[str] = Field(None, description="Mã bệnh viện")
    department: Optional[str] = Field(None, description="Tên khoa (hiển thị)")
    department_code: Optional[str] = Field(None, description="Mã khoa")
    doctor_name: str = Field(..., description="Tên bác sĩ")
    slot_time: str = Field(..., description="YYYY-MM-DD HH:MM")
    room: Optional[str] = None
    score: Optional[float] = None

class BookingResult(BaseModel):
    options: List[BookingOption] = Field(..., description="Danh sách tối đa 3 lựa chọn")
    chosen: BookingOption = Field(..., description="Lựa chọn cuối cùng agent chọn")
    rationale: Optional[str] = None
    patient_name: Optional[str] = None
    phone: Optional[str] = None
    speak_text: Optional[str] = None


SYSTEM = (
    "Bạn là trợ lý gợi ý lịch khám đa bệnh viện. Tạo <=3 options (đa dạng nếu có thể), mỗi option hợp lệ từ dữ liệu. Chọn 1 final vào 'chosen'. Không bịa. Không thêm text ngoài JSON."
)

# ---------------- Debug flag ----------------
BOOKING_DEBUG = os.getenv("BOOKING_DEBUG", "0") == "1"

def _blog(msg: str):
    if BOOKING_DEBUG:
        print(f"[booking_debug] {msg}")

def _resolve_hospital_image(hospital_code: str) -> Optional[str]:
    """Return relative path to hospital image if exists. Looks for PNG with code upper.
    Example: BV_TAMANH -> web/public/images/BV_TAMANH.png
    Returns just filename so frontend can prefix path, or None if not found."""
    if not hospital_code:
        return None
    fname = f"{hospital_code.upper()}.png"
    base = os.path.abspath(os.getcwd())
    rel_dir = os.path.join(base, "web", "public", "images")
    path = os.path.join(rel_dir, fname)
    if os.path.isfile(path):
        return fname
    return None

# ---------------- Stage 1 (Department code selection – simplified) ----------------
STAGE1_SYSTEM = (
    "Bạn là trợ lý chọn khoa. Dựa trên hội thoại, chọn 1-5 MÃ KHOA (department_code) phù hợp nhất. "
    "CHỈ DÙNG MÃ (không thêm tên vào mảng kết quả). Trả JSON: {\"codes\":[\"CODE1\",...]} (1-5). \n"
    "Không bịa code không có trong danh sách. Không thêm text ngoài JSON."
)

# Regex salvage cho JSON bị cắt
_CODE_LIST_RE = re.compile(r'"codes"\s*:\s*\[(.*?)\]', re.IGNORECASE | re.DOTALL)
_CODE_ITEM_RE = re.compile(r'"([A-Z0-9]{2,10})"')

def _salvage_codes(raw: str, valid_codes: set, limit: int = 5) -> List[str]:
    picked: List[str] = []
    if not raw:
        return picked
    m = _CODE_LIST_RE.search(raw)
    segment = m.group(1) if m else raw
    for c in _CODE_ITEM_RE.findall(segment):
        if c in valid_codes and c not in picked:
            picked.append(c)
        if len(picked) >= limit:
            break
    if picked:
        return picked
    # Fallback: loose scan
    for c in re.findall(r'\b([A-Z0-9]{3,6})\b', raw):
        if c in valid_codes and c not in picked:
            picked.append(c)
        if len(picked) >= limit:
            break
    return picked

def _load_departments_index(path: Optional[str] = None) -> Dict[str, List[Dict[str, str]]]:
    """Load departments_index.json.

    Supports two shapes:
      Legacy: { "BV_CODE": ["Dept A", "Dept B", ...] }
      New   : { "BV_CODE": [ {"code": "ABC", "name": "Dept A"}, ... ] }

    Returns normalized: { hospital_code: [ {code, name}, ... ] }
    """
    candidates = []
    if path:
        candidates.append(path)
    base = os.path.abspath(os.getcwd())
    # Prefer the generated aggregate (reflects actual codes in converted hospital JSON files)
    for rel in [
        "Booking_data/departments_index.generated.json",
        "Booking_Data/departments_index.generated.json",
        "Booking_data/departments_index.json",
        "Booking_Data/departments_index.json",
        "./departments_index.generated.json",
        "./departments_index.json",
    ]:
        candidates.append(os.path.join(base, rel))
    seen = set()
    for p in candidates:
        if not p or p in seen:
            continue
        seen.add(p)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if BOOKING_DEBUG:
                    _blog(f"departments_index loaded from {p}")
                norm: Dict[str, List[Dict[str, str]]] = {}
                if isinstance(raw, dict):
                    for hosp, arr in raw.items():
                        items: List[Dict[str, str]] = []
                        if isinstance(arr, list):
                            for entry in arr:
                                if isinstance(entry, dict) and "name" in entry:
                                    code = entry.get("code") or entry.get("id") or entry.get("code_id")
                                    name = str(entry.get("name"))
                                    if not code:
                                        # derive code fallback
                                        code = _derive_code_from_name(name)
                                    items.append({"code": str(code), "name": name})
                                elif isinstance(entry, str):
                                    name = entry
                                    items.append({"code": _derive_code_from_name(name), "name": name})
                        if items:
                            norm[hosp] = items
                return norm
            except Exception:
                continue
    return {}

def _strip_accents(s: str) -> str:  # legacy kept for potential fallback usage elsewhere
    s_norm = unicodedata.normalize('NFD', s or '')
    return ''.join(ch for ch in s_norm if unicodedata.category(ch) != 'Mn')

def _clean_display_name(s: str) -> str:
    """Make a user-facing department name: remove hard newlines, collapse spaces, recompose accents (NFC)."""
    if not s:
        return s
    s2 = s.replace('\r', ' ').replace('\n', ' ')
    s2 = re.sub(r"\s+", " ", s2).strip()
    try:
        s2 = unicodedata.normalize('NFC', s2)
    except Exception:
        pass
    return s2

def _derive_code_from_name(name: str) -> str:
    # Local accent strip (independent of scripts)
    s_norm = unicodedata.normalize('NFD', name or '')
    base = ''.join(ch for ch in s_norm if unicodedata.category(ch) != 'Mn').upper()
    parts = [p for p in re.split(r"[^A-Z0-9]+", base) if p]
    if not parts:
        return "DEPT"
    letters = ''.join(p[0] for p in parts)[:6]
    if len(letters) < 3:
        letters = (letters + ''.join(parts))[:3]
    return letters or 'DEPT'

def _stage1_select_codes(client, model: str, history_text: str, departments_index: Dict[str, List[Dict[str, str]]]) -> List[str]:
    if not departments_index:
        _blog("Stage1 skip: no departments_index"); return []
    code_name: Dict[str, str] = {}
    for arr in departments_index.values():
        for it in arr:
            c = it.get("code"); n = it.get("name")
            if c and n and c not in code_name:
                code_name[c] = n
    valid = set(code_name.keys())
    lines = [f"{c} - {code_name[c]}" for c in sorted(valid)]

    print(lines)
    prompt = (
        "Bạn là một người lựa chọn khoa thăm khám bước bước đầu cho bệnh nhân, dựa vào các dấu hiệu bệnh, yêu cầu, và lịch sử hội thoại. Kết hợp với các code và tên Khoa thăm khám để chọn ra 1-3 khoa phù hợp. Chú ý phải trả ra mã Khoa thăm khám để người tiếp theo có thể dùng mã đó để xử lí tiếp."
        "# DANH SÁCH MÃ KHOA\n" + "\n".join(lines) + "\n\n" +
        "# HỘI THOẠI\n" + history_text + "\n\n" +
        "# YÊU CẦU\nTrả JSON: {\"codes\":[""MÃ1"",...]} (1-3). Không bịa. Chỉ JSON."
        "Chắc chắn có tên và mã khoa phù hợp với nhu cầu của bệnh nhân, thật chi tiết nha ví dụ ho, sổ mũi thì gửi mã khoa tai mũi họng. Bên cạnh đó tìm thật kĩ để trả ra mã khoa thăm khám phù hợp để tôi có thể dò mã tìm khoa khám cho bệnh nhân"
    )

    def _call() -> str:
        r = client.models.generate_content(
            model=model,
            contents=[genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=prompt)])],
            config=genai_types.GenerateContentConfig(
                system_instruction=STAGE1_SYSTEM,
                temperature=0.0,
                max_output_tokens=456,
                response_mime_type="application/json",
            ),
        )
        print(r.text)
        return r.text or ""

    attempts = 0
    picked: List[str] = []
    raw = ""
    while attempts < 2 and not picked:
        attempts += 1
        try:
            raw = _call()
            if raw:
                _blog(f"Stage1 raw text len={len(raw)} preview={raw[:150].replace(chr(10),' ')}")
            if BOOKING_DEBUG:
                print("[stage1 raw full]\n" + raw)
            data = {}
            
            try:
                if raw:
                    data = json.loads(_fix_truncated_json(raw))
            except Exception:
                data = {}
            for key in ("codes", "selected_codes", "selected"):
                arr = data.get(key)
                if isinstance(arr, list):
                    for c in arr:
                        if isinstance(c, str) and c in valid and c not in picked:
                            picked.append(c)
                        if len(picked) >= 5:
                            break
                if picked:
                    break
            if not picked:
                picked = _salvage_codes(raw, valid)
                if picked:
                    _blog("Stage1 salvage")
        except Exception as e:
            _blog(f"Stage1 error attempt{attempts}: {e}")
    _blog(f"Stage1 parsed codes={picked} attempts={attempts}")
    return picked

# ---------------- Stage 2 schedule aggregation ----------------
def _gather_schedule(selected_department_codes: List[str], departments_index: Dict[str, List[Dict[str, str]]], date_str: str) -> Dict[str, Any]:
    """Aggregate schedule strictly by department_code, excluding active holds.

    Data sources:
      - Hospital meta departments_by_code -> doctor roster
      - Blocked snapshot by codes -> booked + active holds (Dashboard.schedule_logic)

    Return shape (unchanged for Stage2):
      { date, slots, hospitals: [ { hospital_code, departments: [ { department_code, department_name, doctors:[{name,free_slots}] } ] } ], selected_department_codes }
    """
    try:
        from Dashboard.schedule_logic import get_hospital_meta, get_blocked_snapshot_by_codes, ALL_SLOTS
    except Exception as e:
        print("[stage2] import schedule_logic failed:", e)
        return {"error": "schedule_logic_import_failed"}

    # Build quick lookup code->canonical display name (first seen in departments_index)
    code_display: Dict[str, str] = {}
    for _h, arr in (departments_index or {}).items():
        for entry in arr or []:
            c = entry.get("code"); n = entry.get("name")
            if c and n and c in selected_department_codes and c not in code_display:
                code_display[c] = _clean_display_name(n)

    # Discover hospital codes from folder
    base_dir = os.path.abspath(os.getcwd())
    booking_dir = None
    for cand in ["Booking_data", "Booking_Data"]:
        p = os.path.join(base_dir, cand)
        if os.path.isdir(p):
            booking_dir = p
            break
    hospital_codes: List[str] = []
    if booking_dir:
        for f in os.listdir(booking_dir):
            low = f.lower()
            if low.endswith('.json') and not low.startswith('departments_index'):
                hospital_codes.append(os.path.splitext(f)[0])
    out_hospitals: List[Dict[str, Any]] = []
    debug_map: List[Dict[str, Any]] = []
    for hosp_code in hospital_codes:
        meta = get_hospital_meta(hosp_code)
        if not meta:
            continue
        by_code: Dict[str, Dict[str, Any]] = meta.get("departments_by_code", {}) or {}
        if not by_code:
            # fallback: skip hospital (legacy path) to avoid name fuzz now
            continue
        dep_entries: List[Dict[str, Any]] = []
        # One blocked snapshot per hospital for all selected codes
        blocked = get_blocked_snapshot_by_codes(hosp_code, selected_department_codes, date_str)
        blocked_map: Dict[str, Dict[str, List[str]]] = (blocked.get("blocked") or {}) if isinstance(blocked, dict) else {}
        for code in selected_department_codes:
            info = by_code.get(code)
            if not info:
                debug_map.append({"hospital": hosp_code, "code": code, "match": 0})
                continue
            disp_name = _clean_display_name(info.get("name") or code_display.get(code) or code)
            doctors = info.get("doctors", []) or []
            doc_entries: List[Dict[str, Any]] = []
            for doc in doctors:
                blocked_slots = set((blocked_map.get(code, {}) or {}).get(doc, []))
                free_slots = [s for s in ALL_SLOTS if s not in blocked_slots]
                if BOOKING_DEBUG:
                    _blog(f"[Stage2 free] {hosp_code}/{code}/{doc}: free={free_slots[:6]} count={len(free_slots)}")
                doc_entries.append({"name": doc, "free_slots": free_slots})
            dep_entries.append({
                "department_code": code,
                "department_name": disp_name,
                "doctors": doc_entries,
            })
            debug_map.append({"hospital": hosp_code, "code": code, "dep_name": disp_name, "doctor_count": len(doctors)})
        if dep_entries:
            out_hospitals.append({
                "hospital_code": hosp_code,
                "hospital_name": meta.get("hospital_name") or meta.get("name") or hosp_code,
                "departments": dep_entries,
                "hospital_image": _resolve_hospital_image(hosp_code)
            })

    result = {
        "date": date_str,
        "slots": {"start": "07:40", "end": "16:40", "slot_minutes": 20},
        "hospitals": out_hospitals,
        "selected_department_codes": selected_department_codes,
    }
    if BOOKING_DEBUG:
        sample = []
        for d in debug_map[:8]:
            sample.append((d.get('hospital'), d.get('code'), d.get('dep_name'), d.get('doctor_count', d.get('match'))))
        _blog(f"Stage2 match detail: {sample}")
    return result

STAGE2_SYSTEM = (
    "Bạn là trợ lý gợi ý lịch khám dựa 100% vào dữ liệu cung cấp (hospitals->departments->doctors->free_slots). "
    "KHÔNG được tạo thêm bệnh viện, khoa, bác sĩ, hoặc giờ ngoài danh sách free_slots. "
    "Nếu danh sách hospitals trống hoặc tất cả doctors không có free_slots thì trả options=[] và chosen phải null hoặc thiếu."
)

def _sanitize_stage2_options(schedule_data: Dict[str, Any], result_dict: Dict[str, Any], dept_index_map: Optional[Dict[str, Dict[str, str]]] = None) -> None:
    try:
        hosp_allowed: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
        hospital_names: Dict[str, str] = {}
        for h in schedule_data.get("hospitals", []):
            hcode = h.get("hospital_code")
            if not hcode:
                continue
            hospital_names[hcode] = _clean_display_name(h.get("hospital_name") or hcode)
            dep_map: Dict[str, Dict[str, List[str]]] = {}
            for dep in h.get("departments", []):
                dcode = dep.get("department_code")
                if not dcode:
                    continue
                dname = dep.get("department_name") or dcode
                docs = [d.get("name") for d in dep.get("doctors", []) if d.get("name")]
                dep_map[dcode] = {"name": dname, "doctors": docs}
            hosp_allowed[hcode] = dep_map
        # Build free slot map keyed by (hospital_code, department_code, doctor)
        free_map: Dict[Tuple[str,str,str], set] = {}
        for h in schedule_data.get("hospitals", []):
            hcode = h.get("hospital_code")
            for dep in h.get("departments", []):
                dcode = dep.get("department_code")
                if not dcode:
                    continue
                for doc in dep.get("doctors", []):
                    free_map[(hcode, dcode, doc.get("name"))] = set(doc.get("free_slots", []))
        opts = result_dict.get("options") or []
        valid_opts = []
        removed = []
        for o in opts:
            if not isinstance(o, dict):
                removed.append({"reason":"not_dict"}); continue
            hosp = o.get("hospital_code") or o.get("hospital")
            dep_code = o.get("department_code")  # must be present
            doc = o.get("doctor_name")
            slot = (o.get("slot_time") or "").split(" ")[-1]
            if hosp not in hosp_allowed:
                removed.append({"hospital":hosp,"reason":"hospital_not_in_schedule"}); continue
            if dep_code not in hosp_allowed[hosp]:
                removed.append({"hospital":hosp,"department_code":dep_code,"reason":"department_not_in_schedule"}); continue
            docs_allowed = hosp_allowed[hosp][dep_code]["doctors"]
            if doc not in docs_allowed:
                removed.append({"hospital":hosp,"department_code":dep_code,"doctor":doc,"reason":"doctor_not_in_schedule"}); continue
            if slot and slot not in free_map.get((hosp, dep_code, doc), set()):
                removed.append({"hospital":hosp,"department_code":dep_code,"doctor":doc,"slot":slot,"reason":"slot_not_free"}); continue
            # normalize + enforce canonical names
            o["hospital_code"] = hosp
            o["department_code"] = dep_code
            # derive display name
            canonical_name = (dept_index_map or {}).get(hosp, {}).get(dep_code) if dept_index_map else None
            if not canonical_name:
                canonical_name = hosp_allowed[hosp][dep_code]["name"]
            if canonical_name:
                o["department"] = _clean_display_name(canonical_name)
            hn = hospital_names.get(hosp)
            if hn:
                o["hospital"] = _clean_display_name(hn)
            valid_opts.append(o)
        if removed and BOOKING_DEBUG:
            _blog(f"Stage2 sanitize removed={len(removed)} details={removed[:3]}")
        result_dict["options"] = valid_opts
        # chosen fix
        chosen = result_dict.get("chosen")
        if chosen and chosen not in valid_opts:
            result_dict["chosen"] = valid_opts[0] if valid_opts else None
        if not valid_opts:
            # ensure empty if nothing valid
            result_dict["options"] = []
            if result_dict.get("chosen") is not None and not isinstance(result_dict.get("chosen"), dict):
                result_dict["chosen"] = None
    except Exception as e:
        if BOOKING_DEBUG:
            _blog(f"Stage2 sanitize error: {e}")

def _stage2_build_booking(client, model: str, history_text: str, schedule_data: Dict[str, Any], dept_index_map: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
    user_prompt = (
        "# DATA\n" + json.dumps(schedule_data, ensure_ascii=False) + "\n\n" +
        "# HỘI THOẠI\n" + history_text + "\n\n" +
        "# YÊU CẦU\nTạo tối đa 3 options hợp lệ. Mỗi option: hospital_code, department_code, doctor_name, slot_time=DATE HH:MM (dùng free_slots). "
        "Chọn 1 vào 'chosen'. Không bịa. Nếu không còn slot: options=[] và chosen=null."
    )
    try:
        resp = client.models.generate_content(
            model=model,
            contents=[genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_prompt)])],
            config=genai_types.GenerateContentConfig(
                system_instruction=STAGE2_SYSTEM,
                temperature=0.0,
                max_output_tokens=10096,
                response_mime_type="application/json",
                response_schema=BookingResult,
            ),
        )
        raw_txt = (resp.text or "")
        if raw_txt:
            _blog(f"Stage2 raw text len={len(raw_txt)} preview={raw_txt[:300].replace(chr(10),' ')}")
    except genai_errors.APIError as e:
        return {"error": f"api_error: {e.code}", "message": str(e)}
    except Exception as e:
        return {"error": f"stage2_invoke_error: {e}"}
    # Parse
    result_dict: Dict[str, Any] = {}
    try:
        parsed = getattr(resp, "parsed", None)
        if parsed is not None:
            if isinstance(parsed, list) and parsed:
                result_dict = parsed[0].model_dump() if hasattr(parsed[0], "model_dump") else parsed[0]
            elif hasattr(parsed, "model_dump"):
                result_dict = parsed.model_dump()
            elif isinstance(parsed, dict):
                result_dict = parsed
    except Exception:
        result_dict = {}
    if not result_dict:
        txt = resp.text or ""
        if txt:
            try:
                txt = _fix_truncated_json(txt)
                result_dict = json.loads(txt)
            except Exception:
                result_dict = _extract_json(txt)
    if not isinstance(result_dict, dict) or not result_dict:
        return {"error": "empty_or_malformed_json", "raw": resp.text or ""}
    try:
        _sanitize_stage2_options(schedule_data, result_dict, dept_index_map)
        opts = result_dict.get("options") or []
        _blog(f"Stage2 parsed options={len(opts)} chosen_keys={list((result_dict.get('chosen') or {} or {}).keys())}")
    except Exception:
        pass
    return result_dict


def book_appointment(
    history_text: str,
    clinic_data_path: str,
    model: str = "gemini-2.5-flash",
    extra_paths: Optional[List[str]] = None,
    *,
    two_stage: bool = True,
    departments_index_path: Optional[str] = None,
    target_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Two-stage booking (department selection then schedule reasoning) with fallback to single stage.

    Stage 1: Use departments_index.json to select relevant departments.
    Stage 2: Aggregate current free slots (schedule_logic) limited to selected departments, then produce BookingResult.
    """
    api_key = _pick_api_key()
    if not api_key:
        raise RuntimeError("Thiếu GOOGLE_API_KEY/GEMINI_API_KEY")
    client = google_genai.Client(api_key=api_key)

    if not target_date:
        target_date = datetime.date.today().isoformat()

    departments_index = _load_departments_index(departments_index_path)
    selected_codes: List[str] = []
    if two_stage and departments_index:
        selected_codes = _stage1_select_codes(client, model, history_text, departments_index)
    if not selected_codes and departments_index:
        # fallback: 3 mã đầu tiên gặp được
        uniq = []
        for arr in departments_index.values():
            for e in arr:
                c = e.get('code')
                if c and c not in uniq:
                    uniq.append(c)
                if len(uniq) >= 3:
                    break
            if len(uniq) >= 3:
                break
        selected_codes = uniq
    code_to_name = {}
    for arr in departments_index.values():
        for e in arr:
            c = e.get('code'); n = e.get('name')
            if c and n and c not in code_to_name:
                code_to_name[c] = n
    selected_names = [code_to_name.get(c, c) for c in selected_codes]

    # Stage 2: gather schedule (by codes)
    schedule_data = _gather_schedule(selected_codes, departments_index, target_date) if selected_codes else {"error": "no_departments_selected"}
    if two_stage and not schedule_data.get("error"):
        try:
            hosp_count = len(schedule_data.get("hospitals", []))
            doc_count = sum(len(d.get("doctors", [])) for h in schedule_data.get("hospitals", []) for d in h.get("departments", []))
            _blog(f"Stage2 schedule aggregated hospitals={hosp_count} doctors={doc_count} selected_codes={selected_codes} names={selected_names}")
        except Exception:
            pass
    if schedule_data.get("error"):
        # fallback to legacy single-file multi-hospital data path approach
        two_stage = False

    # Build dept_index_map: {hospital_code: {dept_code: canonical_name}}
    dept_index_map: Dict[str, Dict[str, str]] = {}
    for hosp, arr in (departments_index or {}).items():
        inner = {}
        for e in arr or []:
            c = e.get('code'); n = e.get('name')
            if c and n:
                inner[c] = _clean_display_name(n)
        if inner:
            dept_index_map[hosp] = inner

    if two_stage and not schedule_data.get("error"):
        result_dict = _stage2_build_booking(client, model, history_text, schedule_data, dept_index_map)
    else:
        # Legacy path: reuse previous single-stage logic using clinic_data_path(s)
        _blog("Falling back to legacy single-stage booking flow")
        paths = [clinic_data_path]
        if extra_paths:
            for p in extra_paths:
                if p and p not in paths:
                    paths.append(p)
        loaded = []
        for p in paths:
            try:
                loaded.append(_load_clinic_data(p))
            except Exception as e:
                print(f"[booking] warn cannot load {p}: {e}")
        if not loaded:
            return {"error": "no_data"}
        data = _merge_multi(loaded)
        # Build prompt
        user_prompt = (
            "# DỮ LIỆU LỊCH ĐA BỆNH VIỆN (JSON)\n" + json.dumps(data, ensure_ascii=False) + "\n\n" +
            "# TRANSCRIPT HỘI THOẠI\n" + history_text + "\n\n" +
            "# YÊU CẦU\n1) Phân tích nhu cầu. 2) Sinh tối đa 3 options tốt nhất. 3) Chọn 1 final trong 'chosen'."
        )
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_prompt)])],
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    temperature=0.0,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                    response_schema=BookingResult,
                ),
            )
        except genai_errors.APIError as e:
            return {"error": f"api_error: {e.code}", "message": str(e)}
        except Exception as e:
            return {"error": f"legacy_invoke_error: {e}"}
        result_dict = {}
        try:
            parsed = getattr(resp, "parsed", None)
            if parsed is not None:
                if isinstance(parsed, list) and parsed:
                    result_dict = parsed[0].model_dump() if hasattr(parsed[0], "model_dump") else parsed[0]
                elif hasattr(parsed, "model_dump"):
                    result_dict = parsed.model_dump()
                elif isinstance(parsed, dict):
                    result_dict = parsed
        except Exception:
            result_dict = {}
        if not result_dict:
            txt = resp.text or ""
            if txt:
                try:
                    txt = _fix_truncated_json(txt)
                    result_dict = json.loads(txt)
                except Exception:
                    result_dict = _extract_json(txt)
        if not isinstance(result_dict, dict) or not result_dict:
            return {"error": "empty_or_malformed_json", "raw": resp.text or ""}

    # Normalize options shape
    if isinstance(result_dict, dict) and "options" not in result_dict and all(k in result_dict for k in ("department","doctor_name","slot_time")):
        opt = {"hospital": "(unknown)", **{k: result_dict[k] for k in ("department","doctor_name","slot_time") if k in result_dict}}
        result_dict = {"options": [opt], "chosen": opt}

    # Attach meta about selection
    result_dict.setdefault("meta", {})
    result_dict["meta"]["selected_department_codes"] = selected_codes
    result_dict["meta"]["selected_departments"] = selected_names
    result_dict["meta"]["two_stage"] = bool(two_stage)
    if "schedule" not in result_dict.get("meta", {}) and two_stage:
        result_dict["meta"]["schedule"] = {k: v for k, v in schedule_data.items() if k != "error"}
    if two_stage:
        result_dict["meta"]["dept_index_map"] = dept_index_map

    return result_dict
