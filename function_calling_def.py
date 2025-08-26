import re
import os
import json
import asyncio
import contextlib
from typing import Optional, Callable, Dict, Any, List
from livekit.agents import function_tool, RunContext

# Regex helpers
NAME_RE = re.compile(r"\b([A-ZÀ-ỴĐ][a-zà-ỹđ]+(?:\s+[A-ZÀ-ỴĐ][a-zà-ỹđ]+){1,5})\b")
PHONE_RE_FULL = re.compile(r"^(\+?84|0)(3|5|7|8|9)\d{8}$")
PHONE_RE_BASIC = re.compile(r"^(\+?84|0)(3|5|7|8|9)\d{8}$")

def build_all_tools(
    publish_data: Callable[[Dict[str, Any]], asyncio.Task],
    identity_state: Dict[str, Any],
    shared: Dict[str, Any],
    *,
    state,  # SessionBuf
    book_appointment,
    get_or_create_customer,
    save_visit,
    summarize_visit_json,
    clinic_defaults: Dict[str, Any],
):
    """Build all function-calling tools (identity + booking + finalize).

    shared keys expected: latest_booking, allow_finalize, closing, session, ctx
    identity_state keys: patient_name, patient_phone, draft_name, draft_phone, draft_conf, identity_confirmed
    """

    # -------- Identity Tools --------
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
        if identity_state.get("identity_confirmed"):
            return {"status": "already_confirmed", "patient_name": identity_state.get("patient_name"), "phone": identity_state.get("patient_phone")}
        if patient_name_input:
            identity_state["patient_name"] = patient_name_input.strip()
        elif identity_state.get("draft_name") and not identity_state.get("patient_name"):
            identity_state["patient_name"] = identity_state.get("draft_name")
        if phone_input and PHONE_RE_FULL.match(phone_input.strip()):
            identity_state["patient_phone"] = phone_input.strip()
        elif identity_state.get("draft_phone") and not identity_state.get("patient_phone"):
            identity_state["patient_phone"] = identity_state.get("draft_phone")
        if confirm and identity_state.get("patient_name") and identity_state.get("patient_phone"):
            identity_state["identity_confirmed"] = True
            payload = {
                "type": "identity_confirmed",
                "patient_name": identity_state.get("patient_name"),
                "phone": identity_state.get("patient_phone"),
                "confidence": identity_state.get("draft_conf", 1.0),
                "confirmed": True,
            }
            publish_data(payload)
            return {"status": "confirmed", **payload}
        return {
            "status": "pending",
            "patient_name": identity_state.get("patient_name") or identity_state.get("draft_name"),
            "phone": identity_state.get("patient_phone") or identity_state.get("draft_phone"),
        }

    # -------- Booking Tool --------
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
        raw_phone = (phone or "").strip()
        raw_name = (patient_name or "").strip() or "(không rõ)"
        if not PHONE_RE_BASIC.match(raw_phone):
            raw_phone = raw_phone or "(không rõ)"
        if symptoms:
            state.add("user", f"Triệu chứng khai báo: {symptoms}")
        history = "\n".join(state.lines)
        data_path = os.getenv("CLINIC_DATA_PATH", "./clinic_data.json")
        book_model = os.getenv("BOOK_MODEL", "gemini-2.5-flash")

        async def _do_booking():
            try:
                result = await asyncio.to_thread(book_appointment, history, data_path, book_model)
                if symptoms and not result.get("symptoms"):
                    result["symptoms"] = symptoms
                try:
                    cid, _ = get_or_create_customer(result.get("patient_name") or raw_name, result.get("phone") or raw_phone)
                    result["customer_id"] = cid
                    await asyncio.to_thread(save_visit, cid, {"booking": result}, final=False)
                except Exception:
                    pass
                shared["latest_booking"] = result
                shared["allow_finalize"] = True
                publish_data({"type": "booking_result", "booking": result})
                speak_text = (result.get("speak_text") or "Tôi đã sắp xếp lịch phù hợp. Cảm ơn bạn.").strip()
                session = shared.get("session")
                if session is not None:
                    try:
                        handle = await session.generate_reply(instructions=(speak_text))
                        await handle
                    except Exception:
                        pass
            except Exception:
                pass

        publish_data({
            "type": "booking_pending",
            "patient_name": raw_name,
            "phone": raw_phone,
            "preferred_time": preferred_time,
        })
        asyncio.create_task(_do_booking())
        return {"ok": True, "pending": True, "message": "Đang kiểm tra lịch phù hợp hãy chờ một chút nhé."}

    # -------- Finalize Tool --------
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
        user_only = "\n".join(line[len("[user] "):] for line in transcript_lines if line.startswith("[user] "))
        combined = transcript + "\n\n[USER_ONLY]\n" + (user_only or "(rỗng)")
        try:
            combined += "\n\n[BOOKING_JSON]\n" + json.dumps(latest_booking, ensure_ascii=False)
        except Exception:
            pass
        try:
            summary = await asyncio.to_thread(summarize_visit_json, combined, clinic_defaults, latest_booking)
            patient_name = summary.get("patient_name") or ""
            phone = summary.get("phone") or ""
            cid, _ = await asyncio.to_thread(get_or_create_customer, patient_name, phone)
            summary["customer_id"] = cid
            await asyncio.to_thread(save_visit, cid, summary)
        except Exception:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(save_visit, "UNKNOWN", {"raw_transcript": transcript_lines, "booking": latest_booking})
        finally:
            shared["allow_finalize"] = False
            shared["latest_booking"] = None
            shared["closing"] = True

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

    return [propose_identity, confirm_identity, schedule_appointment, finalize_visit]
