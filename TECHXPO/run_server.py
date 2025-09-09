"""Entry point for unified server (inside package).
Use: uvicorn TECHXPO.run_server:app --host 0.0.0.0 --port 8080
"""
from . import combined_server as _cs

app = _cs.app
