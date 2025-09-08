# server.py
import os, secrets, datetime as dt
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv(".env.local") or load_dotenv()

from livekit.api import AccessToken, VideoGrants, RoomConfiguration, RoomAgentDispatch

app = FastAPI(title="Kiosk Token Server")

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

STATIC_DIR = os.path.join(os.path.dirname(__file__), "public")
os.makedirs(STATIC_DIR, exist_ok=True)
IMAGES_DIR = os.path.join(os.path.dirname(__file__), "images")
if not os.path.exists(IMAGES_DIR):
    # allow user to keep images inside public/images alternatively
    alt = os.path.join(STATIC_DIR, "images")
    if os.path.isdir(alt):
        IMAGES_DIR = alt

class TokenResponse(BaseModel):
    url: str
    room: str
    token: str

def _rand(n=6): return secrets.token_hex(n//2)

# 1) --- API ROUTES (khai báo TRƯỚC) ---
@app.get("/api/token", response_model=TokenResponse)
def get_token(identity: Optional[str] = Query(None), room: Optional[str] = Query(None)):
    url = os.getenv("LIVEKIT_URL")
    if not url:
        raise HTTPException(500, "LIVEKIT_URL not configured")

    room = room or f"kiosk-{_rand(6)}"
    identity = identity or f"web-{_rand(8)}"
    agent_name = os.getenv("AGENT_NAME", "kiosk")

    grants = VideoGrants(room_join=True, room=room)
    cfg = RoomConfiguration(agents=[RoomAgentDispatch(agent_name=agent_name, metadata="from-web")])

    token = (AccessToken()
             .with_identity(identity)
             .with_name(identity)
             .with_grants(grants)
             .with_room_config(cfg)          # Dispatch agent khi client kết nối
             .with_ttl(dt.timedelta(minutes=5))
             .to_jwt())
    return TokenResponse(url=url, room=room, token=token)

@app.get("/healthz")
def healthz():
    return {"ok": True}

# Avoid noisy 404 from Chrome DevTools (some embedded webviews probe this path)
@app.get("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools_probe():
    return {"status": "ok", "description": "placeholder for chrome devtools probe"}

# (tuỳ chọn) favicon để khỏi 404
@app.get("/favicon.ico")
def favicon():
    path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(path):
        return FileResponse(path)
    return HTMLResponse("", status_code=204)

# 2) --- STATIC (mount SAU để không chặn /api/*) ---
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
if os.path.isdir(IMAGES_DIR) and IMAGES_DIR != STATIC_DIR:
    # mount explicit images route if images folder is sibling of public
    app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

# --- THÊM VÀO ĐÂY ---
# Chỉ cho server biết cách phục vụ các file ảnh từ thư mục web/public/images
# Khi frontend yêu cầu /images/BV_NAMSAIGON.png, server sẽ tìm file đó trong thư mục này.
images_dir = os.path.join(os.path.dirname(__file__), "..", "web", "public", "images")
app.mount("/images", StaticFiles(directory=images_dir), name="images")
# --------------------
