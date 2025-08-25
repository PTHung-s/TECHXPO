# gemini_kiosk.py
# -*- coding: utf-8 -*-
import os, asyncio, re, json, contextlib, logging
from dataclasses import dataclass, field
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv(".env.local") or load_dotenv()

from livekit import agents
from livekit.agents import (
    WorkerOptions, Agent, AgentSession, JobContext,
    AutoSubscribe, RoomInputOptions, RoomOutputOptions, ChatContext,
    function_tool, RunContext
)
from livekit.plugins.google.beta import realtime
from livekit.plugins import noise_cancellation

from storage import init_db, get_or_create_customer, save_visit
from clerk_wrapup import summarize_visit_json

from med_rag import MedicalRAG
from booking import book_appointment

# ================== Cấu hình hội thoại ==================
WELCOME = (
    "Nói nguyên văn cụm này khi bắt đầu hội thoại: Xin chào! Tôi là bác sĩ ảo Minh Quang của bệnh viện Ngôi Sao. "
    "Vui lòng cho biết HỌ TÊN và SỐ ĐIỆN THOẠI để tôi hoàn thành hồ sơ nhé. "
    "Luôn chào hỏi bằng câu này khi chưa hỏi được tên và số điện thoại của bệnh nhân"
)

SYSTEM_PROMPT = """
Bạn là bác sĩ hỏi bệnh thân thiện, chuyên nghiệp và già dặn, nói ngắn gọn bằng tiếng Việt, mỗi lần chỉ hỏi một câu, trầm tính.

Mục tiêu của 1 lượt khám:
1) Hỏi và ghi nhận: họ tên, số điện thoại.
2) Khai thác TRIỆU CHỨNG (tên, mức độ) thật kĩ và nhiều nhất có thể qua trò chuyện gần gũi; khi nghi ngờ có triệu chứng khác thì chủ động hỏi thêm.
3) Khi đã đủ dữ kiện để ĐẶT LỊCH, hãy GỌI TOOL `schedule_appointment` với các tham số bạn đã nắm (ví dụ: patient_name, phone, preferred_time, symptoms). KHÔNG dùng cụm từ kích hoạt.
4) Khi đã có đủ (thông tin cá nhân + triệu chứng + lịch khám), hãy GỌI TOOL `finalize_visit` để tổng kết và kết thúc. Sau khi tool trả về, nói lời chào kết thúc ngắn gọn và KHÔNG đặt thêm câu hỏi mới.

QUY TẮC:
- Luôn tuân thủ quy chuẩn y tế nội bộ (nếu có) được cung cấp trong hội thoại.
- Tránh độc thoại dài; luôn hỏi-đáp theo lượt.
- Nhắc rõ rằng đây chỉ là hỗ trợ sơ bộ, không thay thế chẩn đoán y khoa chính thức.
""".strip()

# Logging level via env (DEBUG để theo dõi chi tiết)
logging.basicConfig(level=getattr(logging, os.getenv("KIOSK_LOG_LEVEL", "DEBUG").upper(), logging.INFO))
log = logging.getLogger("kiosk")

# ================== Bộ đệm ==================
@dataclass
class SessionBuf:
    lines: List[str] = field(default_factory=list)  # [role] text
    def add(self, role: str, text: str):
        text = (text or "").strip()
        if text:
            self.lines.append(f"[{role}] {text}")
    def clear(self):
        self.lines.clear()

# ================== Helpers log ==================
def _log_evt(tag: str, role: str, text: str, extra: str = ""):
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        log.debug("%s role=%s %s text=%r", tag, role, extra, text)

