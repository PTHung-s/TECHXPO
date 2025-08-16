import os
import base64
import asyncio
from typing import Optional
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from dotenv import load_dotenv
from google import genai
from google.genai import types
import httpx

load_dotenv()

app = FastAPI()

MODEL = "gemini-2.5-flash-preview-native-audio-dialog"
VOICE_NAME = "Puck"
IN_RATE = 16_000
OUT_RATE = 24_000

# Lazy clients so unit tests don't require keys
_genai_client: Optional[genai.Client] = None
_http_client: Optional[httpx.AsyncClient] = None


def get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY missing")
        _genai_client = genai.Client(api_key=api_key)
    return _genai_client


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient()
    return _http_client


@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket):
    await ws.accept()
    client = get_genai_client()
    session = await client.aio.live.connect(
        model=MODEL,
        config=types.LiveConnectConfig(
            voice=types.Voice(name=VOICE_NAME),
            output_audio_config=types.AudioConfig(mime_type="audio/pcm", sample_rate_hz=OUT_RATE),
            input_audio_config=types.AudioConfig(mime_type="audio/pcm", sample_rate_hz=IN_RATE),
            enable_output_transcription=True,
        ),
        system_instruction=SYSTEM_PROMPT,
    )
    send_task = asyncio.create_task(_ws_to_session(ws, session))
    recv_task = asyncio.create_task(_session_to_ws(ws, session))
    try:
        await asyncio.wait([send_task, recv_task], return_when=asyncio.FIRST_COMPLETED)
    finally:
        await session.close()
        await ws.close()


SYSTEM_PROMPT = "Bạn là trợ lý thân thiện. Hãy trả lời ngắn gọn, tự nhiên bằng tiếng Việt."


async def _ws_to_session(ws: WebSocket, session):
    try:
        while True:
            data = await ws.receive_text()
            message = json.loads(data)
            if message.get("type") == "audio":
                pcm = base64.b64decode(message["data"])
                await session.send(data=pcm)
            elif message.get("type") == "userText":
                txt = message.get("text", "")
                await session.send_client_content(turns=types.Content(role="user", parts=[types.Part.from_text(txt)]), turn_complete=True)
    except WebSocketDisconnect:
        pass


async def _session_to_ws(ws: WebSocket, session):
    try:
        async for msg in session.receive():
            out = {}
            if getattr(msg, "data", None):
                out["type"] = "audio"
                out["data"] = base64.b64encode(msg.data).decode()
            sc = getattr(msg, "server_content", None)
            if sc and getattr(sc, "output_transcription", None):
                out["caption"] = sc.output_transcription.text
            if out:
                await ws.send_json(out)
    except WebSocketDisconnect:
        pass


@app.post("/upload-image")
async def upload_image(file: UploadFile = File(...), prompt: str = Form("Mô tả nội dung ảnh.")):
    client = get_genai_client()
    data = await file.read()
    part = types.Part.from_bytes(data=data, mime_type=file.content_type)
    resp = await client.responses.generate_async(
        model="gemini-1.5-flash",  # text+image model
        contents=[types.Content(role="user", parts=[types.Part.from_text(prompt), part])],
    )
    text = resp.output[0].content[0].text if resp.output else ""
    return {"text": text}


HEYGEN_BASE = "https://api.heygen.com/v2"


@app.post("/avatar/create")
async def avatar_create(script: str = Form(...), avatar_id: str = Form(...), voice_id: str = Form(...)):
    client = get_http_client()
    api_key = os.getenv("HEYGEN_API_KEY")
    headers = {"X-Api-Key": api_key}
    payload = {"avatar_id": avatar_id, "voice_id": voice_id, "script": script}
    r = await client.post(f"{HEYGEN_BASE}/videos", json=payload, headers=headers)
    r.raise_for_status()
    return r.json()


@app.get("/avatar/status")
async def avatar_status(job_id: str):
    client = get_http_client()
    api_key = os.getenv("HEYGEN_API_KEY")
    headers = {"X-Api-Key": api_key}
    r = await client.get(f"{HEYGEN_BASE}/videos/{job_id}", headers=headers)
    r.raise_for_status()
    return r.json()


@app.on_event("shutdown")
async def shutdown_event():
    if _http_client:
        await _http_client.aclose()
