# clerk_wrapup.py
# -*- coding: utf-8 -*-
import os, json, re
from typing import Dict, Any, Optional, List, Tuple

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

CLERK_MODEL = os.getenv("CLERK_MODEL", "gemini-2.5-flash")

# ---------- utils ----------
def _to_jsonable(x):
    if isinstance(x, set):
        return list(x)
    if isinstance(x, dict):
        return {k: _to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_to_jsonable(v) for v in x]
    return x

def _client():
    api_key = (os.getenv("GOOGLE_API_KEY2"))
    return genai.Client(api_key=api_key) if api_key else genai.Client()

def _json_dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=2)

def _debug_dump_response(resp):
    print("\n==== DEBUG: WRAPUP RAW MODEL RESPONSE ====")
    try: print("resp.text:", repr(resp.text))
    except Exception as e: print("resp.text: <err>", e)

    try:
        p = getattr(resp, "parsed", None)
        if p is None:
            print("resp.parsed: None")
        elif isinstance(p, list):
            out = []
            for it in p:
                out.append(it.model_dump() if hasattr(it, "model_dump") else it)
            print("resp.parsed (list):\n", _json_dumps(out))
        elif hasattr(p, "model_dump"):
            print("resp.parsed (model_dump):\n", _json_dumps(p.model_dump()))
        else:
            print("resp.parsed (raw):", p)
    except Exception as e:
        print("resp.parsed: <err>", e)

    try:
        cands = getattr(resp, "candidates", None) or []
        print(f"candidates: {len(cands)}")
        for i, c in enumerate(cands):
            fr = getattr(c, "finish_reason", None) or getattr(c, "finishReason", None)
            sr = getattr(c, "safety_ratings", None) or getattr(c, "safetyRatings", None)
            print(f"  - cand[{i}] finish_reason={fr}")
            if sr: print(f"    safety_ratings={sr}")
            content = getattr(c, "content", None)
            parts = getattr(content, "parts", None) or []
            for j, part in enumerate(parts):
                t = getattr(part, "text", None)
                if isinstance(t, str):
                    preview = t[:200].replace("\n", "\\n")
                    print(f"    part[{j}].text[:200] = {repr(preview)}")
    except Exception as e:
        print("candidates dump err:", e)
    print("==== END DEBUG ====\n")

# --- vá JSON nếu bị cắt ---
_BRACE_RE = re.compile(r"\{[\s\S]*")
def _brace_balance(s: str) -> Tuple[int, int]:
    return s.count("{"), s.count("}")

def _fix_truncated_json(text: str) -> str:
    if not text: return text
    m = _BRACE_RE.search(text)
    if m: text = m.group(0)
    text = text.strip()
    opens, closes = _brace_balance(text)
    if opens > closes:
        missing = opens - closes
        print(f"[wrapup] WARN: JSON thiếu {missing} dấu '}}' -> tự vá")
        text = text + ("}" * missing)
    return text

def _first_json_like_from_parts(resp) -> Optional[str]:
    try:
        c = (resp.candidates or [None])[0]
        if not c or not getattr(c, "content", None): return None
        for p in c.content.parts or []:
            t = getattr(p, "text", None)
            if isinstance(t, str) and "{" in t:
                return _fix_truncated_json(t)
    except Exception:
        pass
    return None

# ---------- Structured Output schema ----------
class Symptom(BaseModel):
    name: str
    severity: Optional[str] = None
    duration: Optional[str] = None

class VisitSummary(BaseModel):
    patient_name: str
    phone: str
    customer_id: str
    doctor_name: str
    appointment_time: str
    room: Optional[str] = ""
    queue_number: Optional[str] = ""
    symptoms: List[Symptom]
    tentative_diagnoses: List[str]
    tests_recommended: List[str]
    medications_advised: List[str]
    diet_notes: str
    follow_up: str
    warnings: str

SYSTEM = (
    "Bạn là thư ký y khoa. Trích xuất phiếu thăm khám tiếng Việt từ hội thoại. "
    "Nếu có khối [BOOKING], coi đó là nguồn chuẩn cho bác sĩ, thời gian hẹn, phòng, số thứ tự, tên & SĐT bệnh nhân. "
    "Chỉ xuất JSON theo schema đã cấu hình."
)

