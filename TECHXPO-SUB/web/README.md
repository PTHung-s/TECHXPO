# Web Kiosk for Gemini LiveKit Agent

This folder contains a minimal web UI (HTML + vanilla JS) and a FastAPI token server to join the same LiveKit room where your Python AI agent (`gemini_kiosk.py`) operates.

## Flow
1. Start LiveKit agent worker (your existing `gemini_kiosk.py`). It connects to `LIVEKIT_URL` and waits in a room (configured by environment or first job request).
2. Start this FastAPI server that mints ephemeral tokens.
3. Open the web page. Click **Bắt đầu** to join the room; speak with the AI doctor.

## Environment Variables
| Name | Description |
|------|-------------|
| `LIVEKIT_URL` | Your LiveKit server URL (wss://...). |
| `LIVEKIT_API_KEY` | API key with roomJoin permission. |
| `LIVEKIT_API_SECRET` | API secret. |
| `KIOSK_ROOM` | (Optional) Room name; defaults to `kiosk-room`. Agent must join same room. |
| `KIOSK_LOG_LEVEL` | (Optional) Log level for Python agent. |

## Install Dependencies
Inside your venv (`hic`):
```
pip install fastapi uvicorn livekit-api livekit-client
```
(If `livekit` meta package already installed, you likely have `livekit-api`.)

## Run Token Server
```
uvicorn server:app --port 8000 --reload
```
Visit: http://localhost:8000

## Run Agent (example)
You typically launch using LiveKit worker CLI or directly:
```
python ..\gemini_kiosk.py
```
Ensure it connects to the same room (if using a job dispatcher, configure the room). For quick manual test you can adjust agent code to always join `kiosk-room` or pass via environment.

## Customization
- Replace styling in `index.html`.
- Add chat transcript display using LiveKit data channels.
- Add push-to-talk: stop publishing the mic until button pressed.

## Security Notes
In production, restrict token endpoint (auth, rate limit, CORS). Never expose API secret to the browser—only the server generates tokens.

---
Happy testing!
