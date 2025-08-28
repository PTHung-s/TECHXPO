import re
import os
import json
import asyncio
import contextlib
from typing import Optional, Callable, Dict, Any
from livekit.agents import function_tool, RunContext
from storage import get_customer_by_phone, build_personal_context, get_recent_visits

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
                                # Đánh dấu cần gửi lời chào follow-up tự động
                                shared["needs_personal_greet"] = True
                            except Exception:
                                pass
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
        if not identity_state.get("identity_confirmed"):
            return {"ok": False, "error": "identity_not_confirmed", "message": "Chưa xác nhận họ tên & SĐT."}
        prev = shared.get("latest_booking")
        if prev and preferred_time and prev.get("preferred_time") == preferred_time:
            return {"ok": False, "error": "duplicate_booking", "message": "Lịch này đã được đặt, hãy chọn thời điểm khác."}
        raw_phone = (phone or "").strip()
        raw_name = (patient_name or "").strip() or "(không rõ)"
        if not PHONE_RE_BASIC.match(raw_phone):
            raw_phone = raw_phone or "(không rõ)"
        if symptoms:
            state.add("user", f"Triệu chứng khai báo: {symptoms}")
        
        # Get RAG guidelines context for booking decision
        history = "\n".join(state.lines)
        rag = shared.get("rag")
        if rag and symptoms:
            # Query guidelines for symptom-specific booking advice
            guideline_ctx = rag.query(symptoms, k=3, max_chars=600)
            if guideline_ctx and "[GUIDELINES]" in guideline_ctx:
                history += f"\n\n[MEDICAL_GUIDELINES]\n{guideline_ctx}\n[/MEDICAL_GUIDELINES]"
        
        data_path = os.getenv("CLINIC_DATA_PATH", "./clinic_data.json")
        book_model = os.getenv("BOOK_MODEL", "gemini-2.5-flash")
        # Multi-hospital support: CLINIC_DATA_PATHS env (comma separated) OR default known sample files
        extra_paths_env = os.getenv("CLINIC_DATA_PATHS", "")
        extra_paths = []
        if extra_paths_env.strip():
            extra_paths = [p.strip() for p in extra_paths_env.split(",") if p.strip() and p.strip() != data_path]
        else:
            # Auto-detect common multi hospital sample files co-located with primary
            defaults = [
                "./clinic_data_hospitalA.json",
                "./clinic_data_hospitalB.json",
                "./clinic_data_hospitalC.json",
            ]
            for p in defaults:
                if os.path.exists(p) and p != data_path:
                    extra_paths.append(p)

        # Thay vì chạy nền (model không thấy kết quả), ta làm đồng bộ để tool result chứa toàn bộ options.
        publish_data({
            "type": "booking_pending",
            "patient_name": raw_name,
            "phone": raw_phone,
            "preferred_time": preferred_time,
        })
        try:
            result = await asyncio.to_thread(
                book_appointment,
                history,
                data_path,
                book_model,
                extra_paths,
            )
        except Exception as e:
            return {"ok": False, "error": "booking_failed", "message": str(e)}

        if symptoms and not result.get("symptoms"):
            result["symptoms"] = symptoms
        shared["latest_booking"] = result
        shared["allow_finalize"] = True
        publish_data({
            "type": "booking_result",
            "booking": result,
            "multi": bool(result.get("options")),
        })
        # Trả về đầy đủ để LLM đọc được tất cả option (options + chosen + speak_text)
        return {
            "ok": True,
            "booking": result,
            "multi": bool(result.get("options")),
            "speak_text": result.get("speak_text"),
            "message": "Đã lấy danh sách lựa chọn lịch (có thể dùng choose_booking_option để đổi).",
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
        # regenerate speak_text concise
        try:
            from booking import _build_speak_text  # reuse helper
            latest["speak_text"] = _build_speak_text(chosen)
        except Exception:
            latest["speak_text"] = f"Đã chọn lịch với {chosen.get('doctor_name')} tại {chosen.get('hospital')} lúc {chosen.get('slot_time')}"
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
            "speak_text": latest.get("speak_text"),
            "message": "Đã chọn phương án đặt lịch.",
        }

    # ---------------- Finalize Tool ----------------
    @function_tool
    async def finalize_visit(context: RunContext) -> dict:
        if shared.get("closing"):
            return {"ok": False, "message": "Đang đóng phiên."}
        await context.wait_for_playout()
        if not shared.get("allow_finalize") or shared.get("latest_booking") is None:
            return {"ok": False, "message": "Chưa thể kết thúc: chưa có booking hợp lệ."}
        session = shared.get("session")
        ctx = shared.get("ctx")
        latest_booking = shared.get("latest_booking")
        transcript_lines = list(state.lines)
        transcript = "\n".join(transcript_lines)
        user_only = "\n".join(line[len("[user] "): ] for line in transcript_lines if line.startswith("[user] "))
        combined = transcript + "\n\n[USER_ONLY]\n" + (user_only or "(rỗng)")
        try:
            combined += "\n\n[BOOKING_JSON]\n" + json.dumps(latest_booking, ensure_ascii=False)
        except Exception:
            pass
        
        try:
            summary = await asyncio.to_thread(summarize_visit_json, combined, clinic_defaults, latest_booking)
            patient_name = summary.get("patient_name") or identity_state.get("patient_name") or ""
            phone = summary.get("phone") or identity_state.get("phone") or ""
            cid, _ = await asyncio.to_thread(get_or_create_customer, patient_name, phone)
            summary["customer_id"] = cid
            
            # Extract facts and summary using facts_extractor
            extract_facts_fn = shared.get("extract_facts_and_summary")
            if extract_facts_fn:
                try:
                    # Get existing facts and summary
                    from storage import get_customer_facts_summary, update_customer_facts_summary
                    existing_data = await asyncio.to_thread(get_customer_facts_summary, cid)
                    
                    # Extract new facts and summary
                    facts_result = await asyncio.to_thread(
                        extract_facts_fn,
                        combined,  # new conversation
                        existing_data.get("facts", ""),  # existing facts
                        existing_data.get("last_summary", "")  # existing summary
                    )
                    
                    new_facts = facts_result.get("facts", "")
                    new_summary = facts_result.get("summary", "")

                    if os.getenv("KIOSK_DEBUG_PERSONAL", "0") == "1":
                        def _trunc(txt: str, lim: int = 600):
                            return txt if len(txt) <= lim else txt[:lim] + " ...[TRUNCATED]"
                        print(f"[Facts Extraction DEBUG cid={cid}] NEW_FACTS=\n{_trunc(new_facts)}")
                        print(f"[Facts Extraction DEBUG cid={cid}] NEW_SUMMARY=\n{_trunc(new_summary)}")
                    
                    # Update customer facts and summary
                    await asyncio.to_thread(update_customer_facts_summary, cid, new_facts, new_summary)
                    
                    # Save visit with extracted facts and summary
                    await asyncio.to_thread(save_visit, cid, summary, final=True, summary=new_summary, facts=new_facts)
                    
                    print(f"[Facts Extraction] Updated customer {cid} facts_len={len(new_facts)} summary_len={len(new_summary)}")
                    
                except Exception as e:
                    print(f"[Facts Extraction] Error: {e}")
                    # Fallback to regular save
                    await asyncio.to_thread(save_visit, cid, summary)
            else:
                # Fallback if extract_facts_and_summary not available
                await asyncio.to_thread(save_visit, cid, summary)
                
        except Exception:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(save_visit, "UNKNOWN", {"raw_transcript": transcript_lines, "booking": latest_booking})
        finally:
            shared["allow_finalize"] = False
            shared["latest_booking"] = None
            shared["closing"] = True

            # Personalization reset removed (feature disabled)

            async def _grace_close():
                try:
                    publish_data({"type": "wrapup_done", "message": "Visit finalized; closing"})
                except Exception:
                    pass
                await asyncio.sleep(float(os.getenv("SESSION_CLOSE_DELAY", "1.0")))
                with contextlib.suppress(Exception):
                    if session is not None:
                        await session.aclose()
                with contextlib.suppress(Exception):
                    if ctx and ctx.room:
                        await ctx.room.disconnect()
                state.clear()
            asyncio.create_task(_grace_close())
        return {"ok": True, "message": "Visit finalized; closing."}

    return [propose_identity, confirm_identity, schedule_appointment, choose_booking_option, finalize_visit]
