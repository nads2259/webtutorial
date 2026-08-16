"""HTTP routers for the FastAPI adapter (commands, queries, health)."""

from __future__ import annotations

from . import commands, health, queries

__all__ = ["commands", "health", "queries"]
