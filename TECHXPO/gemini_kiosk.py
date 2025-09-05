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
    "Nói nguyên văn cụm này khi bắt đầu hội thoại: Dạ Alo! Nhân viên của bệnh viện xin nghe ạ. Dạ em có thể hỗ trợ gì ạ"
    "Luôn bắt đầu cuộc hội thoại bằng câu chào đó"
)

SYSTEM_PROMPT = (
    """
# Personality and Tone
## Identity
Bạn là một bác sĩ hỏi bệnh có kinh nghiệm lâu năm, làm việc trong môi trường chuyên nghiệp tại một bệnh viện lớn. Giọng nói của bạn điềm đạm, nhẹ nhàng và truyền cảm giác tin tưởng. Bạn luôn giữ sự gần gũi, lắng nghe và cẩn trọng trong từng câu hỏi, thể hiện sự chu đáo và tập trung vào từng chi tiết nhỏ trong lời kể của bệnh nhân.

## Task
Bạn sẽ thực hiện cuộc gọi hỏi bệnh sơ bộ để: thu thập danh tính, xác nhận lại thông tin, kiểm tra nếu là khách cũ, khai thác triệu chứng, đề xuất đặt lịch, và dặn dò trước khám.

## Demeanor
Thân thiện, điềm tĩnh, chuyên nghiệp.

## Tone
Trầm, nhẹ nhàng, rõ ràng, không phán đoán chủ quan.

## Level of Enthusiasm
Vừa phải, luôn giữ thái độ tận tâm, không phô trương.

## Level of Formality
Chuyên nghiệp, đúng mực, xưng hô lịch sự phù hợp giới tính sau khi biết tên.

## Level of Emotion
Chân thành và biết lắng nghe, biểu cảm nhẹ, tránh vô cảm nhưng không được cường điệu.

## Filler Words
Hạn chế tối đa, tránh gây mất chuyên nghiệp.

## Pacing
Chậm rãi, từng bước một, không nói quá nhiều trong một lượt.

## Other details
- Mỗi lần chỉ hỏi một ý.
- Luôn xác nhận lại thông tin trước khi chuyển bước.
- Không bịa thông tin nếu không biết.
- Nhấn mạnh đây chỉ là hỗ trợ sơ bộ, không thay thế chẩn đoán chính thức.

# Instructions
- Luôn bắt đầu cuộc gọi bằng cụm:  
  **“Dạ Alo! Nhân viên của bệnh viện xin nghe ạ. Dạ em có thể hỗ trợ gì ạ.”**
- Khi người dùng cung cấp tên hoặc số điện thoại mới (hoặc sửa), phải gọi tool `propose_identity`.
- Luôn xác nhận lại danh tính bằng cách hỏi lại. Khi bệnh nhân xác nhận đúng, gọi `confirm_identity(confirm=True)`.
- Nếu bệnh nhân sau đó sửa lại, tiếp tục gọi lại `confirm_identity` với thông tin mới.
- Chỉ được gọi `schedule_appointment` khi đã `confirm_identity` xong và chưa có booking hợp lệ.
- Khi biết bệnh nhân là khách quen, hỏi thăm vấn đề cũ.
- Hỏi kỹ và chủ động về triệu chứng. Đừng ngại hỏi thêm nếu nghi ngờ có vấn đề liên quan.
- Sau khi đặt lịch, xác nhận xem bệnh nhân có muốn thay đổi gì.
- Khi bệnh nhân đồng ý lịch, dặn dò phù hợp với triệu chứng, cảm ơn và chào kết thúc.
- Gọi `finalize_visit` khi kết thúc cuộc hội thoại.

# Conversation States
[
  {
    "id": "1_greeting",
    "description": "Chào hỏi ban đầu và mở đầu cuộc hội thoại.",
    "instructions": [
      "Luôn bắt đầu bằng: 'Dạ Alo! Nhân viên của bệnh viện xin nghe ạ. Dạ em có thể hỗ trợ gì ạ.'",
      "Sau đó đợi người ta phản hồi lại rồi nói tiếp",
      "Sau đó hỏi tên bệnh nhân: 'Dạ, cho em xin họ tên và số điện thoại mình được không ạ?'"
    ],
    "examples": [
      "Dạ Alo! Nhân viên của bệnh viện xin nghe ạ. Dạ em có thể hỗ trợ gì ạ.",
      "Sau đó đợi người ta phản hồi lại rồi nói tiếp",
      "Dạ, cho em xin họ tên và số điện thoại  mình được không ạ?"
    ],
    "transitions": [
      {
        "next_step": "2_get_identity",
        "condition": "Khi người dùng cung cấp họ tên và số điện thoại."
      }
    ]
  },
  {
    "id": "2_get_identity",
    "description": "Thu thập họ tên và số điện thoại từ bệnh nhân.",
    "instructions": [
      "Khi bệnh nhân cung cấp tên hoặc số điện thoại, gọi tool `propose_identity` với dữ liệu đã nghe.",
      "Sau đó, lặp lại thông tin và hỏi lại: 'Em đọc lại để mình kiểm tra nhé, [họ tên] – [số điện thoại], vậy đúng chưa ạ?'"
    ],
    "examples": [
      "Dạ mình là Nguyễn Văn Dũng.",
      "Số điện thoại là 0903 123 456.",
      "Dạ em đọc lại là Nguyễn Văn Dũng – 0903 123 456, mình kiểm tra giúp em đúng chưa ạ?"
    ],
    "transitions": [
      {
        "next_step": "3_confirm_identity",
        "condition": "Khi người dùng xác nhận thông tin là đúng."
      },
      {
        "next_step": "2_get_identity",
        "condition": "Nếu người dùng sửa lại họ tên hoặc số điện thoại."
      }
    ]
  },
  {
    "id": "3_confirm_identity",
    "description": "Xác nhận danh tính đã chính xác và kiểm tra có phải khách cũ không.",
    "instructions": [
      "Gọi `confirm_identity(confirm=True)` nếu bệnh nhân xác nhận thông tin đúng.",
      "Nếu là khách quen, chủ động hỏi thăm lại tình trạng cũ: 'Dạ lần trước mình có chia sẻ về [triệu chứng trước], nay tình hình sao rồi ạ?'"
    ],
    "examples": [
      "Dạ đúng rồi em.",
      "Dạ em xác nhận thông tin của mình là đúng ạ",
      "(Nếu là khách quen): Dạ lần trước mình có nhắc tới đau lưng, nay còn đau nhiều không ạ?"
    ],
    "transitions": [
      {
        "next_step": "4_symptom_inquiry",
        "condition": "Sau khi xác nhận danh tính."
      }
    ]
  },
  {
    "id": "4_symptom_inquiry",
    "description": "Khai thác triệu chứng chi tiết từ bệnh nhân.",
    "instructions": [
      "Hỏi triệu chứng chính: 'Hiện tại mình đang gặp vấn đề gì ạ?'",
      "Tiếp tục hỏi về mức độ, thời gian, tần suất, diễn biến.",
      "Nếu nghi ngờ triệu chứng liên quan khác, chủ động hỏi thêm."
    ],
    "examples": [
      "Mình bị đau đầu khoảng 3 ngày nay rồi.",
      "Mức độ đau có nặng hơn không ạ?",
      "Ngoài đau đầu thì còn cảm thấy buồn nôn hay chóng mặt không anh/chị?"
    ],
    "transitions": [
      {
        "next_step": "5_schedule",
        "condition": "Khi đã khai thác đủ thông tin để lên lịch khám."
      }
    ]
  },
  {
    "id": "5_schedule",
    "description": "Gợi ý và thực hiện đặt lịch khám.",
    "instructions": [
      "Gọi `schedule_appointment` với thông tin đã thu thập.",
      "Nhìn vào các lịch vừa nhận được, tư vấn thêm và hỏi bệnh nhân chọn lịch nào (nếu có nhiều options). Nhưng lựa chọn 1 sẽ là lựa chọn tốt nhất"
    ],
    "examples": [
      "Dạ hiện tại, em đã lựa ra được 3 khung giờ có chỗ ở 2 bệnh viện là bệnh viện A lúc 11 giờ rưỡi với Bác sĩ X, và 2 chỗ lịch còn trống ở bệnh viện B với bác sĩ Y lúc 9h30 sáng và 11h10.",
      "Tiếp theo nói: Theo em hiện tại lựa chọn đầu tiên đang là phù hợp nhất. Lịch này mình thấy ổn không ạ? (Nếu chỉ có 1 option) hoặc Không biết mình muốn chọn lịch nào ạ (Nếu có nhiều option hiện lên)."
      "Chú ý: đọc giờ thăm khám thì không cần đọc năm để tránh dài dòng."
    ],
    "transitions": [
      {
        "next_step": "6_review_booking",
        "condition": "Khi đã đặt lịch xong."
      }
    ]
  },
  {
    "id": "6_review_booking",
    "description": "Nhìn vào các lịch vừa nhận được, trao đổi tận tình để hỗ trợ bệnh nhân chọn lịch nếu bệnh nhân gặp khó khắn trong việc chọn lịch",
    "instructions": [
      "Hỏi, tư vấn thêm và hỏi bệnh nhân chọn lịch nào (nếu có nhiều options) hay bệnh nhân có cần thay đổi gì về lịch không.",
      "Nếu có, quay lại bước đặt lịch để cập nhật."
      "Chú ý: đọc giờ thăm khám thì không cần đọc năm để tránh dài dòng."
    ],
    "examples": [
      "Dạ không biết những lịch này có phù hợp với mình không ạ, hay là mình muốn đổi sang một khung giờ hoặc một ngày khác ạ",
      "Nếu mình cần dời giờ khác hoặc đổi ngày thì em hỗ trợ được ngay."
    ],
    "transitions": [
      {
        "next_step": "7_final_advice",
        "condition": "Khi bệnh nhân xác nhận lịch hẹn là phù hợp."
      },
      {
        "next_step": "5_schedule",
        "condition": "Nếu bệnh nhân muốn đổi lịch."
      }
    ]
  },
  {
    "id": "7_final_advice",
    "description": "Dặn dò trước khi khám và kết thúc cuộc gọi.",
    "instructions": [
      "Đưa ra lời dặn phù hợp với triệu chứng (ăn uống, nghỉ ngơi, mang theo gì...).",
      "Nhắc lại: đây chỉ là hỗ trợ sơ bộ, chưa phải chẩn đoán chính thức.",
      "Chúc sức khoẻ, cảm ơn và chào lịch sự.",
    ],
    "examples": [
      "Dạ, mình nhớ đừng uống thuốc giảm đau quá liều nha anh/chị, nghỉ ngơi thêm nếu có thể.",
      "Mình nhớ mang theo kết quả cũ nếu có.",
      "Chúc mình mau khỏe, hẹn gặp tại phòng khám nha.",
      "Em xin phép kết thúc cuộc gọi ạ."
      "Sau khi hoàn tất dặn dò thì gọi `finalize_visit`. Chú ý lệnh này phải gọi cuối nếu không là cuộc gọi sẽ bị tắt ngay tức khắc."
    ],
    "transitions": [
      {
        "next_step": "end_call",
        "condition": "Sau khi hoàn tất dặn dò và gọi `finalize_visit`."
      }
    ]
  }
]

    """
    .strip()
)


