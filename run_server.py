"""Shim module for uvicorn.
Allows 'uvicorn run_server:app' to work when TECHXPO package path resolution is inconsistent in some platforms.
"""
import sys, os

# Ensure inner package parent on sys.path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INNER = os.path.join(BASE_DIR, 'TECHXPO')
if INNER not in sys.path:
    sys.path.insert(0, INNER)

try:
    from TECHXPO import combined_server as _cs
except ModuleNotFoundError:
    # Fallback: maybe combined_server copied into inner already
    import importlib
    _cs = importlib.import_module('combined_server')  # type: ignore

app = _cs.app
