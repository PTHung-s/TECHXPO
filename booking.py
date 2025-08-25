# booking.py
# -*- coding: utf-8 -*-
"""
Đặt lịch khám:
- Đọc file dữ liệu (JSON/YAML) về khoa, bác sĩ, lịch rảnh.
- Dùng Gemini để chọn khoa, bác sĩ, slot.
- Trả về JSON + câu thoại ngắn để Talker phát lại.

ENV: GOOGLE_API_KEY (hoặc GEMINI_API_KEY)
"""

import os, json, re, datetime
from typing import Dict, Any, Optional, Tuple

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

    # debug nhẹ để yên tâm file đã đọc được
    dept_cnt = len(data.get("departments", [])) if isinstance(data, dict) else 0
    doc_cnt = len(data.get("doctors", [])) if isinstance(data, dict) else 0
    print(f"[booking] loaded clinic_data: path={ap} departments={dept_cnt} doctors={doc_cnt}")
    return data


def _pick_api_key() -> str:
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""


# ---------------- Debug helpers ----------------
def _json_dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=2)

def _debug_dump_response(resp):
    """In toàn bộ phản hồi model để chẩn đoán (text, parsed, candidates, parts...)."""
    print("\n==== DEBUG: RAW MODEL RESPONSE ====")
    # 1) text
    try:
        print("resp.text:", repr(resp.text))
    except Exception as e:
        print("resp.text: <err>", e)

    # 2) parsed
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

    # 3) candidates / parts / finish_reason / safety
    try:
        cands = getattr(resp, "candidates", None) or []
        print(f"candidates: {len(cands)}")
        for i, c in enumerate(cands):
            fr = getattr(c, "finish_reason", None) or getattr(c, "finishReason", None)
            sr = getattr(c, "safety_ratings", None) or getattr(c, "safetyRatings", None)
            print(f"  - cand[{i}] finish_reason={fr}")
            if sr:
                print(f"    safety_ratings={sr}")
            content = getattr(c, "content", None)
            parts = getattr(content, "parts", None) or []
            for j, part in enumerate(parts):
                t = getattr(part, "text", None)
                fc = getattr(part, "function_call", None)
                if t is not None:
                    preview = t[:200].replace("\n", "\\n")
                    print(f"    part[{j}].text[:200] = {repr(preview)}")
                if fc is not None:
                    print(f"    part[{j}].function_call = {fc}")
    except Exception as e:
        print("candidates dump err:", e)

    # 4) usage / model version
    try:
        print("usage_metadata:", getattr(resp, "usage_metadata", None))
        print("model_version:", getattr(resp, "model_version", None))
    except Exception as e:
        print("meta dump err:", e)

    # 5) dump toàn thể (pydantic)
    try:
        print("resp.model_dump():\n", _json_dumps(resp.model_dump()))
    except Exception as e:
        print("resp.model_dump(): <err>", e)
    print("==== END DEBUG ====\n")


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


def _build_speak_text(result: Dict[str, Any]) -> str:
    """Luôn tạo câu thoại ngắn, tránh model viết dài (ngăn truncation)."""
    dep = result.get("department") or "khoa phù hợp"
    doc = result.get("doctor_name") or "bác sĩ phụ trách"
    slot = (result.get("slot_time") or "").strip()

    # Chuẩn hoá thời gian về 'HH:MM ngày dd/mm/YYYY' nếu có thể
    spoken_time = slot
    try:
        s = slot.replace("T", " ").replace("Z", "").strip()
        dt = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.datetime.strptime(s, fmt); break
            except Exception:
                pass
        if dt is None:
            try:
                dt = datetime.datetime.fromisoformat(s)
            except Exception:
                dt = None
        if dt is not None:
            spoken_time = dt.strftime("%H:%M ngày %d/%m/%Y")
    except Exception:
        pass

    return f"Tôi đã sắp xếp lịch tại {dep}, bác sĩ {doc}, khung giờ {spoken_time}. Nếu cần đổi, xin báo để tôi điều chỉnh."


# ---------------- Structured Output schema (Pydantic) ----------------
class BookingResult(BaseModel):
    department: str = Field(..., description="Tên khoa")
    doctor_name: str = Field(..., description="Tên bác sĩ")
    slot_time: str = Field(..., description="ISO 8601 hoặc 'YYYY-MM-DD HH:MM'")
    room: Optional[str] = Field(None, description="Mã phòng, ví dụ 'K.101'")
    queue_number: Optional[str] = Field(None, description="Số thứ tự, ví dụ 'A-051'")
    patient_name: Optional[str] = None
    phone: Optional[str] = None
    note: Optional[str] = None
    # Không bắt buộc speak_text để JSON ngắn gọn; sẽ tự build ở code
    speak_text: Optional[str] = None


