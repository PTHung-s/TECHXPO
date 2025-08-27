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
)
from livekit.plugins.google.beta import realtime
from livekit.plugins import noise_cancellation

from storage import init_db, get_or_create_customer, save_visit  # pared down (remove personalization build)
from function_calling_def import build_all_tools
from facts_extractor import extract_facts_and_summary  # for personalization injection & later finalize
from clerk_wrapup import summarize_visit_json
from med_rag import MedicalRAG
from booking import book_appointment

# ================== Cấu hình hội thoại ==================
WELCOME = (
    "Nói nguyên văn cụm này khi bắt đầu hội thoại: Dạ Alo! Nhân viên của bệnh viện Ngôi Sao xin nghe ạ. Dạ em có thể hỗ trợ gì ạ"
    "Luôn bắt đầu cuộc hội thoại bằng câu chào đó"
)

SYSTEM_PROMPT = (
    """
    Bạn là bác sĩ hỏi bệnh thân thiện, chuyên nghiệp và già dặn, nói ngắn gọn bằng tiếng Việt, mỗi lần chỉ hỏi một câu, trầm tính, không bịa thông tin nếu không biết.

    Mục tiêu của 1 lượt khám:
    1) Hỏi và ghi nhận: họ tên, số điện thoại. Sau khi nhận được thông tin thì phải hỏi đúng chưa coi có sai thông tin không, nếu sai thì gọi lại function propose_identity và truyền tham số để sửa cho đến khi bệnh nhân kêu đúng rồi.
    2) Nếu là khách quen và bạn từng trò chuyện rồi thì hỏi thăm vấn đề cũ, nếu không có thì thôi.
    3) Khai thác TRIỆU CHỨNG (tên, mức độ) thật kĩ và nhiều nhất có thể qua trò chuyện gần gũi; khi nghi ngờ có triệu chứng khác thì chủ động hỏi thêm.
    4) Khi đã đủ dữ kiện để ĐẶT LỊCH, hãy GỌI TOOL `schedule_appointment` với các tham số bạn đã nắm.
    5) Hỏi lại xem Booking có cần sự thay đổi gì không.
    6) KHI bệnh nhân đã đồng ý chốt về lịch khám, hãy dựa vào triệu chứng để đưa ra lời dặn dò phù hợp, chúc bệnh nhân phù hợp rồi xin chào và kết thúc phiên gọi bằng cách gọi hàm finalize_visit.

    QUY TẮC:
    - Luôn tuân thủ quy chuẩn y tế nội bộ (nếu có) được cung cấp trong hội thoại.
    - Tránh độc thoại dài; luôn hỏi-đáp theo lượt.
    - Nhắc rõ rằng đây chỉ là hỗ trợ sơ bộ, không thay thế chẩn đoán y khoa chính thức.
    - QUY TẮC DANH TÍNH (TOOLS): BẤT KỲ khi nào bạn NGHE hoặc NGHĨ rằng bệnh nhân vừa nêu MỚI hoặc SỬA họ tên / số điện thoại (kể cả sửa 1 phần), NGAY LẬP TỨC gọi tool `propose_identity` với phần bạn nghe được (cho phép thiếu trường).
        Sau đó hỏi xác nhận rõ ràng. Nếu bệnh nhân xác nhận thông tin đúng, gọi `confirm_identity` (confirm=True). Nếu bệnh nhân SỬA lại sau khi đã xác nhận trước đó, tiếp tục gọi `confirm_identity` với giá trị mới: hành vi này sẽ cập nhật danh tính và huỷ booking cũ để đặt lại.
        Chỉ được gọi `schedule_appointment` SAU khi `confirm_identity` trả về status=confirmed (hoặc reconfirmed) và chưa có booking hợp lệ tương ứng với danh tính hiện tại.
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
    """Đơn giản hoá: bỏ toàn bộ cơ chế personal memory injection."""
    def __init__(self, rag: MedicalRAG, buf: SessionBuf):
        super().__init__(instructions=SYSTEM_PROMPT)
        self.rag = rag
        self.buf = buf

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message):
        # Ghi lại user để summarize (giữ logic nhẹ)
        user_text = (getattr(new_message, "text_content", "") or "").strip()
        if not user_text:
            collected = []
            for m in getattr(turn_ctx, "user_messages", []) or []:
                t = (getattr(m, "text_content", "") or "").strip()
                if t:
                    collected.append(t)
            user_text = "\n".join(collected).strip()
        if user_text and (not self.buf.lines or not self.buf.lines[-1].endswith(user_text)):
            self.buf.add("user", user_text)

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
    identity_state = {
        "identity_confirmed": False,
        "patient_name": None,
        "phone": None,  # unified key
        "draft_name": None,
        "draft_phone": None,
        "draft_conf": 0.0,
    }

    # Heuristic patterns removed (identity extraction now fully via tools)

    async def _publish_data(obj: dict):
        try:
            payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            with contextlib.suppress(Exception):
                await ctx.room.local_participant.publish_data(payload)
        except Exception:
            log.exception("publish data failed type=%s", obj.get("type"))


    # Removed _inject_personal_context: personalization disabled

    async def start_new_session():
        nonlocal session, latest_booking, allow_finalize, closing
        if session is not None:
            with contextlib.suppress(Exception):
                await session.aclose()
        talker = Talker(rag=rag, buf=state)
        session = AgentSession(llm=llm)
        room_io = RoomInputOptions(noise_cancellation=noise_cancellation.BVC())

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
                # (Heuristic identity extraction removed)

        @session.on("conversation_item_updated")
        def on_item_updated(ev):
            role = (ev.item.role or "unknown")
            text = (getattr(ev.item, "text_content", "") or "").strip()
            if text:
                _log_evt("EVT conversation_item_updated", role, text)

        # ---------- Build all tools externally (identity + booking + finalize) ----------
        shared = {
            "latest_booking": latest_booking,
            "allow_finalize": allow_finalize,
            "closing": closing,
            "session": session,
            "ctx": ctx,
            "rag": rag,
            "talker": talker,
            "extract_facts_and_summary": extract_facts_and_summary,
            # marker flags
            "personal_context_injected": False,
        }

        tools = build_all_tools(
            lambda obj: asyncio.create_task(_publish_data(obj)),
            identity_state,
            shared,
            state=state,
            book_appointment=book_appointment,
            get_or_create_customer=get_or_create_customer,
            save_visit=save_visit,
            summarize_visit_json=summarize_visit_json,
            clinic_defaults=clinic_defaults,
        )
        await talker.update_tools(tools)

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

        # Lắng nghe data từ web (identity_confirmed_ui / identity_corrected)
        @ctx.room.on("data_received")
        def _on_data(pkt):
            try:
                raw = pkt.data
                msg = json.loads(raw.decode("utf-8"))
            except Exception:
                return
            t = msg.get("type")
            if t == "identity_confirmed_ui":
                # Deprecated: manual confirm button removed
                pass
            elif t == "identity_corrected":
                pn = (msg.get("patient_name") or "").strip()
                ph_raw = (msg.get("phone") or "").strip()
                ph_digits = re.sub(r"\D", "", ph_raw)
                if pn:
                    identity_state["patient_name"] = pn
                if len(ph_digits) == 10 and ph_digits.startswith("0"):
                    identity_state["phone"] = ph_digits
                if identity_state.get("patient_name") and identity_state.get("phone"):
                    identity_state["identity_confirmed"] = True
                    asyncio.create_task(_publish_data({
                        "type": "identity_confirmed",
                        "patient_name": identity_state.get("patient_name"),
                        "phone": identity_state.get("phone"),
                        "confidence": 0.9,
                    }))
            elif t == "identity_confirmed":
                # No-op: personalization injected via confirm_identity callback
                pass
        # Có thể mở rộng các type khác

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