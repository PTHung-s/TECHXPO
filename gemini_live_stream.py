# -*- coding: utf-8 -*-
"""
Realtime voice chat với Gemini 2.5 native audio + chèn ảnh giữa hội thoại.
- Mic 16 kHz PCM 16-bit mono -> model -> Audio 24 kHz PCM 16-bit mono.
- Bật phụ đề (transcription) cho tiếng nói của model và (tùy chọn) đầu vào từ mic.
- Chặn mic khi model đang nói để tránh "tự ngắt" và hiện tượng nói giữa chừng bị đơ.
- Buffer phát audio qua Queue để playback mượt hơn, tránh nghẽn event loop.
- Lệnh runtime:
    :say <text>             -> gửi tin nhắn văn bản cho model
    :img <path> [prompt]    -> chèn ảnh giữa hội thoại (path bắt buộc)
    :quit                   -> thoát

Cài đặt:
  pip install --upgrade google-genai python-dotenv pyaudio soundfile
Tạo .env cạnh file này:
  GOOGLE_API_KEY=YOUR_KEY

Tham chiếu chính:
- Live API capabilities (audio formats, transcription, single-modality, native audio model)
- Speech generation voices (tên voice như 'Puck', 'Kore'...)
- types.Part.from_bytes để gửi ảnh
"""

import asyncio
import os
import sys
import signal
import mimetypes
from pathlib import Path
from dataclasses import dataclass

import pyaudio
from dotenv import load_dotenv

from google import genai
from google.genai import types

# ========== Cấu hình cơ bản ==========
MODEL = "gemini-2.5-flash-preview-native-audio-dialog"  # Native audio dialog
VOICE_NAME = "Puck"  # Có thể đổi: 'Kore', 'Charon', 'Zephyr', ... (xem doc TTS)
SYSTEM_PROMPT = "Bạn là trợ lý thân thiện. Hãy trả lời ngắn gọn, tự nhiên bằng tiếng Việt."

# Audio I/O
IN_RATE = 16_000     # mic -> 16 kHz PCM 16-bit (khuyến nghị Live API)
OUT_RATE = 24_000    # model -> 24 kHz PCM 16-bit (chuẩn Live API)
CHUNK_MS = 20        # khung 20ms cho latency thấp + phát mượt
IN_CHUNK = int(IN_RATE * CHUNK_MS / 1000)
OUT_CHUNK = int(OUT_RATE * CHUNK_MS / 1000)

# ========== Utilities ==========
def log(*args, **kwargs):
    print(*args, **kwargs, flush=True)

@dataclass
class GracefulExit:
    event: asyncio.Event

# ========== Audio helpers ==========
class AudioIO:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        # Mic
        self.in_stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=IN_RATE,
            input=True,
            frames_per_buffer=IN_CHUNK,
        )
        # Loa
        self.out_stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=OUT_RATE,
            output=True,
            frames_per_buffer=OUT_CHUNK,
        )

    def read_mic(self) -> bytes:
        # exception_on_overflow=False để tránh crash khi CPU bận
        return self.in_stream.read(IN_CHUNK, exception_on_overflow=False)

    def play(self, pcm: bytes):
        if pcm:
            self.out_stream.write(pcm)

    def close(self):
        for s in (getattr(self, "in_stream", None), getattr(self, "out_stream", None)):
            try:
                s.stop_stream(); s.close()
            except Exception:
                pass
        try:
            self.pa.terminate()
        except Exception:
            pass

# ========== NVIDIA Audio2Face-2D hook (stub) ==========
async def maybe_send_to_a2f2d(portrait_path: str, wav_path: str, target="127.0.0.1:8001"):
    """
    Placeholder để tích hợp A2F-2D NIM (gRPC) sau này.
    Khi đã dựng NIM, gửi (portrait_image, audio.wav) -> nhận video lip-sync.
    """
    pass

# ========== Ảnh -> Part ==========
def build_image_part(image_path: str) -> types.Part:
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    data = Path(image_path).read_bytes()
    return types.Part.from_bytes(data=data, mime_type=mime)

# ========== Console input async ==========
async def console_reader():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, sys.stdin.readline)