SYSTEM = (
    "Bạn là trợ lý đặt lịch khám bệnh, TUÂN THỦ DỮ LIỆU đầu vào (khoa/bác sĩ/slots). "
    "Chỉ chọn slot có trong dữ liệu và gần nhất với thời điểm yêu cầu. "
    "KHÔNG diễn giải, KHÔNG tóm tắt transcript, KHÔNG thêm câu chữ ngoài JSON. "
    "Chỉ xuất JSON theo schema đã cấu hình."
)


def book_appointment(
    history_text: str,
    clinic_data_path: str,
    model: str = "gemini-2.5-flash",
) -> Dict[str, Any]:
    """
    Input:
      - history_text: toàn bộ transcript hội thoại (user + assistant)
      - clinic_data_path: đường dẫn file dữ liệu lịch/khoa/bác sĩ
    Output JSON (theo BookingResult)
    """
    data = _load_clinic_data(clinic_data_path)
    api_key = _pick_api_key()
    if not api_key:
        raise RuntimeError("Thiếu GOOGLE_API_KEY/GEMINI_API_KEY")

    client = google_genai.Client(api_key=api_key)

    # Prompt gọn; schema đặt ở config theo đúng hướng dẫn (không lặp trong prompt).
    user_prompt = (
        "# DỮ LIỆU LỊCH/BS (JSON)\n"
        f"{json.dumps(data, ensure_ascii=False)}\n\n"
        "# TRANSCRIPT HỘI THOẠI\n"
        f"{history_text}\n\n"
        "# YÊU CẦU\n"
        "- Chọn khoa, bác sĩ và slot HỢP LỆ gần nhất có trong dữ liệu trên.\n"
        "- Trả JSON đúng schema đã cấu hình. Không thêm lời giải thích.\n"
        "- Các giá trị ngắn gọn; không viết thành câu dài."
    )

    try:
        resp = client.models.generate_content(
            model=model,
            contents=[genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_prompt)])],
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM,
                temperature=0.0,                 # ổn định JSON
                max_output_tokens=1068,          # nới theo nhu cầu
                # NOTE: Structured Output (official)
                response_mime_type="application/json",
                response_schema=BookingResult,   # Pydantic schema
                # Safety có thể khiến text rỗng nếu bị block; cân chỉnh nếu cần.
                # safety_settings=[genai_types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_ONLY_HIGH")],
            ),
        )
    except genai_errors.APIError as e:
        return {"error": f"api_error: {e.code}", "message": str(e)}

    # 👉 Dump chi tiết để kiểm tra model thực sự trả gì
    try:
        _debug_dump_response(resp)
    except Exception:
        pass

    # Ưu tiên lấy object đã parse (SDK sẽ parse sẵn khi structured output OK)
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

    # Fallback 1: dùng text nếu có (vá JSON nếu thiếu } )
    if not result_dict:
        text = resp.text or ""
        if not text:
            # Fallback 2: thử vớt từ parts
            text = _first_json_like_from_parts(resp) or ""
        if text:
            text = _fix_truncated_json(text)
            try:
                result_dict = json.loads(text)
            except Exception:
                result_dict = _extract_json(text)

    if not isinstance(result_dict, dict) or not result_dict:
        return {"error": "empty_or_malformed_json", "raw": resp.text or ""}

    # --- Hậu xử lý để đủ field hệ thống dùng ---
    dep_name = (result_dict.get("department") or "").strip()

    # room mặc định theo khoa nếu thiếu
    if not result_dict.get("room"):
        try:
            dep = next((d for d in data.get("departments", []) if d.get("name") == dep_name), None)
            rooms = dep.get("rooms", []) if dep else []
            if rooms:
                result_dict["room"] = rooms[0]
        except Exception:
            pass

    # cấp số thứ tự hàng chờ nếu thiếu -> 3 chữ số như "A-051"
    if not result_dict.get("queue_number") and data.get("queue_prefix") and data.get("next_queue_number") is not None:
        try:
            qn = f'{data["queue_prefix"]}{int(data["next_queue_number"]):03d}'
            result_dict["queue_number"] = qn
        except Exception:
            pass

    # Luôn tự dựng speak_text để ngắn gọn, tránh truncation
    st = (result_dict.get("speak_text") or "").strip()
    if not st:
        result_dict["speak_text"] = _build_speak_text(result_dict)

    return result_dict