# ================== Talker (Agent) có RAG ==================
class Talker(Agent):
    def __init__(self, rag: MedicalRAG):
        super().__init__(instructions=SYSTEM_PROMPT)
        self.rag = rag

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message):
        """Chèn RAG (quy chuẩn y tế) vào system trước khi LLM trả lời."""
        user_text = (new_message.text_content or "").strip()
        if not user_text:
            return
        ctx = self.rag.query(user_text, k=4, max_chars=1200)
        if not ctx:
            return
        turn_ctx.add_message(
            role="system",
            content="Các quy chuẩn y tế nội bộ ưu tiên áp dụng (không đọc ra, chỉ tham khảo):\n" + ctx
        )
        await self.update_chat_ctx(turn_ctx)

# ================== Entrypoint ==================
async def entrypoint(ctx: JobContext):
    init_db()
    log.info("entrypoint: starting")

    # 1) Kết nối vào room
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    log.info("connected to room: %s", getattr(ctx.room, "name", "?"))

    # 2) Gemini Live API (Realtime LLM có audio & tool calling)
    # Lưu ý: ID đúng của model là 'gemini-live-2.5-flash-preview' (không phải 'gemini-2.5-flash-live-preview')
    rt_model = os.getenv("GEMINI_RT_MODEL", "gemini-live-2.5-flash-preview")
    rt_lang  = os.getenv("GEMINI_LANGUAGE", "vi-VN")  # BCP-47, ví dụ: vi-VN, en-US
    llm = realtime.RealtimeModel(
        model=rt_model,
        voice=os.getenv("GEMINI_VOICE", "Puck"),  # LiveKit plugin cho phép chọn 'Puck' mặc định
        language=rt_lang,
    )
    log.info("Realtime LLM: %s", rt_model)

    # 3) RAG engine
    rules_path = os.getenv("MED_RULES_PATH", "./med_rules")  # file hoặc thư mục
    rag = MedicalRAG(source_path=rules_path)

    # ===== State & session =====
    state = SessionBuf()
    session: Optional[AgentSession] = None

    async def start_new_session():
        nonlocal session
        if session is not None:
            with contextlib.suppress(Exception):
                await session.aclose()

        talker = Talker(rag=rag)
        session = AgentSession(llm=llm)
        room_io = RoomInputOptions(noise_cancellation=noise_cancellation.BVC())

        # === Event: transcript người dùng
        @session.on("user_input_transcribed")
        def on_user_input_transcribed(ev):
            _log_evt("EVT user_input_transcribed", "user", ev.transcript,
                     extra=f"final={getattr(ev,'is_final',None)} lang={getattr(ev,'language',None)}")
            if getattr(ev, "is_final", False):
                state.add("user", ev.transcript)

        # === Event: item assistant/user được commit vào history
        @session.on("conversation_item_added")
        def on_item_added(ev):
            role = (ev.item.role or "unknown")
            text = (getattr(ev.item, "text_content", "") or "").strip()
            if text:
                _log_evt("EVT conversation_item_added", role, text)
                state.add(role, text)

        # === Event: item assistant/user được cập nhật
        @session.on("conversation_item_updated")
        def on_item_updated(ev):
            role = (ev.item.role or "unknown")
            text = (getattr(ev.item, "text_content", "") or "").strip()
            if text:
                _log_evt("EVT conversation_item_updated", role, text)
                state.add(role, text)

        # ---------- Tool definitions (function calling) ----------
        # Đăng ký tool bên trong để có thể "bắt" state/session qua closure

        @function_tool
        async def schedule_appointment(
            context: RunContext,
            patient_name: str,
            phone: str,
            preferred_time: Optional[str] = None,
            symptoms: Optional[str] = None,
        ) -> dict:
            """
            Đặt lịch khám cho bệnh nhân.
            Sử dụng khi bạn (model) đã thu thập được họ tên, số điện thoại và (nếu có) thời gian mong muốn + tóm tắt triệu chứng.
            Trả về JSON chứa thông tin lịch hẹn và gợi ý câu nói xác nhận cho bệnh nhân.
            """
            history = "\n".join(state.lines)
            data_path = os.getenv("CLINIC_DATA_PATH", "./clinic_data.json")

            # Thực hiện đặt lịch (dùng model text 'gemini-2.5-flash' trong module booking.py như trước)
            result = await asyncio.to_thread(
                book_appointment, history, data_path, "gemini-2.5-pro"
            )

            # Lưu CSDL khách
            try:
                cid, _ = get_or_create_customer(
                    result.get("patient_name") or patient_name or "",
                    result.get("phone") or phone or "",
                )
                result["customer_id"] = cid
                await asyncio.to_thread(save_visit, cid, {"booking": result})
            except Exception:
                pass

            return {
                "ok": True,
                "booking": result,
                # Model sẽ đọc/diễn đạt phần speak_text này theo cách tự nhiên trong câu trả lời kế tiếp
                "speak_text": result.get("speak_text") or "Tôi đã sắp xếp lịch phù hợp. Cảm ơn bạn."
            }

        @function_tool
        async def finalize_visit(context: RunContext) -> dict:
            """
            Tổng hợp hồ sơ, lưu vào CSDL và reset phiên làm việc để tiếp bệnh nhân kế tiếp.
            Gọi tool này khi (model) đã thu thập đủ thông tin và đã đặt lịch thành công.
            Sau khi tool trả về, hãy nói lời chào kết thúc ngắn gọn (ví dụ: 'Cảm ơn bạn đã đến khám. Chúc bạn mau khỏe!').
            """
            transcript_lines = list(state.lines)
            transcript = "\n".join(transcript_lines)
            user_only = "\n".join(
                line[len("[user] "):] for line in transcript_lines if line.startswith("[user] ")
            )
            combined = transcript + "\n\n[USER_ONLY]\n" + (user_only or "(rỗng)")

            clinic_defaults = {
                "doctor_name": str(os.getenv("CLINIC_DOCTOR", "Bác sĩ trực")),
                "appointment_time": "(không rõ)",
                "diet_notes": "",
            }

            async def _do_wrap_up_and_reset():
                try:
                    print("===== WRAP UP (snapshot) =====")
                    print(combined)
                    summary = await asyncio.to_thread(summarize_visit_json, combined, clinic_defaults)
                    patient_name = summary.get("patient_name") or ""
                    phone = summary.get("phone") or ""
                    cid, _ = await asyncio.to_thread(get_or_create_customer, patient_name, phone)
                    summary["customer_id"] = cid
                    await asyncio.to_thread(save_visit, cid, summary)
                    log.info("visit saved: cid=%s", cid)
                except Exception as e:
                    fallback = {"error": str(e), "raw_transcript": transcript_lines, "user_only": user_only}
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(save_visit, "UNKNOWN", fallback)
                    log.exception("Wrap-up error: %s", e)
                finally:
                    # Đợi ngắn để đảm bảo model đã nói xong lời chào
                    await asyncio.sleep(1.2)
                    with contextlib.suppress(Exception):
                        if session is not None:
                            await session.aclose()
                    state.clear()
                    await asyncio.sleep(0.6)
                    await start_new_session()

            # Chạy wrap-up ở background để không chặn luồng tool-calling
            asyncio.create_task(_do_wrap_up_and_reset())
            return {"ok": True, "message": "Visit finalized and session will reset."}

        # Cập nhật tool vào agent (đồng bộ với phiên realtime hiện tại)
        await talker.update_tools([schedule_appointment, finalize_visit])

        # Khởi động phiên
        await session.start(
            room=ctx.room,
            agent=talker,
            room_input_options=room_io,
            room_output_options=RoomOutputOptions(
                transcription_enabled=True,
                audio_enabled=True,
            ),
        )

        # Chào đầu
        try:
            handle = await session.generate_reply(instructions=WELCOME)
            await handle
        except Exception as e:
            logging.warning("welcome failed: %s", e)

    # Khởi động
    await start_new_session()

if __name__ == "__main__":
    agents.cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
