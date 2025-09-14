import asyncio
import contextlib
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, Optional

from google.cloud import texttospeech as tts
from livekit.agents import JobContext, RunContext, function_tool
from storage import get_customer_by_phone, build_personal_context, get_recent_visits

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

def _fn_log(msg: str):
    log.info(f"[FnDef] {msg}")

# Regex helpers (Vietnam local mobile carriers starting 03/05/07/08/09)
PHONE_RE_FULL = re.compile(r"^0(3|5|7|8|9)\d{8}$")
PHONE_RE_BASIC = PHONE_RE_FULL


def build_all_tools(
    publish_data: Callable[[Dict[str, Any]], asyncio.Task],
    identity_state: Dict[str, Any],
    shared: Dict[str, Any],
    *,
    state,  # SessionBuf instance
    book_appointment,
    get_or_create_customer,
    save_visit,
    summarize_visit_json,
    clinic_defaults: Dict[str, Any],
):
    """Return list of function tools (identity, booking, finalize).

    identity_state keys expected: patient_name, phone, draft_name, draft_phone, draft_conf, identity_confirmed
    shared expected: latest_booking, allow_finalize, closing, session, ctx, rag, extract_facts_and_summary
    """

    # ---------------- Identity Tools ----------------
    @function_tool
    async def propose_identity(
        context: RunContext,
        patient_name_partial: Optional[str] = None,
        phone_partial: Optional[str] = None,
        confidence: float = 0.0,
        reason: Optional[str] = None,
    ) -> dict:
        if identity_state.get("identity_confirmed"):
            return {"status": "ignored", "message": "already_confirmed"}
        conf = max(0.0, min(1.0, float(confidence or 0.0)))
        if patient_name_partial:
            prev_conf = identity_state.get("draft_conf") or 0.0
            if not identity_state.get("draft_name") or conf >= prev_conf:
                identity_state["draft_name"] = patient_name_partial.strip()
                identity_state["draft_conf"] = conf
        if phone_partial:
            prev_conf = identity_state.get("draft_conf") or 0.0
            if not identity_state.get("draft_phone") or conf >= prev_conf:
                identity_state["draft_phone"] = phone_partial.strip()
                identity_state["draft_conf"] = conf
        payload = {
            "type": "identity_captured",
            "patient_name": identity_state.get("draft_name"),
            "phone": identity_state.get("draft_phone"),
            "confidence": identity_state.get("draft_conf", conf),
            "confirmed": False,
            "reason": reason,
        }
        publish_data(payload)
        return {"status": "proposed", **payload}

    @function_tool
    async def confirm_identity(
        context: RunContext,
        patient_name_input: Optional[str] = None,
        phone_input: Optional[str] = None,
        confirm: bool = True,
    ) -> dict:
        # Allow reconfirmation (update) when already confirmed
        if identity_state.get("identity_confirmed"):
            changed = False
            current_name = identity_state.get("patient_name") or ""
            current_phone = identity_state.get("phone") or ""
            new_name = patient_name_input.strip() if patient_name_input else None
            new_phone = phone_input.strip() if phone_input else None
            if new_name and new_name != current_name:
                identity_state["patient_name"] = new_name
                changed = True
            if new_phone and PHONE_RE_FULL.match(new_phone) and new_phone != current_phone:
                identity_state["phone"] = new_phone
                changed = True
            if changed:
                shared["latest_booking"] = None
                shared["allow_finalize"] = False
                payload = {
                    "type": "identity_updated",
                    "patient_name": identity_state.get("patient_name"),
                    "phone": identity_state.get("phone"),
                    "confidence": identity_state.get("draft_conf", 1.0),
                    "confirmed": True,
                }
                publish_data(payload)
                return {"status": "reconfirmed", **payload}
            return {"status": "already_confirmed", "patient_name": current_name, "phone": current_phone}

        # First-time confirm flow
        if patient_name_input:
            identity_state["patient_name"] = patient_name_input.strip()
        elif identity_state.get("draft_name") and not identity_state.get("patient_name"):
            identity_state["patient_name"] = identity_state.get("draft_name")

        if phone_input and PHONE_RE_FULL.match(phone_input.strip()):
            identity_state["phone"] = phone_input.strip()
        elif identity_state.get("draft_phone") and not identity_state.get("phone"):
            identity_state["phone"] = identity_state.get("draft_phone")

        if confirm and identity_state.get("patient_name") and identity_state.get("phone"):
            identity_state["identity_confirmed"] = True
            payload = {
                "type": "identity_confirmed",
                "patient_name": identity_state.get("patient_name"),
                "phone": identity_state.get("phone"),
                "confidence": identity_state.get("draft_conf", 1.0),
                "confirmed": True,
            }
            publish_data(payload)
            # Đánh dấu để LLM không gọi lại tool xác nhận
            state.add("system", f"IDENTITY_CONFIRMED name={identity_state.get('patient_name')} phone={identity_state.get('phone')}")
            talker = shared.get("talker")
            if talker is not None:
                # chỉ giữ các tool còn cần thiết
                await talker.update_tools([schedule_appointment, choose_booking_option, finalize_visit])
            # Inject personal context (facts + last summary) exactly once
            if not shared.get("personal_context_injected"):
                try:
                    # try lookup existing customer (by phone), build context
                    cid = get_customer_by_phone(identity_state.get("phone"))
                    personal_blocks = ""
                    if cid:
                        visits = get_recent_visits(cid, limit=3)
                        personal_blocks = build_personal_context(customer_id=cid, visits=visits)
                    if personal_blocks:
                        wrapped = f"[PERSONAL_HISTORY]\n{personal_blocks}\n[/PERSONAL_HISTORY]"
                        shared["personal_context_injected"] = True
                        # Keep for transcript
                        state.add("system", "PERSONAL_CONTEXT_INJECTED")
                        talker = shared.get("talker")
                        if talker is not None:
                            try:
                                base_instr = getattr(talker, "instructions", "") or ""
                                new_instr = base_instr + "\n\n# PERSONAL CONTEXT\n" + wrapped + "\n\nHướng dẫn: Không lặp lại nguyên văn; chỉ tham chiếu khi hỗ trợ chẩn đoán hoặc hỏi triệu chứng mới. Nếu thấy bệnh nhân quen thuộc hãy chào hỏi phù hợp và hỏi vấn đề hiện tại."
                                await talker.update_instructions(new_instr)
                                publish_data({
                                    "type": "personal_context_injected",
                                    "has_facts": True,
                                })
                                # Đánh dấu cần gửi lời chào follow-up tự động và tự phát reply
                                shared["needs_personal_greet"] = True
                                rg = shared.get("reply_gate")
                                if rg:
                                        await rg.say("Hãy phản hồi ngắn gọn xác nhận đã cập nhật thông tin. Đừng gọi bất kỳ công cụ nào.")
                            except Exception:
                                pass
                    else:
                        # Khách mới: không có personal context, chỉ cần trigger reply
                        rg = shared.get("reply_gate")
                        if rg:
                            await rg.say("Hãy phản hồi ngắn gọn xác nhận đã cập nhật thông tin. Đừng gọi bất kỳ công cụ nào.")
                except Exception:
                    pass
            return {"status": "confirmed", **payload}

        return {
            "status": "pending",
            "patient_name": identity_state.get("patient_name") or identity_state.get("draft_name"),
            "phone": identity_state.get("phone") or identity_state.get("draft_phone"),
        }

    # ---------------- Booking Tool ----------------
    @function_tool
    async def schedule_appointment(
        context: RunContext,
        patient_name: str,
        phone: str,
        preferred_time: Optional[str] = None,
        symptoms: Optional[str] = None,
    ) -> dict:
        # Xóa kết quả đặt lịch cũ để bắt đầu một phiên mới, tránh đọc lại lịch cũ
        shared["latest_booking"] = None
        shared["allow_finalize"] = False

        if not identity_state.get("identity_confirmed"):
            return {"ok": False, "error": "identity_not_confirmed", "message": "Chưa xác nhận họ tên & SĐT."}
        # Ngăn spam khi đang chạy
        if shared.get("booking_in_progress"):
            return {"ok": False, "error": "booking_in_progress", "message": "Đang tra cứu lịch, vui lòng chờ."}
        # Đảm bảo không chồng tiếng trước khi tool bắt đầu (tránh timeout realtime)
        with contextlib.suppress(Exception):
            await context.wait_for_playout()
        prev = shared.get("latest_booking")
        if prev and preferred_time and prev.get("preferred_time") == preferred_time:
            return {"ok": False, "error": "duplicate_booking", "message": "Lịch này đã được đặt, hãy chọn thời điểm khác."}

        raw_phone = (phone or "").strip()
        raw_name = (patient_name or "").strip() or "(không rõ)"
        if not PHONE_RE_BASIC.match(raw_phone):
            raw_phone = raw_phone or "(không rõ)"
        if symptoms:
            state.add("user", f"Triệu chứng khai báo: {symptoms}")

        # Snapshot history (không chặn user tiếp tục nói)
        history = "\n".join(state.lines)
        rag = shared.get("rag")
        if rag and symptoms:
            try:
                guideline_ctx = rag.query(symptoms, k=3, max_chars=600)
                if guideline_ctx and "[GUIDELINES]" in guideline_ctx:
                    history += f"\n\n[MEDICAL_GUIDELINES]\n{guideline_ctx}\n[/MEDICAL_GUIDELINES]"
            except Exception:
                pass

        # Lấy cấu hình datasources
        data_path = os.getenv("CLINIC_DATA_PATH", "./clinic_data.json")
        book_model = os.getenv("BOOK_MODEL", "gemini-2.5-flash")
        extra_paths_env = os.getenv("CLINIC_DATA_PATHS", "")
        extra_paths = []
        if not os.path.exists(data_path):
            bd_dir = os.getenv("CLINIC_DATA_DIR", "./Booking_data")
            if os.path.isdir(bd_dir):
                try:
                    files = [os.path.join(bd_dir, f) for f in os.listdir(bd_dir) if f.lower().endswith('.json')]
                    files.sort()
                    if files:
                        data_path = files[0]
                        auto_extra = files[1:]
                        extra_paths.extend([p for p in auto_extra if p != data_path])
                except Exception:
                    pass
        if extra_paths_env.strip():
            extra_paths = [p.strip() for p in extra_paths_env.split(",") if p.strip() and p.strip() != data_path]
        seen = set(); _dedup = []
        for p in extra_paths:
            if p not in seen:
                seen.add(p); _dedup.append(p)
        extra_paths = _dedup

        shared["booking_in_progress"] = True
        # Báo UI đang xử lý
        publish_data({
            "type": "booking_pending",
            "patient_name": raw_name,
            "phone": raw_phone,
            "preferred_time": preferred_time,
        })

        session = shared.get("session")
        talker = shared.get("talker")

        # Guard chống bịa lịch trước khi có booking_result
        if not shared.get("booking_guard_added"):
            state.add("system", "BOOKING_GUARD: ĐANG TRA CỨU LỊCH - KHÔNG ĐƯỢC NÊU GIỜ/BÁC SĨ/BỆNH VIỆN CỤ THỂ TRƯỚC KHI NHẬN booking_result.")
            shared["booking_guard_added"] = True

        async def _run_booking():
            try:
                result = await asyncio.to_thread(
                    book_appointment,
                    history,
                    data_path,
                    book_model,
                    extra_paths,
                )
                if symptoms and not result.get("symptoms"):
                    result["symptoms"] = symptoms
                shared["latest_booking"] = result
                shared["allow_finalize"] = True
                # LOGGING: In ra các options trước khi gửi đi để kiểm tra hospital_name
                _fn_log(f"Publishing booking options with hospital names: {result.get('options', [])[:2]}")

                publish_data({
                    "type": "booking_result",
                    "booking": result,
                })
                # Kết thúc guard
                if shared.get("booking_guard_added"):
                    state.add("system", "BOOKING_GUARD_END")
                    shared["booking_guard_added"] = False
                # Sau khi có kết quả, tự động tạo 1 câu nói để trình bày
                try:
                    # Inject JSON (truncate) + options list as system context, không ép câu thoại cố định
                    _json_short = json.dumps(result, ensure_ascii=False)
                    if len(_json_short) > 1800:
                        _json_short = _json_short[:1800] + "...TRUNCATED"
                    state.add("system", f"BOOKING_JSON {_json_short}")
                    opts = result.get("options") or []
                    if opts:
                        lines = []
                        for i, o in enumerate(opts, start=1):
                            lines.append(f"BOOKING_OPT[{i}] hospital={o.get('hospital','?')} doctor={o.get('doctor_name','?')} time={o.get('slot_time','?')}")
                        state.add("system", "\n".join(lines))
                    # Phát speak_text (ngắn gọn) cho bệnh nhân nghe
                    speak_text = (result.get("speak_text") or "").strip()
                    
                    # Yêu cầu LLM phải trả lời dựa trên context mới, đảm bảo agent không bị im lặng
                    rg = shared.get("reply_gate")
                    if rg:
                        # Kết hợp speak_text và chỉ dẫn để "đánh thức" agent
                        instruction = "Bạn hãy dựa vào thông tin lịch khám vừa được cung cấp để trình bày các lựa chọn cho bệnh nhân."
                        if speak_text:
                            # Nếu có speak_text, ưu tiên dùng nó làm câu mào đầu
                            full_prompt = f"{speak_text}."
                        
                        # Gửi yêu cầu để Agent phải nói. Đây chính là "lời đánh thức" bạn cần.
                        await rg.say(full_prompt)
                    else:
                        # Fallback nếu không có reply_gate
                        if speak_text and session:
                            try:
                                await session.say(text=speak_text)
                            except Exception as e:
                                _fn_log(f"Error speaking booking result via session: {e}")
                    
                except Exception as e:
                    _fn_log(f"Error processing booking result for speech: {e}")
            except Exception as e:
                publish_data({
                    "type": "booking_error",
                    "error": str(e),
                })
                if shared.get("booking_guard_added"):
                    state.add("system", "BOOKING_GUARD_END")
                    shared["booking_guard_added"] = False
                try:
                    if session is not None:
                        apology = "Em xin lỗi, hiện tại hệ thống đặt lịch gặp lỗi, mình có muốn thử lại một lát nữa không ạ?"
                        rg = shared.get("reply_gate")
                        if rg:
                            await rg.say(apology)
                        else:
                            handle = await session.generate_reply(instructions=apology)
                            await handle
                except Exception:
                    pass
            finally:
                shared["booking_in_progress"] = False

        # chạy nền
        asyncio.create_task(_run_booking())

        # Trả về ngay để LLM có thể tiếp tục nói câu giữ chân
        hold_message = "Dạ em đang tìm lịch phù hợp cho mình ạ, xin vui lòng đợi và giữ máy một chút nhé. Em sẽ thông báo lịch khám ngay khi có kết quả ạ"
        try:
            # Sử dụng talker để phát audio trực tiếp
            talker = shared.get("talker")
            if talker:
                # Tạo audio từ Google TTS một cách bất đồng bộ
                tts_client = tts.TextToSpeechAsyncClient()
                synth_input = tts.SynthesisInput(text=hold_message)
                voice = tts.VoiceSelectionParams(language_code="vi-VN", name="vi-VN-Standard-A") # Giọng nữ miền Nam
                audio_cfg = tts.AudioConfig(audio_encoding=tts.AudioEncoding.LINEAR16, sample_rate_hertz=24000)
                audio_response = await tts_client.synthesize_speech(input=synth_input, voice=voice, audio_config=audio_cfg)
                
                # Phát audio qua talker
                await talker.speak_audio(audio_response.audio_content)
            else:
                # Nếu không có talker, ghi log cảnh báo thay vì dùng fallback
                _fn_log("Warning: 'talker' object not found. Cannot play hold message directly.")

        except Exception as e:
            _fn_log(f"Error speaking hold message: {e}")
        
        return {
            "ok": True,
            "pending": True,
            # Xóa speak_text ở đây để LLM không tự ý nói lại câu giữ máy
            "instruction": "Không được cung cấp lịch khám cụ thể cho tới khi nhận booking_result. Nếu cần nói gì thêm chỉ nhắc bệnh nhân chờ.",
        }

    @function_tool
    async def choose_booking_option(
        context: RunContext,
        option_index: int,
        reason: Optional[str] = None,
    ) -> dict:
        """Chọn 1 option trong kết quả đặt lịch đa lựa chọn (0-based). Dùng sau khi model đã hiển thị các phương án.

        Nếu index không hợp lệ sẽ trả về danh sách hiện có (nếu có)."""
        latest = shared.get("latest_booking")
        if not latest:
            return {"ok": False, "error": "no_booking_options"}
        options = latest.get("options") or []
        if not options:
            return {"ok": False, "error": "no_options"}
        if option_index < 0 or option_index >= len(options):
            return {"ok": False, "error": "invalid_index", "count": len(options)}
        chosen = options[option_index]
        latest["chosen"] = chosen
        # Soft hold: cancel previous holds for this session then create new hold for chosen option
        try:
            session_id = (shared.get("session") and getattr(shared.get("session"), "id", None)) or shared.get("session_id") or "default"
            from Dashboard.schedule_logic import cancel_holds_for_session, create_hold
            cancel_holds_for_session(session_id)
            # Canonicalize department name from schedule meta using department_code
            try:
                sched = (latest.get("meta") or {}).get("schedule") or {}
                hlist = sched.get("hospitals", []) or []
                hcode = chosen.get("hospital_code") or chosen.get("hospital")
                dcode = chosen.get("department_code") or chosen.get("department")
                canon_dep = None
                for h in hlist:
                    if h.get("hospital_code") == hcode:
                        for dep in h.get("departments", []) or []:
                            if dep.get("department_code") == dcode:
                                canon_dep = dep.get("department_name") or dep.get("department")
                                break
                    if canon_dep:
                        break
                if canon_dep and chosen.get("department") != canon_dep:
                    chosen["department"] = canon_dep
                    latest["chosen"] = chosen
            except Exception:
                pass
            slot_time_full = chosen.get("slot_time") or ""
            date_part = slot_time_full.split(" ")[0]
            time_part = slot_time_full.split(" ")[-1]
            hospital_code = chosen.get("hospital_code") or chosen.get("hospital") or ""
            department = chosen.get("department") or ""
            department_code = chosen.get("department_code") or None
            doctor_name = chosen.get("doctor_name") or ""
            if hospital_code and department and doctor_name and date_part and time_part:
                ok, msg = create_hold(hospital_code, department, doctor_name, date_part, time_part, session_id, ttl_seconds=300, department_code=department_code)
                latest.setdefault("persist_result", {})["soft_hold"] = {"ok": ok, "message": msg, "session_id": session_id}
        except Exception as e:
            latest.setdefault("persist_result", {})["soft_hold_error"] = str(e)
        # Không gọi book_slot ở đây nữa; sẽ promote tại finalize_visit.
        # Không ép generate speak_text cố định nữa; để model tự nói.
        latest.pop("speak_text", None)
        try:
            # Thêm system line mô tả option đã chọn để model quan sát
            state.add("system", f"BOOKING_CHOSEN doctor={chosen.get('doctor_name')} hospital={chosen.get('hospital')} time={chosen.get('slot_time')}")
        except Exception:
            pass
        shared["latest_booking"] = latest
        shared["allow_finalize"] = True
        publish_data({
            "type": "booking_option_chosen",
            "booking": latest,
            "chosen_index": option_index,
            "reason": reason,
        })
        return {
            "ok": True,
            "chosen_index": option_index,
            "chosen": chosen,
            "options": options,
            "speak_text": latest.get("speak_text"),  # likely None now
            "message": "Đã chọn phương án đặt lịch.",
        }

    # ---------------- Finalize Tool ----------------
    @function_tool
    async def finalize_visit(context: RunContext) -> dict:
        if shared.get("closing"):
            return {"ok": False, "message": "Đang đóng phiên."}
        if not shared.get("allow_finalize") or shared.get("latest_booking") is None:
            pass
        session = shared.get("session")
        ctx = shared.get("ctx")
        latest_booking = shared.get("latest_booking")
        # --- Booking persistence (promote hold or direct) ---
        # Làm ngay đầu finalize để chắc chắn commit trước khi session bị đóng.
        persist_info = {}
        # Promote soft hold to real booking here (one-time)
        try:
            if latest_booking and isinstance(latest_booking, dict):
                chosen = latest_booking.get("chosen") or {}
                if chosen:
                    slot_time_full = chosen.get("slot_time") or ""
                    date_part = slot_time_full.split(" ")[0]
                    time_part = slot_time_full.split(" ")[-1]
                    hospital_code = chosen.get("hospital_code") or chosen.get("hospital") or ""
                    department = chosen.get("department") or ""
                    doctor_name = chosen.get("doctor_name") or ""
                    department_code = chosen.get("department_code") or None
                    if hospital_code and department and doctor_name and date_part and time_part:
                        session_id = (shared.get("session") and getattr(shared.get("session"), "id", None)) or shared.get("session_id") or "default"
                        from Dashboard.schedule_logic import promote_hold_to_booking, book_slot as _direct_book
                        print(f"[finalize_visit] promote_hold start session={session_id} {hospital_code}/{department}/{doctor_name} {date_part} {time_part} code={department_code}")
                        try:
                            ok, msg = promote_hold_to_booking(session_id, hospital_code, department, doctor_name, date_part, time_part, department_code=department_code)
                            persist_info = {"mode": "promote_hold", "ok": ok, "message": msg}
                            if not ok:
                                # Fallback direct booking
                                try:
                                    d_ok, d_msg = _direct_book(hospital_code, department, doctor_name, date_part, time_part, department_code=department_code)
                                    persist_info = {"mode": "direct_fallback", "ok": d_ok, "message": d_msg, "promote_message": msg}
                                    print(f"[finalize_visit] fallback direct booking ok={d_ok} msg={d_msg}")
                                except Exception as ee:
                                    persist_info = {"mode": "direct_fallback", "ok": False, "message": f"fallback_error: {ee}", "promote_message": msg}
                            else:
                                print(f"[finalize_visit] promote_hold success msg={msg}")
                            latest_booking.setdefault("persist_result", {})["schedule_backend"] = {"ok": persist_info.get("ok"), "message": persist_info.get("message"), "finalize_persist": True, "mode": persist_info.get("mode")}
                        except Exception as e:
                            persist_info = {"mode": "error", "ok": False, "message": str(e)}
                            latest_booking.setdefault("persist_result", {})["schedule_backend_error"] = f"{e}"
                            print(f"[finalize_visit] persist error: {e}")
        except Exception as e:
            print(f"[finalize_visit] persist attempt error: {e}")
        # Nếu không có chosen hoặc thiếu dữ liệu
        if not persist_info:
            print("[finalize_visit] no chosen option to persist")
        transcript_lines = list(state.lines)
        transcript = "\n".join(transcript_lines)
        user_only = "\n".join(line[len("[user] "): ] for line in transcript_lines if line.startswith("[user] "))
        combined = transcript + "\n\n[USER_ONLY]\n" + (user_only or "(rỗng)")
        try:
            combined += "\n\n[BOOKING_JSON]\n" + json.dumps(latest_booking, ensure_ascii=False)
        except Exception:
            pass

        ident_name = identity_state.get("patient_name") or ""
        ident_phone = identity_state.get("phone") or ""
        extract_facts_fn = shared.get("extract_facts_and_summary")
        loop = asyncio.get_running_loop()

        def _bg_finalize_thread():
            start_t = time.time()
            print("[FinalizeThread] start")
            def _coerce_text(val):
                if isinstance(val, (str, type(None))):
                    return val or ""
                try:
                    return json.dumps(val, ensure_ascii=False)
                except Exception:
                    return str(val)
            def _pretty_jsonish(text: str) -> str:
                """Convert a JSON-looking string to bullet list; else return original."""
                if not text or not isinstance(text, str):
                    return text
                s = text.strip()
                if not (s.startswith('{') and s.endswith('}')):
                    return text
                try:
                    obj = json.loads(s)
                except Exception:
                    return text
                lines = []
                def emit(prefix, value, indent=0):
                    pad = '  ' * indent
                    if isinstance(value, dict):
                        if prefix:
                            lines.append(f"{pad}- {prefix}:")
                        for k,v in value.items():
                            emit(k, v, indent+1 if prefix else indent)
                    elif isinstance(value, list):
                        if prefix:
                            lines.append(f"{pad}- {prefix}:")
                        for i, item in enumerate(value, start=1):
                            emit(f"{i}", item, indent+1)
                    else:
                        if prefix:
                            lines.append(f"{pad}- {prefix}: {value}")
                        else:
                            lines.append(f"{pad}- {value}")
                if isinstance(obj, dict):
                    for k,v in obj.items():
                        emit(k, v)
                    pretty = '\n'.join(lines)
                    return pretty if pretty else text
                return text
            try:
                # 1) Summary
                try:
                    summary_obj = summarize_visit_json(combined, clinic_defaults, latest_booking)
                except Exception as e:
                    print(f"[FinalizeThread] summarize_visit_json error: {e}")
                    summary_obj = {"raw_transcript": transcript_lines, "booking": latest_booking}
                # 2) Normalize
                normalized_summary_obj = {}
                if isinstance(summary_obj, dict):
                    for k, v in summary_obj.items():
                        if isinstance(v, (dict, list)):
                            normalized_summary_obj[k] = json.dumps(v, ensure_ascii=False)
                        else:
                            normalized_summary_obj[k] = v
                else:
                    normalized_summary_obj = {"summary_raw": _coerce_text(summary_obj)}
                patient_name = normalized_summary_obj.get("patient_name") or ident_name
                phone = normalized_summary_obj.get("phone") or ident_phone
                # 3) Customer
                try:
                    cid, _ = get_or_create_customer(patient_name, phone)
                except Exception as e:
                    print(f"[FinalizeThread] get_or_create_customer error: {e}")
                    cid = "UNKNOWN"
                # 4) Facts + summary advanced
                if extract_facts_fn:
                    try:
                        from storage import get_customer_facts_summary, update_customer_facts_summary
                        try:
                            existing_data = get_customer_facts_summary(cid)
                        except Exception as e:
                            print(f"[FinalizeThread] get_customer_facts_summary error: {e}")
                            existing_data = {}
                        existing_facts = existing_data.get("facts", "") if isinstance(existing_data, dict) else ""
                        existing_summary_text = existing_data.get("last_summary", "") if isinstance(existing_data, dict) else ""
                        try:
                            facts_result = extract_facts_fn(combined, existing_facts, existing_summary_text)
                        except Exception as e:
                            print(f"[FinalizeThread] extract_facts_fn error: {e}")
                            facts_result = {}
                        print(f"[Facts Extraction] raw_result keys: {list(facts_result.keys()) if isinstance(facts_result, dict) else type(facts_result)}")
                        new_facts = facts_result.get("facts", "") if isinstance(facts_result, dict) else ""
                        new_summary = facts_result.get("summary", "") if isinstance(facts_result, dict) else ""
                        # Coerce non-string facts/summary to JSON text early
                        if not isinstance(new_facts, (str, type(None))):
                            try:
                                new_facts = json.dumps(new_facts, ensure_ascii=False)
                            except Exception:
                                new_facts = str(new_facts)
                        if not isinstance(new_summary, (str, type(None))):
                            try:
                                new_summary = json.dumps(new_summary, ensure_ascii=False)
                            except Exception:
                                new_summary = str(new_summary)
                        def _preview(txt):
                            try:
                                return txt[:400] if isinstance(txt, str) else str(txt)[:400]
                            except Exception:
                                return "(preview_err)"
                        print(f"[Facts Extraction] facts len={len(new_facts)} preview={_preview(new_facts)}")
                        print(f"[Facts Extraction] summary len={len(new_summary)} preview={_preview(new_summary)}")
                        cfacts = _coerce_text(new_facts)
                        csummary = _coerce_text(new_summary)
                        # Pretty formatting (for storage & later injection)
                        cfacts_pretty = _pretty_jsonish(cfacts)
                        csummary_pretty = _pretty_jsonish(csummary)
                        try:
                            update_customer_facts_summary(cid, cfacts_pretty, csummary_pretty)
                        except Exception as e:
                            print(f"[Facts Extraction] update_customer_facts_summary error: {e} types: facts={type(cfacts_pretty)} summary={type(csummary_pretty)}")
                        try:
                            base_payload = normalized_summary_obj if isinstance(normalized_summary_obj, dict) else {"summary_raw": _coerce_text(normalized_summary_obj)}
                            # Build final payload structure (avoid accidental string)
                            final_payload = {
                                "patient_name": patient_name,
                                "phone": phone,
                                "booking": latest_booking,
                                "summary_struct": base_payload,
                                "facts_pretty": cfacts_pretty,
                                "summary_pretty": csummary_pretty,
                            }
                            # Derive booking_index for reliable Dashboard lookup
                            try:
                                chosen_bk = None
                                if isinstance(latest_booking, dict):
                                    chosen_bk = latest_booking.get("chosen") if isinstance(latest_booking.get("chosen"), dict) else latest_booking
                                chosen_bk = chosen_bk or {}
                                slot_time_raw = chosen_bk.get("slot_time") or chosen_bk.get("appointment_time") or base_payload.get("appointment_time") or ""
                                date_part = None; time_part = None
                                if slot_time_raw:
                                    parts = slot_time_raw.strip().split()
                                    if len(parts) == 2 and parts[1].count(":") == 1:
                                        date_part, time_part = parts[0], parts[1]
                                    elif len(parts) == 1 and parts[0].count(":") == 1:
                                        time_part = parts[0]
                                        # try pull date from latest_booking or base_payload
                                        date_part = latest_booking.get("target_date") if isinstance(latest_booking, dict) else None
                                if not date_part and isinstance(latest_booking, dict):
                                    date_part = latest_booking.get("date") or (latest_booking.get("chosen") or {}).get("date")
                                # Accept a separate field stored earlier
                                hospital_bk = chosen_bk.get("hospital_code") or chosen_bk.get("hospital") or latest_booking.get("hospital_code") if isinstance(latest_booking, dict) else None
                                department_code_bk = chosen_bk.get("department_code") or latest_booking.get("department_code") if isinstance(latest_booking, dict) else None
                                doctor_bk = chosen_bk.get("doctor_name") or base_payload.get("doctor_name")
                                booking_index = {
                                    "hospital_code": hospital_bk,
                                    "department_code": department_code_bk,
                                    "doctor_name": doctor_bk,
                                    "date": date_part,
                                    "slot_time": time_part,
                                }
                                final_payload["booking_index"] = booking_index
                                # Promote fields to top-level for simpler LIKE queries
                                if hospital_bk: final_payload["hospital_code"] = hospital_bk
                                if date_part: final_payload["date"] = date_part
                                if time_part: final_payload["slot_time"] = time_part
                            except Exception as _e_bi:
                                print(f"[FinalizeThread] booking_index build error: {_e_bi}")
                            # Extra fields from base_payload pulled up for convenience
                            for k in ("doctor_name","appointment_time","slot_time","preferred_time"):
                                if k in base_payload and k not in final_payload:
                                    final_payload[k] = base_payload[k]
                            print(f"[FinalizeThread] save_visit final_payload keys={list(final_payload.keys())}")
                            save_visit(
                                cid,
                                final_payload,
                                final=True,
                                summary=csummary_pretty,
                                facts=cfacts_pretty,
                            )
                        except Exception as e:
                            print(f"[FinalizeThread] save_visit (final) error: {e}")
                            try:
                                fallback_payload = {
                                    "patient_name": patient_name,
                                    "phone": phone,
                                    "summary_raw": _coerce_text(normalized_summary_obj),
                                    "booking": latest_booking,
                                }
                                print(f"[FinalizeThread] save_visit fallback payload type={type(fallback_payload)}")
                                save_visit(cid, fallback_payload)
                            except Exception as ee:
                                print(f"[FinalizeThread] save_visit fallback error: {ee}")
                    except Exception as e:
                        print(f"[FinalizeThread] facts pipeline error: {e}")
                        try:
                            base_payload = normalized_summary_obj if isinstance(normalized_summary_obj, dict) else {"summary_raw": _coerce_text(normalized_summary_obj)}
                            save_visit(cid, {"patient_name": patient_name, "phone": phone, "booking": latest_booking, "summary_struct": base_payload})
                        except Exception as ee:
                            print(f"[FinalizeThread] save_visit (no facts) error: {ee}")
                else:
                    try:
                            base_payload = normalized_summary_obj if isinstance(normalized_summary_obj, dict) else {"summary_raw": _coerce_text(normalized_summary_obj)}
                            save_visit(cid, {"patient_name": patient_name, "phone": phone, "booking": latest_booking, "summary_struct": base_payload})
                    except Exception as e:
                        print(f"[FinalizeThread] save_visit (no extractor) error: {e}")
            except Exception as e:
                print(f"[FinalizeThread] error: {e}")
            finally:
                dur = time.time() - start_t
                print(f"[FinalizeThread] done in {dur:.2f}s")
                async def _late_close():
                    with contextlib.suppress(Exception):
                        if session is not None:
                            await session.aclose()
                    with contextlib.suppress(Exception):
                        if ctx and ctx.room:
                            await ctx.room.disconnect()
                    state.clear()
                try:
                    asyncio.run_coroutine_threadsafe(_late_close(), loop)
                except Exception:
                    pass

        th = threading.Thread(target=_bg_finalize_thread, name="FinalizeThread", daemon=True)
        shared["finalize_thread"] = th
        th.start()
        shared["allow_finalize"] = False
        shared["latest_booking"] = None
        shared["closing"] = True
        try:
            publish_data({"type": "wrapup_done", "message": "Visit finalized; background saving"})
        except Exception:
            pass
        return {"ok": True, "message": "Finalizing in background."}

    return [propose_identity, confirm_identity, schedule_appointment, choose_booking_option, finalize_visit]