def summarize_visit_json(
    transcript_text: str,
    clinic_defaults: Dict[str, Any],
    booking: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Structured Output (JSON) + ưu tiên dữ liệu booking.
    """
    client = _client()
    safe_defaults = _to_jsonable(clinic_defaults)
    booking_safe = _to_jsonable(booking or {})

    prompt = f"""
Bạn là thư ký y khoa. Hãy trích xuất phiếu thăm khám (tiếng Việt) từ hội thoại dưới đây.
- ƯU TIÊN dùng thông tin từ [BOOKING] (coi là nguồn chuẩn).
- Chỉ trả JSON đúng schema, không kèm giải thích.
- Nếu thiếu thông tin, dùng "(không rõ)" hoặc mảng rỗng.

[THÔNG TIN PHÒNG KHÁM MẶC ĐỊNH]
{_json_dumps(safe_defaults)}

[BOOKING]
{_json_dumps(booking_safe)}

[HỘI THOẠI]
{transcript_text}
""".strip()

    try:
        # ⚠️ FIX: truyền chuỗi trực tiếp vào contents (chuẩn docs)
        resp = client.models.generate_content(
            model=CLERK_MODEL,
            contents=prompt,  # <-- thay vì Content/Part.from_text(...)
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                temperature=0.0,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=VisitSummary,   # Pydantic schema
                # safety_settings=[ ... ]  # nới nếu cần
            ),
        )
    except Exception as e:
        return {
            "patient_name": "(không rõ)", "phone": "(không rõ)", "customer_id": "(không rõ)",
            "doctor_name": clinic_defaults.get("doctor_name") or "(không rõ)",
            "appointment_time": clinic_defaults.get("appointment_time") or "(không rõ)",
            "room": "", "queue_number": "", "symptoms": [],
            "tentative_diagnoses": [], "tests_recommended": [], "medications_advised": [],
            "diet_notes": clinic_defaults.get("diet_notes") or "(không rõ)",
            "follow_up": "Tái khám khi có dấu hiệu bất thường.", "warnings": f"api_error: {e}"
        }

    try:
        _debug_dump_response(resp)
    except Exception:
        pass

    # 1) ưu tiên structured parsed
    data: Dict[str, Any] = {}
    try:
        parsed = getattr(resp, "parsed", None)
        if parsed is not None:
            if isinstance(parsed, list) and parsed:
                data = parsed[0].model_dump() if hasattr(parsed[0], "model_dump") else parsed[0]
            elif hasattr(parsed, "model_dump"):
                data = parsed.model_dump()
            elif isinstance(parsed, dict):
                data = parsed
    except Exception:
        data = {}

    # 2) fallback: text/parts + vá JSON
    if not data:
        text = resp.text or _first_json_like_from_parts(resp) or ""
        if text:
            text = _fix_truncated_json(text)
            try:
                data = json.loads(text)
            except Exception:
                data = {}

    # 3) hợp nhất với booking & defaults
    if booking_safe:
        data.setdefault("doctor_name", booking_safe.get("doctor_name") or "")
        data.setdefault("appointment_time", booking_safe.get("slot_time") or "")
        if booking_safe.get("room"): data.setdefault("room", booking_safe.get("room"))
        if booking_safe.get("queue_number"): data.setdefault("queue_number", booking_safe.get("queue_number"))
        if booking_safe.get("patient_name"): data.setdefault("patient_name", booking_safe.get("patient_name"))
        if booking_safe.get("phone"): data.setdefault("phone", booking_safe.get("phone"))

    safe = {
        "patient_name": data.get("patient_name") or "(không rõ)",
        "phone": data.get("phone") or "(không rõ)",
        "customer_id": data.get("customer_id") or "(không rõ)",
        "doctor_name": data.get("doctor_name") or clinic_defaults.get("doctor_name") or "(không rõ)",
        "appointment_time": data.get("appointment_time") or clinic_defaults.get("appointment_time") or "(không rõ)",
        "room": data.get("room") or "",
        "queue_number": data.get("queue_number") or "",
        "symptoms": data.get("symptoms") or [],
        "tentative_diagnoses": data.get("tentative_diagnoses") or [],
        "tests_recommended": data.get("tests_recommended") or [],
        "medications_advised": data.get("medications_advised") or [],
        "diet_notes": data.get("diet_notes") or clinic_defaults.get("diet_notes") or "(không rõ)",
        "follow_up": data.get("follow_up") or "Tái khám khi có dấu hiệu bất thường.",
        "warnings": data.get("warnings") or "",
    }
    return safe