# ========== Main ==========
async def main():
    # Load .env (nếu .env không cùng thư mục, truyền đường dẫn tuyệt đối)
    load_dotenv()

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Thiếu API key. Hãy đặt GOOGLE_API_KEY trong .env")

    # Tạo client: để rỗng cũng được vì SDK tự đọc env, nhưng truyền thẳng cho chắc.
    client = genai.Client(api_key=api_key)

    # Config Live API:
    # - Chỉ 1 modality: AUDIO
    # - Bật transcription cho output (và input nếu muốn debug/VAD)
    # - Chọn voice native audio
    live_config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": SYSTEM_PROMPT,
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": VOICE_NAME}
            }
        },
        # Bật phụ đề cho tiếng nói model (docs: dùng trường output_audio_transcription)
        "output_audio_transcription": {},
        # (tùy chọn) bật transcript cho mic để thấy model hiểu gì từ bạn
        "input_audio_transcription": {},
        # Có thể tinh chỉnh media_resolution nếu muốn
        # "media_resolution": types.MediaResolution.MEDIA_RESOLUTION_LOW,
        #
        # Nếu muốn tự VAD thủ công:
        # "realtime_input_config": {
        #     "automatic_activity_detection": {"disabled": True}
        # }
    }

    audio = AudioIO()
    shutting_down = asyncio.Event()
    ge = GracefulExit(event=shutting_down)

    def _handle_sigint(sig, frame):
        ge.event.set()

    signal.signal(signal.SIGINT, _handle_sigint)

    log(f"[✔] Kết nối model: {MODEL}")
    log("Lệnh: ':say ...', ':img <path> [prompt]', ':quit'")
    log("Bạn có thể nói trực tiếp qua micro. Model sẽ trả lời bằng tiếng nói.\n")

    # Trạng thái điều phối: dừng mic khi model đang nói + phát audio qua queue
    model_speaking = asyncio.Event()
    play_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=128)

    async with client.aio.live.connect(model=MODEL, config=live_config) as session:

        async def mic_loop():
            # Gửi chunk mic đều đặn, NHƯNG khi model đang nói thì tạm dừng
            while not ge.event.is_set():
                if model_speaking.is_set():
                    await asyncio.sleep(CHUNK_MS / 1000)
                    continue
                try:
                    pcm = audio.read_mic()
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=pcm,
                            mime_type=f"audio/pcm;rate={IN_RATE}",
                        )
                    )
                except Exception as e:
                    log(f"[MicLoop] lỗi: {e}")
                    break

        async def playback_loop():
            # Phát âm ra loa từ queue để tránh nghẽn recv_loop
            while not ge.event.is_set():
                try:
                    pcm = await play_q.get()
                    audio.play(pcm)
                    play_q.task_done()
                except Exception as e:
                    log(f"[Playback] lỗi: {e}")

        async def recv_loop():
            # Nhận audio + phụ đề. KHÔNG đọc message.text trong phiên AUDIO (tránh cảnh báo).
            async for message in session.receive():
                try:
                    # 1) Audio PCM 24kHz từ model -> buffer để phát mượt
                    if getattr(message, "data", None):
                        if not model_speaking.is_set():
                            model_speaking.set()
                        try:
                            play_q.put_nowait(message.data)
                        except asyncio.QueueFull:
                            # Tránh nghẽn: bỏ khung cũ nếu hàng đợi đầy
                            try:
                                _ = play_q.get_nowait()
                                play_q.task_done()
                            except Exception:
                                pass
                            play_q.put_nowait(message.data)

                    sc = getattr(message, "server_content", None)

                    # 2) Phụ đề output (model nói)
                    if sc and getattr(sc, "output_transcription", None):
                        t = sc.output_transcription.text
                        if t:
                            print(f"\n[Gemini] {t}", end="", flush=True)

                    # 3) (tuỳ chọn) Transcript INPUT (mic) nếu bật input_audio_transcription
                    if sc and getattr(sc, "input_transcription", None):
                        it = sc.input_transcription.text
                        if it:
                            print(f"\n[You@mic] {it}", end="", flush=True)

                    # 4) Nếu server báo bị chen (interrupted) -> coi như kết thúc lượt
                    if sc and getattr(sc, "interrupted", False):
                        model_speaking.clear()

                    # 5) Kết thúc lượt nói -> mở mic lại
                    if sc and getattr(sc, "turn_complete", False):
                        await asyncio.sleep(0.05)  # xả bớt queue phát cho gọn
                        model_speaking.clear()
                        print()
                except Exception as e:
                    log(f"[RecvLoop] lỗi: {e}")

        async def ui_loop():
            # Nhận lệnh từ bàn phím: :say, :img, :quit
            while not ge.event.is_set():
                line = (await console_reader()).strip()
                if not line:
                    continue
                head, *rest = line.split(maxsplit=1)
                head_l = head.lower()

                if head_l == ":quit":
                    ge.event.set()
                    break

                elif head_l == ":say" and rest:
                    txt = rest[0]
                    await session.send_client_content(
                        turns=types.Content(
                            role="user",
                            parts=[types.Part.from_text(txt)]
                        ),
                        turn_complete=True
                    )

                elif head_l == ":img" and rest:
                    # :img <path> [prompt]
                    args = rest[0].split(maxsplit=1)
                    path = args[0]
                    prompt = args[1] if len(args) == 2 else "Mô tả nội dung ảnh này."
                    try:
                        img_part = build_image_part(path)
                        await session.send_client_content(
                            turns=types.Content(
                                role="user",
                                parts=[types.Part.from_text(prompt), img_part]
                            ),
                            turn_complete=True
                        )
                        log(f"[+] Đã gửi ảnh: {path}")
                    except Exception as e:
                        log(f"[!] Gửi ảnh lỗi: {e}")

                else:
                    # Mặc định coi như text
                    await session.send_client_content(
                        turns=types.Content(
                            role="user",
                            parts=[types.Part.from_text(line)]
                        ),
                        turn_complete=True
                    )

        tasks = [
            asyncio.create_task(mic_loop(), name="mic_loop"),
            asyncio.create_task(recv_loop(), name="recv_loop"),
            asyncio.create_task(playback_loop(), name="playback_loop"),
            asyncio.create_task(ui_loop(), name="ui_loop"),
        ]

        await ge.event.wait()
        for t in tasks:
            t.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            pass

    audio.close()
    log("\n[Bye] Đã thoát.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
