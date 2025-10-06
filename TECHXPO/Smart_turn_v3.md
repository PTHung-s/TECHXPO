Có thể tích hợp thẳng — dưới đây là cách “cắm” Smart Turn v3 (ONNX, chạy CPU) vào đúng chỗ pipeline của bạn mà không phải thay toàn bộ logic Gemini Live. Mình đưa dạng patch: thêm một lớp phân tích, gắn vào audio của LiveKit, rồi phát sự kiện end_of_turn để bạn điều phối phản hồi.

1) Cài & chuẩn bị model
pip install onnxruntime
# onnxruntime-gpu cũng được nếu bạn muốn, nhưng CPU là đủ
# Tải smart-turn-v3.onnx về thư mục ./models/


Thêm biến môi trường:

SMART_TURN_ONNX_PATH=./models/smart-turn-v3.onnx
ST_WINDOW_SEC=8
ST_HOP_SEC=2
ST_THRESHOLD=0.60
ST_COOLDOWN_MS=400

2) Thêm lớp SmartTurnV3 (server-side)

Đặt ngay trên các import trong file của bạn (hoặc cuối phần “Helpers”)

# ===== SmartTurn v3 analyzer (CPU, ONNX) =====
import time
import numpy as np
try:
    import onnxruntime as ort
except Exception:
    ort = None

class SmartTurnV3:
    """
    - Nhận PCM16 mono @ 16kHz theo từng mẩu (chunk)
    - Giữ cửa sổ 8s trượt (config được)
    - Mỗi hop (2s) chạy inference 1 lần
    - Nếu prob > threshold + qua cooldown => callback("end_of_turn", prob)
    """
    def __init__(
        self,
        onnx_path: str,
        sample_rate: int = 16000,
        window_sec: float = 8.0,
        hop_sec: float = 2.0,
        threshold: float = 0.60,
        cooldown_ms: int = 400,
        on_end_of_turn=None,
    ):
        if ort is None:
            raise RuntimeError("onnxruntime not installed")
        self.sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self.sr = sample_rate
        self.win = int(window_sec * sample_rate)
        self.hop = int(hop_sec * sample_rate)
        self.thr = threshold
        self.cooldown_ms = cooldown_ms
        self.on_end = on_end_of_turn

        self.buf = np.zeros(0, dtype=np.float32)
        self._hop_accum = 0
        self._last_ms = 0.0

    @staticmethod
    def _pcm16_to_f32(pcm16: bytes) -> np.ndarray:
        x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        return x

    def push_pcm16(self, pcm16: bytes, sample_rate: int):
        """Đẩy thêm audio. Nếu sample_rate != 16k, bạn nên resample trước khi gọi."""
        if sample_rate != self.sr:
            # chốt: ở LiveKit bạn đặt opus 48k. Bạn có thể downsample ngoài hàm này để tiết kiệm CPU.
            raise ValueError(f"SmartTurn expects {self.sr} Hz, got {sample_rate}")
        x = self._pcm16_to_f32(pcm16)
        if x.size == 0:
            return

        # append & keep last window
        if self.buf.size:
            self.buf = np.concatenate([self.buf, x], axis=0)
        else:
            self.buf = x
        if self.buf.size > self.win:
            self.buf = self.buf[-self.win:]

        self._hop_accum += x.size
        if self.buf.size >= self.win and self._hop_accum >= self.hop:
            self._hop_accum = 0
            self._infer()

    def _infer(self):
        # input shape [1, T]
        inp = np.expand_dims(self.buf[-self.win:], 0).astype(np.float32)
        out = self.sess.run(None, {"waveform": inp})
        # lấy xác suất/score đầu ra (tuỳ model, thường là 1 logit/prob)
        prob = float(out[0].squeeze())
        now = time.time() * 1000.0
        if prob > self.thr and (now - self._last_ms) > self.cooldown_ms:
            self._last_ms = now
            if callable(self.on_end):
                self.on_end(prob)

3) Gắn vào audio từ LiveKit

Bạn đã await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY). Ta sẽ:

Bắt remote audio frames từ track đã subscribe.

Downsample (nếu cần) → đẩy vào SmartTurnV3.

Khi bật end_of_turn, ta kích Gemini phản hồi (không cần tắt auto VAD của Gemini; đây là hint/hỗ trợ). Cách an toàn: gửi một data event nội bộ và/hoặc gọi reply_gate.say("") (để commit turn) — Gemini Live sẽ dùng nội dung vừa nghe để trả lời.

Thêm util resample 48k→16k (nhẹ, linear):

