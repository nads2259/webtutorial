"""AI HTTP API (thin inbound adapter over the kernel buses, LAW-04)."""

from __future__ import annotations

from .router import AiApiDependencies, bind_ai_dependencies, create_ai_router

__all__ = ["AiApiDependencies", "bind_ai_dependencies", "create_ai_router"]
