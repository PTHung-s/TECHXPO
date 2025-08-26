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

from storage import init_db, get_or_create_customer, save_visit
from function_calling_def import build_all_tools
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
    1) Hỏi và ghi nhận: họ tên, số điện thoại. Sau khi nhận được thông tin thì phải hỏi confirm cho đến khi nào họ xác nhận đúng, nếu sai thì gọi lại function và truyền tham số để sửa.
    2) Khai thác TRIỆU CHỨNG (tên, mức độ) thật kĩ và nhiều nhất có thể qua trò chuyện gần gũi; khi nghi ngờ có triệu chứng khác thì chủ động hỏi thêm.
    3) Khi đã đủ dữ kiện để ĐẶT LỊCH, hãy GỌI TOOL `schedule_appointment` với các tham số bạn đã nắm.
    4) Hỏi lại xem Booking có cần sự thay đổi gì không.
    5) KHI ĐÃ ĐỦ dữ kiện (đủ danh tính + triệu chứng + booking đã xác nhận), THỰC HIỆN THỨ TỰ SAU: (a) Nói một LỜI CHÀO KẾT THÚC NGẮN GỌN bằng tiếng Việt (không hỏi thêm, không giới thiệu lại, không yêu cầu phản hồi), sau đó (b) NGAY LẬP TỨC GỌI TOOL `finalize_visit` để tổng kết và đóng phiên. KHÔNG nói thêm gì sau khi đã gọi tool.

    QUY TẮC:
    - Luôn tuân thủ quy chuẩn y tế nội bộ (nếu có) được cung cấp trong hội thoại.
    - Tránh độc thoại dài; luôn hỏi-đáp theo lượt.
    - Nhắc rõ rằng đây chỉ là hỗ trợ sơ bộ, không thay thế chẩn đoán y khoa chính thức.
    - QUY TẮC DANH TÍNH (TOOLS): Ngay khi bạn NGHĨ rằng đã nghe được một phần hoặc toàn bộ họ tên hoặc số điện thoại, hãy GỌI tool `propose_identity` (kể cả thiếu 1 trường) với confidence 0.0–1.0 và lý do ngắn (reason). Khi bệnh nhân xác nhận hoặc sửa, gọi `confirm_identity` (confirm=True nếu đúng, hoặc truyền bản sửa rồi confirm ở lượt kế). CHỈ ĐƯỢC gọi `schedule_appointment` sau khi `confirm_identity` trả về status=confirmed.
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
    identity_state = {
        "identity_confirmed": False,
        "patient_name": None,
        "patient_phone": None,
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
                ph = (msg.get("phone") or "").strip()
                if pn:
                    identity_state["patient_name"] = pn
                if ph and re.fullmatch(r"(\+?84|0)(3|5|7|8|9)\d{8}", ph):
                    identity_state["patient_phone"] = ph
                if identity_state.get("patient_name") and identity_state.get("patient_phone"):
                    identity_state["identity_confirmed"] = True
                    asyncio.create_task(_publish_data({
                        "type": "identity_confirmed",
                        "patient_name": identity_state.get("patient_name"),
                        "phone": identity_state.get("patient_phone"),
                        "confidence": 0.9,
                    }))
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