def _resample_linear_f32(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    ratio = sr_in / sr_out
    out_len = int(x.size / ratio)
    if out_len <= 1:
        return np.zeros(0, dtype=np.float32)
    idx = np.arange(out_len) * ratio
    i0 = np.floor(idx).astype(np.int64)
    i1 = np.minimum(i0 + 1, x.size - 1)
    t = idx - i0
    return (x[i0] * (1.0 - t) + x[i1] * t).astype(np.float32)


Task lấy audio frames từ LiveKit:

Ở LiveKit Python, bạn có thể nhận audio frames từ remote track. Đoạn dưới dùng một API phổ biến: lấy RemoteAudioTrack và đọc frame PCM 16-bit @ 48k. Nếu SDK của bạn khác, bạn chỉ cần thay phần lấy PCM rồi giữ nguyên st.push_pcm16(...).

async def _attach_smart_turn(ctx: JobContext, on_end_cb):
    """
    - Đợi subscribe audio track của user
    - Đọc PCM 48k, downsample -> 16k, đẩy vào SmartTurnV3
    """
    onnx_path = os.getenv("SMART_TURN_ONNX_PATH", "./models/smart-turn-v3.onnx")
    st = SmartTurnV3(
        onnx_path=onnx_path,
        sample_rate=16000,
        window_sec=float(os.getenv("ST_WINDOW_SEC", "8")),
        hop_sec=float(os.getenv("ST_HOP_SEC", "2")),
        threshold=float(os.getenv("ST_THRESHOLD", "0.60")),
        cooldown_ms=int(os.getenv("ST_COOLDOWN_MS", "400")),
        on_end_of_turn=on_end_cb,
    )

    # Helper: khi có frame (PCM 48k float32/bytes) -> convert -> push
    async def _pump_frames(track):
        # tuỳ SDK: có thể có phương thức trả về bytes PCM16 hoặc float32
        async for frame in track.frames():
            # Giả định frame là bytes PCM s16le @ 48000; nếu là float32, chuyển về pcm16 trước.
            pcm16_48k: bytes = frame.data  # <- thay tuỳ SDK
            # sang float32, rồi downsample 48k -> 16k, rồi về PCM16 (vì SmartTurnV3 nhận pcm16)
            f32_48k = np.frombuffer(pcm16_48k, dtype=np.int16).astype(np.float32) / 32768.0
            f32_16k = _resample_linear_f32(f32_48k, 48000, 16000)
            pcm16_16k = (np.clip(f32_16k, -1.0, 1.0) * 32768.0).astype(np.int16).tobytes()
            st.push_pcm16(pcm16=pcm16_16k, sample_rate=16000)

    # Đợi đến khi có track audio của remote participant
    @ctx.room.on("track_subscribed")
    def _on_track_subscribed(track, publication, participant):
        from livekit import rtc
        if isinstance(track, rtc.RemoteAudioTrack):
            log.info("smart-turn: subscribed audio from %s", participant.identity)
            asyncio.create_task(_pump_frames(track))

4) Kích hoạt trong entrypoint()

Ngay sau await start_new_session(), gắn Smart Turn và định nghĩa hành vi khi end_of_turn:

    await start_new_session()

    # ===== Smart Turn: hành vi khi detect end_of_turn =====
    async def _on_end_of_turn(prob: float):
        log.info("smart-turn: END_OF_TURN prob=%.3f", prob)
        # 1) (khuyến nghị) phát sự kiện qua DataChannel để UI hiển thị trạng thái
        await _publish_data({"type": "end_of_turn", "prob": prob})

        # 2) "Nudge" Gemini Live phản hồi sớm:
        #    - Vì bạn đang dùng mặc định auto-turn của Gemini, cách an toàn là gửi
        #      1 instruction rỗng để commit/cue trả lời.
        #    - Nếu bạn muốn “tắt” auto-turn và điều khiển hoàn toàn bằng Smart Turn,
        #      thì cần đổi cấu hình RealtimeModel (không làm ở đây).
        try:
            if shared.get("reply_gate"):
                # câu ngắn gọn để commit; bạn có thể để "" hoặc prompt ngắn
                await shared["reply_gate"].say("", retry=False)
        except Exception as e:
            log.warning("smart-turn nudge failed: %s", e)

    # Gắn pipeline nghe audio và chạy Smart Turn
    await _attach_smart_turn(ctx, on_end_cb=lambda p: asyncio.create_task(_on_end_of_turn(p)))


Lưu ý: Nếu SDK LiveKit/Agents của bạn dùng API khác cho “đọc frame audio” (ví dụ track.create_audio_stream() hoặc AudioStream(track).frames()), bạn chỉ cần thay phần trong _pump_frames(track) để thu được pcm16@48k.

5) Cách dùng (luồng hoạt động)

User nói → LiveKit stream audio → _attach_smart_turn đọc frame, downsample → SmartTurnV3.

Khi xác suất kết thúc lượt > threshold và qua cooldown, callback _on_end_of_turn chạy.

Callback:

phát data event về UI (bạn có thể bật đèn “đang trả lời”),

nudge ReplyGate.say("") để Gemini Live “commit” và trả lời sớm hơn (vẫn giữ được auto VAD/turn mặc định của Gemini để an toàn).

Nếu bạn muốn Smart Turn điều khiển hoàn toàn (tắt auto-turn của Gemini), hãy:

Tắt các mặc định VAD/turn trong RealtimeModel (tuỳ option của plugin),

Chỉ khi _on_end_of_turn bắn sự kiện thì mới generate_reply.

6) Tinh chỉnh nhanh (tiếng Việt)

ST_THRESHOLD: 0.55–0.70 (giá trị thấp → nhạy hơn, dễ cắt sớm; cao → an toàn hơn).

ST_COOLDOWN_MS: 300–600 ms (chặn bắn liên tiếp).

ST_HOP_SEC: 2–3 s (CPU thấp thì tăng lên).

Có thể thêm rule “prob phải > threshold liên tục ≥ X ms” nếu gặp false-positive.

Bạn chưa cần gửi thêm file

Chỉ cần bạn lấy được PCM từ remote audio track để feed vào SmartTurnV3.push_pcm16(...) như patch ở trên là chạy được. Nếu trong SDK của bạn hàm đọc frame khác tên, cứ thay phần _pump_frames(track) theo API bạn đang có.

Nếu muốn, mình có thể viết lại _attach_smart_turn đúng theo phiên bản LiveKit Agents bạn đang dùng (chỉ cần bạn cho biết livekit-agents và livekit-rtc version).