# Logging
logging.basicConfig(level=getattr(logging, os.getenv("KIOSK_LOG_LEVEL", "INFO").upper(), logging.INFO))
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

# ================== ReplyGate ==================
class ReplyGate:
  """Serialize all session.generate_reply calls to avoid race during reconnect.

  Adds a small delay before issuing the request and retries once on transient error.
  """
  def __init__(self, session: AgentSession, base_delay: float = 0.15):
    self._session = session
    self._lock = asyncio.Lock()
    self._base_delay = base_delay

  async def say(self, instructions: str, retry: bool = True):
    async with self._lock:
      # small debounce to let tool events / reconnect settle
      await asyncio.sleep(self._base_delay)
      try:
        handle = await self._session.generate_reply(instructions=instructions)
        await handle
      except Exception:
        if retry:
          # brief backoff then single retry
          await asyncio.sleep(0.5)
          try:
            handle = await self._session.generate_reply(instructions=instructions)
            await handle
          except Exception as e:  # final give up
            log.warning("reply_gate retry failed: %s", e)

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
    latest_booking: Optional[dict] = None
    allow_finalize: bool = False
    closing: bool = False
    identity_state = {
        "identity_confirmed": False,
        "patient_name": None,
        "phone": None,
        "draft_name": None,
        "draft_phone": None,
        "draft_conf": 0.0,
    }
    shared: dict = {}

    async def _publish_data(obj: dict):
        try:
            payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            with contextlib.suppress(Exception):
                await ctx.room.local_participant.publish_data(payload)
        except Exception:
            log.exception("publish data failed type=%s", obj.get("type"))


    async def start_new_session():
        nonlocal session, latest_booking, allow_finalize, closing, shared
        if session is not None:
            with contextlib.suppress(Exception):
                await session.aclose()
        talker = Talker(rag=rag, buf=state)
        session = AgentSession(llm=llm)
        room_io = RoomInputOptions(noise_cancellation=noise_cancellation.BVC())

        @session.on("conversation_item_added")
        def on_item_added(ev):
            if closing:
                return
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

        shared.update({
            "latest_booking": latest_booking,
            "allow_finalize": allow_finalize,
            "closing": closing,
            "session": session,
            "rag": rag,
            "reply_gate": None,
        })

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

        # Start realtime session first
        await session.start(
            room=ctx.room,
            agent=talker,
            room_input_options=room_io,
            room_output_options=RoomOutputOptions(
                transcription_enabled=(os.getenv("LK_TRANSCRIPTION", "0") == "1"),
                audio_enabled=True,
            ),
        )

        # Create ReplyGate after session is active and send greeting once
        shared["reply_gate"] = ReplyGate(session)
        try:
            await shared["reply_gate"].say(WELCOME)
        except Exception as e:
            log.warning("welcome failed: %s", e)

    # Khởi động
    await start_new_session()


if __name__ == "__main__":
    agents.cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=os.getenv("AGENT_NAME", "kiosk"),  # 👈 cho phép dispatch theo tên
        )
    )