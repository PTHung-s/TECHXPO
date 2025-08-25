# gemini_kiosk_optimized.py
# -*- coding: utf-8 -*-
"""
Bác sĩ ảo (Realtime) dùng Gemini Live API + LiveKit Agents
- Realtime LLM (voice) + function calling (schedule_appointment, finalize_visit)
- RAG chèn theo lượt (system) ngay trước khi LLM trả lời
- Bộ đệm hội thoại chống trùng lặp
- Kết thúc phiên an toàn sau khi nói lời chào
"""
from __future__ import annotations

import os
import re
import json
import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Set

from dotenv import load_dotenv
load_dotenv(".env.local") or load_dotenv()

from livekit import agents
from livekit.agents import (
    WorkerOptions, Agent, AgentSession, JobContext,
    AutoSubscribe, RoomInputOptions, RoomOutputOptions, ChatContext,
    function_tool, RunContext,
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

SYSTEM_PROMPT = (
    """
    Bạn là bác sĩ hỏi bệnh thân thiện, chuyên nghiệp và già dặn, nói ngắn gọn bằng tiếng Việt, mỗi lần chỉ hỏi một câu, trầm tính.

    Mục tiêu của 1 lượt khám:
    1) Hỏi và ghi nhận: họ tên, số điện thoại.
    2) Khai thác TRIỆU CHỨNG (tên, mức độ) thật kĩ và nhiều nhất có thể qua trò chuyện gần gũi; khi nghi ngờ có triệu chứng khác thì chủ động hỏi thêm.
    3) Khi đã đủ dữ kiện để ĐẶT LỊCH, hãy GỌI TOOL `schedule_appointment` với các tham số bạn đã nắm (ví dụ: patient_name, phone, preferred_time, symptoms). KHÔNG dùng cụm từ kích hoạt.
    4) Hỏi lại xem Booking có cần sự thay đổi gì không.
    5) KHI ĐÃ ĐỦ dữ kiện (đủ danh tính + triệu chứng + booking đã xác nhận), THỰC HIỆN THỨ TỰ SAU: (a) Nói một LỜI CHÀO KẾT THÚC NGẮN GỌN bằng tiếng Việt (không hỏi thêm, không giới thiệu lại, không yêu cầu phản hồi), sau đó (b) NGAY LẬP TỨC GỌI TOOL `finalize_visit` để tổng kết và đóng phiên. KHÔNG nói thêm gì sau khi đã gọi tool.

    QUY TẮC:
    - Luôn tuân thủ quy chuẩn y tế nội bộ (nếu có) được cung cấp trong hội thoại.
    - Tránh độc thoại dài; luôn hỏi-đáp theo lượt.
    - Nhắc rõ rằng đây chỉ là hỗ trợ sơ bộ, không thay thế chẩn đoán y khoa chính thức.
    """
    .strip()
)

# Logging
logging.basicConfig(level=getattr(logging, os.getenv("KIOSK_LOG_LEVEL", "DEBUG").upper(), logging.INFO))
log = logging.getLogger("kiosk")

# ================== Bộ đệm ==================
@dataclass
class SessionBuf:
    lines: List[str] = field(default_factory=list)  # dạng: "[role] text"
    seen_ids: Set[str] = field(default_factory=set)

    def add(self, role: str, text: str):
        text = (text or "").strip()
        if text:
            self.lines.append(f"[{role}] {text}")

    def add_once(self, item_id: Optional[str], role: str, text: str):
        if item_id and item_id in self.seen_ids:
            return
        if item_id:
            self.seen_ids.add(item_id)
        self.add(role, text)

    def clear(self):
        self.lines.clear()
        self.seen_ids.clear()

# ================== Helpers log ==================
def _log_evt(tag: str, role: str, text: str, extra: str = ""):
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        log.debug("%s role=%s %s text=%r", tag, role, extra, text)

# ================== Talker (Agent) có RAG ==================
class Talker(Agent):
    def __init__(self, rag: MedicalRAG, buf: SessionBuf):
        super().__init__(instructions=SYSTEM_PROMPT)
        self.rag = rag
        self.buf = buf  # để ép lưu user transcript nếu realtime không push event user

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message):
        """Chèn RAG + đảm bảo có ghi lại văn bản user vào bộ đệm.

        Một số cấu hình realtime có thể không bắn sự kiện text user đầy đủ; ta gom fallback.
        """
        user_text = (getattr(new_message, "text_content", "") or "").strip()
        if not user_text:
            # Fallback: gom tất cả user_messages trong turn
            collected = []
            for m in getattr(turn_ctx, "user_messages", []) or []:
                t = (getattr(m, "text_content", "") or "").strip()
                if t:
                    collected.append(t)
            user_text = "\n".join(collected).strip()

        # Nếu vẫn chưa có user_text thì bỏ qua phần RAG (không cần query)
        if not user_text:
            return

        # Cố gắng tránh nhân đôi: chỉ thêm nếu dòng cuối khác
        if not self.buf.lines or not self.buf.lines[-1].endswith(user_text):
            self.buf.add("user", user_text)

        # RAG context
        ctx_text = self.rag.query(user_text, k=4, max_chars=900)
        if not ctx_text:
            return
        turn_ctx.add_message(
            role="system",
            content=("Các quy chuẩn y tế nội bộ ưu tiên áp dụng (không đọc ra, chỉ tham khảo):\n" + ctx_text),
        )
        await self.update_chat_ctx(turn_ctx)

