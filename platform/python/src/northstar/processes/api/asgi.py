"""ASGI entrypoint: ``northstar.processes.api.asgi:app`` for uvicorn/gunicorn.

Building the app resolves ``DATABASE_URL`` and constructs the engine at import time, so this
module is imported by the server process (which has configuration present) rather than by unit
tests, which build their own app via :func:`~northstar.processes.api.wiring.build_app`.
"""

from __future__ import annotations

from .wiring import build_app

app = build_app()
