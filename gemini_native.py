# -*- coding: utf-8 -*-
"""
LiveKit Agents + Gemini 2.5 Native Audio (Affective + Proactive),
khử echo bằng BVC và hạn chế tự nghe chính mình (SUBSCRIBE_NONE).

Chạy:
  python gemini_native.py console   # nói chuyện trong terminal (mic/speaker máy bạn)
  python gemini_native.py dev       # dùng Agents Playground (web UI của LiveKit)
"""

import os
from dotenv import load_dotenv

load_dotenv(".env.local") or load_dotenv()

from livekit import agents
from livekit.agents import (
    WorkerOptions,
    Agent,
    AgentSession,
    JobContext,
    AutoSubscribe,
    RoomInputOptions,
)
from livekit.plugins.google.beta import realtime
from livekit.plugins import noise_cancellation  # gói: livekit-plugins-noise-cancellation


# Trợ lý đơn giản (system prompt)
class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "Bạn là trợ lý thân thiện, nói chuyện tự nhiên và lịch sự. "
                "Trả lời gãy gọn, không tự nói quá dài. Nếu người dùng im lặng, nên chủ động hỏi tiếp."
            )
        )


async def entrypoint(ctx: JobContext):
    # 1) Kết nối và KHÔNG auto-subscribe tất cả track:
    #    Tự quản để không bao giờ subscribe track phát từ agent (tránh nghe chính mình).
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY) 

    # 2) Lấy API key cho Gemini
    api_key = os.getenv("GOOGLE_API_KEY2") or os.getenv("GEMINI_API_KEY2")
    if not api_key:
        raise RuntimeError("Thiếu GOOGLE_API_KEY/GEMINI_API_KEY trong env")

    # 3) Tạo phiên AgentSession với Gemini Native Audio (Affective/Proactive là một phần Live API v1alpha)
    #    Lưu ý: RealtimeModel (beta) mặc định đã bật transcript 2 chiều (theo doc),
    #    nếu bản của bạn không có cờ enable_* thì cứ bỏ qua – mặc định True.
    llm = realtime.RealtimeModel(
        model="gemini-2.5-flash-preview-native-audio-dialog",
        voice="Puck",
        api_key=api_key,  # truyền thẳng key thay vì dựa vào env
        # Nếu version plugin hỗ trợ, có thể thêm:
        # enable_user_audio_transcription=True,
        # enable_agent_audio_transcription=True,
    )

    session = AgentSession(llm=llm)

    # 4) Bật enhanced noise cancellation (BVC) phía agent để giảm feedback/echo
    room_io_opts = RoomInputOptions(
        noise_cancellation=noise_cancellation.BVC()
    )

    # 5) Start session: native audio chỉ dùng 1 modality (AUDIO) và Live API sẽ tự VAD/turn
    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_input_options=room_io_opts,
    )

    # (Tuỳ chọn) Chào trước khi người dùng nói
    await session.generate_reply(instructions="Chào bạn! Mình có thể giúp gì ngay bây giờ?")


if __name__ == "__main__":
    agents.cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
