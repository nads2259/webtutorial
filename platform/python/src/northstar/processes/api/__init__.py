"""Northstar API process (composition root + ASGI entrypoint).

Wires the real persistence/policy/audit adapters and kernel buses into the FastAPI HTTP adapter
(:mod:`northstar.adapters.http_fastapi`) and exposes an ASGI ``app`` for uvicorn. FastAPI/uvicorn
live only in this process/adapter layer (rule 10); the kernel stays infrastructure-free.
"""

from __future__ import annotations

from .wiring import build_app, build_dependencies

__all__ = ["build_app", "build_dependencies"]
