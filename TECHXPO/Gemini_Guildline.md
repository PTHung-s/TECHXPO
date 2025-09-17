Dưới đây là “bộ tối thiểu chạy được” để: host room trên LiveKit và dùng Gemini Live (chỉ model gemini-live-2.5-flash-preview) lo STT + VAD/turn + barge-in + voice. Mình giữ ngắn gọn nhưng không thiếu phần bắt buộc.

2) Agent (Python) — dùng gemini-live-2.5-flash-preview

Mặc định đã tự VAD/turn và cho barge-in. Bạn chỉ cần cấu hình model + voice + instructions.

# agent.py
import os
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, cli
from livekit.agents.worker import WorkerOptions
from livekit.plugins import google
from google.genai import types as gtypes

load_dotenv(".env.local")

class VoiceAssistant(Agent):
    def __init__(self):
        super().__init__(instructions="Bạn là trợ lý giọng nói thân thiện, ưu tiên tiếng Việt, trả lời ngắn gọn.")

async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()  # join room được dispatcher giao. :contentReference[oaicite:2]{index=2}

    # Gemini Live: tự phát hiện hoạt động + cho phép barge-in (mặc định)
    realtime_cfg = gtypes.RealtimeInputConfig(
        automatic_activity_detection=gtypes.AutomaticActivityDetection(),  # enabled by default
        activity_handling=gtypes.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
    )

    llm = google.beta.realtime.RealtimeModel(
        model="gemini-live-2.5-flash-preview",
        voice="Puck",  # chọn 1 giọng có sẵn
        instructions="Bạn là trợ lý giọng nói thân thiện.",
        realtime_input_config=realtime_cfg,
    )

    session = AgentSession(llm=llm)
    agent = VoiceAssistant()
    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(instructions="Xin chào! Mình có thể giúp gì cho bạn ngay bây giờ?")

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


RealtimeModel từ plugin Google ánh xạ trực tiếp sang Gemini Live API; automatic_activity_detection + activity_handling=START_OF_ACTIVITY_INTERRUPTS đúng với Live API (barge-in). 
LiveKit Docs
+1

Agents framework cho phép agent tham gia room realtime như 1 participant. 
LiveKit Docs

Nếu muốn không cho cắt lời, đổi activity_handling → gtypes.ActivityHandling.NO_INTERRUPTION. (Live API hỗ trợ tham số này). 
Google AI for Developers

3) Backend cấp token LiveKit (FastAPI, tối giản)
# server.py
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from livekit import api as lkapi

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/token")
def get_token(room: str, identity: str, name: str | None = None):
    token = (
        lkapi.AccessToken()  # đọc LIVEKIT_API_KEY/SECRET từ env
        .with_identity(identity)
        .with_name(name or identity)
        .with_grants(lkapi.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )
    return {"url": os.environ["LIVEKIT_URL"], "token": token}


LiveKit bắt buộc frontend dùng JWT token do backend tạo để join room. 
LiveKit Docs
+1

Python SDK AccessToken() tự lấy API key/secret từ env. 
LiveKit Docs
+1

Chạy:

uvicorn server:app --port 5050 --reload

4) Web client (JS, đủ dùng)
<!doctype html>
<html>
  <body>
    <button id="join">Join</button>
    <script type="module">
      import { Room } from 'https://cdn.skypack.dev/livekit-client'; // demo nhanh

      document.getElementById('join').onclick = async () => {
        const roomName = 'demo';
        const identity = 'user-' + Math.floor(Math.random() * 10000);

        const r = await fetch(`http://localhost:5050/token?room=${roomName}&identity=${identity}`);
        const { url, token } = await r.json();

        const room = new Room();
        await room.connect(url, token);                 // kết nối LiveKit
        await room.localParticipant.setMicrophoneEnabled(true); // bật mic

        console.log('connected to', room.name);
      };
    </script>
  </body>
</html>


Cách kết nối JS client và publish mic theo quickstart của LiveKit. 
LiveKit Docs
+1

5) Chạy toàn hệ

Backend token

uvicorn server:app --port 5050 --reload


Agent worker

python agent.py


Client mở file HTML, bấm Join → agent sẽ vào room qua Agents/dispatcher và hội thoại voice realtime do Gemini Live xử lý (VAD/turn/barge-in/voice). 
LiveKit Docs
+1

Ghi chú ngắn

Live API là stateful WebSocket; cấu hình (model, instructions, tools…) gửi ở đầu phiên. 
Google Cloud

Có thể tinh chỉnh độ nhạy bắt đầu/kết thúc lời nói trong automatic_activity_detection nếu cần.