# ================== Entrypoint ==================
async def entrypoint(ctx: JobContext):
    init_db()
    log.info("entrypoint: starting")

    # 1) Kết nối vào room
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    log.info("connected to room: %s", getattr(ctx.room, "name", "?"))

    # 1b) Load clinic_defaults (cấu hình / thông tin tĩnh) dùng cho summarize_visit_json
    clinic_defaults_path = os.getenv("CLINIC_DATA_PATH", "./clinic_data.json")
    try:
        with open(clinic_defaults_path, "r", encoding="utf-8") as f:
            clinic_defaults = json.load(f)
    except Exception as e:
        log.warning("Không đọc được clinic defaults (%s): %s", clinic_defaults_path, e)
        clinic_defaults = {}

    # 2) Gemini Live API (Realtime LLM có audio & tool calling)
    # Tên model theo docs: gemini-live-2.5-flash-preview (voice/video + tool calling)
    rt_model = os.getenv("GEMINI_RT_MODEL", "gemini-live-2.5-flash-preview")
    rt_lang = os.getenv("GEMINI_LANGUAGE", "vi-VN")  # BCP-47

    llm = realtime.RealtimeModel(
        model=rt_model,
        voice=os.getenv("GEMINI_VOICE", "Puck"),  # "Puck" là mặc định ổn định
        language=rt_lang,
    )
    log.info("Realtime LLM: %s", rt_model)

    # 3) RAG engine
    rules_path = os.getenv("MED_RULES_PATH", "./med_rules")  # file hoặc thư mục
    rag = MedicalRAG(source_path=rules_path)

    # ===== State & session =====
    state = SessionBuf()
    session: Optional[AgentSession] = None

    # NEW: giữ booking gần nhất + cờ gate finalize
    latest_booking: Optional[dict] = None
    allow_finalize: bool = False
    closing: bool = False   # <--- thêm cờ

    async def start_new_session():
        nonlocal session, latest_booking, allow_finalize, closing
        if session is not None:
            with contextlib.suppress(Exception):
                await session.aclose()
        talker = Talker(rag=rag, buf=state)
        session = AgentSession(llm=llm)

        room_io = RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        )

        # ---------- Event handlers ----------
        @session.on("conversation_item_added")
        def on_item_added(ev):
            if closing:
                return  # bỏ qua sự kiện sau khi wrap-up
            role = (ev.item.role or "unknown")
            text = (getattr(ev.item, "text_content", "") or "").strip()
            iid = getattr(ev.item, "id", None)
            if text:
                _log_evt("EVT conversation_item_added", role, text)
                state.add_once(iid, role, text)

        @session.on("conversation_item_updated")
        def on_item_updated(ev):
            role = (ev.item.role or "unknown")
            text = (getattr(ev.item, "text_content", "") or "").strip()
            if text:
                _log_evt("EVT conversation_item_updated", role, text)

        # ---------- Tool definitions (function calling) ----------
        PHONE_RE = re.compile(r"^(\+?84|0)(3|5|7|8|9)\d{8}$")

        @function_tool
        async def schedule_appointment(
            context: RunContext,
            patient_name: str,
            phone: str,
            preferred_time: Optional[str] = None,
            symptoms: Optional[str] = None,
        ) -> dict:
            """
            Đặt lịch khám (chạy nền). Trả về ngay trạng thái 'pending' để agent không bị "đóng băng".
            Khi xử lý xong sẽ tự gửi một lượt assistant với speak_text và cập nhật allow_finalize.
            """
            nonlocal latest_booking, allow_finalize

            # Snapshot inputs (không tin cậy vào model arguments hoàn toàn)
            raw_phone = (phone or "").strip()
            raw_name = (patient_name or "").strip()
            if not raw_name:
                raw_name = "(không rõ)"
            if not PHONE_RE.match(raw_phone):
                raw_phone = raw_phone or "(không rõ)"

            if symptoms:
                state.add("user", f"Triệu chứng khai báo: {symptoms}")

            history = "\n".join(state.lines)
            data_path = os.getenv("CLINIC_DATA_PATH", "./clinic_data.json")
            book_model = os.getenv("BOOK_MODEL", "gemini-2.5-flash")

            async def _do_booking():
                nonlocal latest_booking, allow_finalize
                try:
                    result = await asyncio.to_thread(
                        book_appointment, history, data_path, book_model
                    )
                    if symptoms and not result.get("symptoms"):
                        result["symptoms"] = symptoms
                    try:
                        cid, _ = get_or_create_customer(
                            result.get("patient_name") or raw_name,
                            result.get("phone") or raw_phone,
                        )
                        result["customer_id"] = cid
                        await asyncio.to_thread(save_visit, cid, {"booking": result})
                    except Exception:
                        log.exception("save booking failed")
                    latest_booking = result
                    allow_finalize = True
                    # Phát speak_text cho bệnh nhân: dùng generate_reply (không chặn booking tool ban đầu)
                    speak_text = (result.get("speak_text") or "Tôi đã sắp xếp lịch phù hợp. Cảm ơn bạn.").strip()
                    # SPEAK_TEXT reliability patch: ép model đọc NGUYÊN VĂN, tránh tự tóm tắt hay bỏ qua
                    primary_instr = (
                        "Hãy ĐỌC NGUYÊN VĂN (bắt buộc, không thêm bớt, không giải thích) thông báo lịch hẹn sau đây cho bệnh nhân,"
                        " rồi dừng lại để họ có thể phản hồi. Thông báo: \n" + speak_text
                    )
                    try:
                        if session is not None:
                            handle = await session.generate_reply(instructions=primary_instr)
                            await handle
                            # Fallback: nếu buffer chưa ghi nhận dòng assistant chứa phần chính của speak_text, thử lại tối giản
                            key_fragment = speak_text[:30]  # đoạn đầu để dò
                            if not any(key_fragment in ln for ln in state.lines[-5:]):
                                fallback_instr = "Đọc nguyên văn: " + speak_text
                                try:
                                    fh = await session.generate_reply(instructions=fallback_instr)
                                    await fh
                                except Exception:
                                    log.exception("fallback speak_text failed")
                    except Exception:
                        log.exception("send speak_text failed")
                except Exception:
                    log.exception("async booking failed")

            # Khởi động booking ở background, trả kết quả pending ngay
            asyncio.create_task(_do_booking())

            return {
                "ok": True,
                "pending": True,
                "message": "Đang kiểm tra lịch phù hợp, vui lòng đợi trong giây lát...",
                "note": "Agent sẽ thông báo lịch và sau đó có thể finalize nếu đủ dữ kiện."
            }


        @function_tool
        async def finalize_visit(context: RunContext) -> dict:
            nonlocal latest_booking, allow_finalize, session, closing
            if closing:
                return {"ok": False, "message": "Đang đóng phiên."}

            await context.wait_for_playout()

            if not allow_finalize or latest_booking is None:
                return {"ok": False, "message": "Chưa thể kết thúc: chưa có booking hợp lệ."}

            transcript_lines = list(state.lines)
            transcript = "\n".join(transcript_lines)
            user_only = "\n".join(
                line[len("[user] "):] for line in transcript_lines if line.startswith("[user] ")
            )
            combined = transcript + "\n\n[USER_ONLY]\n" + (user_only or "(rỗng)")

            # Thêm block BOOKING rõ ràng để summarize dễ lấy
            try:
                combined += "\n\n[BOOKING_JSON]\n" + json.dumps(latest_booking, ensure_ascii=False)
            except Exception:
                pass

            try:
                log.info("===== WRAP UP (snapshot) =====\n%s", combined)
                summary = await asyncio.to_thread(
                    summarize_visit_json, combined, clinic_defaults, latest_booking
                )
                log.debug("wrapup summary=%s", json.dumps(summary, ensure_ascii=False))
                patient_name = summary.get("patient_name") or ""
                phone = summary.get("phone") or ""
                cid, _ = await asyncio.to_thread(get_or_create_customer, patient_name, phone)
                summary["customer_id"] = cid
                await asyncio.to_thread(save_visit, cid, summary)
                log.info("visit saved: cid=%s", cid)
            except Exception as e:
                fallback = {
                    "error": str(e),
                    "raw_transcript": transcript_lines,
                    "user_only": user_only,
                    "booking": latest_booking,
                }
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(save_visit, "UNKNOWN", fallback)
                log.exception("Wrap-up error: %s", e)
            finally:
                allow_finalize = False
                latest_booking = None
                closing = True  # bật cờ đóng

                async def _grace_close():
                    # 1) gửi tín hiệu cho web
                    try:
                        payload = json.dumps({
                            "type": "wrapup_done",
                            "message": "Visit finalized; closing"
                        }, ensure_ascii=False).encode("utf-8")
                        with contextlib.suppress(Exception):
                            await ctx.room.local_participant.publish_data(payload)
                    except Exception:
                        log.exception("publish wrapup_done failed")
                    # 2) chờ cho web xử lý
                    await asyncio.sleep(float(os.getenv("SESSION_CLOSE_DELAY", "1.0")))
                    # 3) đóng session
                    with contextlib.suppress(Exception):
                        if session is not None:
                            await session.aclose()
                    # 4) rời phòng
                    with contextlib.suppress(Exception):
                        await ctx.room.disconnect()
                    state.clear()
                    log.info("Session & room disconnected after wrap-up.")

                asyncio.create_task(_grace_close())

            return {"ok": True, "message": "Visit finalized; closing."}

        # Cập nhật tool vào agent (đồng bộ với phiên realtime hiện tại)
        await talker.update_tools([schedule_appointment, finalize_visit])

        # Khởi động phiên
        await session.start(
            room=ctx.room,
            agent=talker,
            room_input_options=room_io,
            room_output_options=RoomOutputOptions(
                transcription_enabled=(os.getenv("LK_TRANSCRIPTION", "0") == "1"),
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
    agents.cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=os.getenv("AGENT_NAME", "kiosk"),  # 👈 cho phép dispatch theo tên
        )
    )