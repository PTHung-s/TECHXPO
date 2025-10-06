# TECHXPO AI Medical Kiosk - Copilot Instructions

## System Architecture

This is a real-time AI medical consultation kiosk using **Gemini Live API + LiveKit** for voice interactions. The system consists of:

- **Core Agent** (`gemini_kiosk.py`): Main conversational AI using Gemini realtime LLM with function calling
- **Web Interface** (`web/`): LiveKit-based web client for patient interactions
- **Dashboard** (`Dashboard/`): FastAPI admin interface for appointment management (port 8090)
- **RAG System** (`med_rag.py`): Medical knowledge retrieval using TF-IDF + cosine similarity
- **Database Layer** (`storage.py`): SQLite with customer/visit tracking and personalization

## Key Conventions

### Environment Configuration
- Use `.env.local` or `.env` for sensitive config (never commit)
- Environment loading pattern: `load_dotenv(".env.local") or load_dotenv()`
- Critical vars: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`, `GOOGLE_API_KEY`
- Database path: `KIOSK_DB` (defaults to `kiosk.db`)
- Room name: `KIOSK_ROOM` (defaults to `kiosk-room`)
- Output directory: `KIOSK_OUT` (defaults to `out/`)
- Visit file saving: `SAVE_VISIT_FILES` ("always", "final", "none")

### Function Calling Architecture
All AI functions are defined in `function_calling_def.py` using the `@function_tool` decorator:
```python
@function_tool
async def propose_identity(context: RunContext, patient_name_partial: str = None, ...):
```

Functions must:
- Accept `RunContext` as first parameter
- Use `shared` dict for cross-function state management
- Call `publish_data()` for UI updates
- Follow the identity → booking → finalize workflow

**Critical Function Flow:**
1. `propose_identity` → `confirm_identity` (identity collection phase)
2. `schedule_appointment` → `choose_booking_option` (appointment booking phase)  
3. `finalize_visit` (conversation conclusion with background processing)

**State Management:** Functions communicate via `shared` dict keys:
- `booking_in_progress`: Prevents duplicate booking calls
- `latest_booking`: Current booking result with options
- `allow_finalize`: Gates access to `finalize_visit`
- `closing`: Prevents new operations during shutdown

### Database Patterns
- Customer IDs: `CUS-{hash}` format generated from phone numbers using `_stable_id_from_phone()`
- Visit tracking with `payload_json` for full conversation context
- Facts extraction and personalization stored in `customers.facts` and `last_summary`
- Use `_conn()` helper for WAL-mode SQLite connections
- Environment variable `KIOSK_DB` controls database path (defaults to `kiosk.db`)
- Visit files saved to `out/` directory (controlled by `SAVE_VISIT_FILES` env var)

### Development Workflows

**Local Development:**
```bash
# Activate environment (Windows PowerShell)
& C:\Users\PhamT\Python\sesame\hic\Scripts\Activate.ps1

# Run agent in development mode
python gemini_kiosk.py dev

# Run dashboard
cd Dashboard
uvicorn server:app --port 8090

# Run web interface  
cd web
uvicorn server:app --port 8000
```

**Testing and Debugging:**
```bash
# Interactive database viewer
python view_database.py

# Web-based database viewer
python serve_viewer.py  # Access at localhost:8080

# Check visit transcripts and outputs
ls out/  # Contains generated visit summaries
```

**Production Deployment:**
- Uses Docker Compose with separate containers for agent/dashboard
- Caddy reverse proxy handles routing
- Persistent volumes for `booking_data`, `dashboard_data`, `kiosk_data`

### Medical Domain Specifics

**Conversation States:** The agent follows a structured flow defined in `SYSTEM_PROMPT`:
1. `1_greeting` - Always start with "Dạ Xin chào! Em là Mét Ly..."
2. `2_identity_collection` - Phone/name gathering with `propose_identity`
3. `3_symptom_inquiry` - Medical symptom collection
4. `4_appointment_scheduling` - Use `schedule_appointment` function
5. `5_visit_conclusion` - Call `finalize_visit` to save

**Vietnamese Language:** All prompts and responses are in Vietnamese. Medical terminology follows Vietnamese healthcare conventions.

**RAG Integration:** Medical knowledge from `med_rules/` is automatically injected before LLM responses using cosine similarity retrieval.

## Integration Patterns

### LiveKit Real-time Communication
- Web client uses LiveKit for voice communication (`web/public/app.js`)
- Token-based authentication via FastAPI server (`web/server.py`)
- Room management: Default room `kiosk-room` (configurable via `KIOSK_ROOM`)
- Audio processing with noise cancellation plugins

### Data Flow Architecture
- **UI Updates:** Functions call `publish_data()` to send structured events to web client
- **State Persistence:** Background threads handle heavy operations (summary generation, fact extraction)
- **Booking Integration:** `booking.py` handles appointment scheduling with external systems
- **Error Resilience:** Fallback patterns throughout for graceful degradation

## File Organization

- `TECHXPO/` - Main application code
- `Booking_data/` - Appointment scheduling data and department configurations
- `KMS/` - Knowledge management system files
- `med_rules/` - Medical guidelines and protocols for RAG
- `Dashboard/` - Admin web interface
- `web/` - Patient-facing web kiosk
- `out/` - Generated visit summaries and transcripts

## Common Tasks

**Adding New Medical Knowledge:** Place files in `med_rules/` - RAG system auto-reloads on file changes.

**Debugging Conversations:** Check `out/` directory for visit transcripts, or use database viewer:
```bash
python view_database.py  # Interactive terminal viewer
python serve_viewer.py   # Web viewer at localhost:8080
```

**Modifying Agent Behavior:** Update `SYSTEM_PROMPT` in `gemini_kiosk.py` and conversation state definitions.

**Adding New Functions:** Define in `function_calling_def.py` with proper `@function_tool` decorator and state management.

**Background Processing Pattern:** The `finalize_visit` function demonstrates async background processing - it returns immediately but runs cleanup/persistence in a separate thread to avoid blocking the real-time conversation flow.

## Integration Points

- **LiveKit Cloud:** Real-time audio/video communication
- **Gemini Live API:** Conversational AI with function calling
- **Deepgram:** Speech-to-text processing
- **SQLite:** Local data persistence with WAL mode
- **FastAPI + Uvicorn:** Web services architecture
- **Docker + Caddy:** Production deployment stack