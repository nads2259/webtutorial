"""Moderation HTTP API (thin inbound adapter over the kernel buses, LAW-04)."""

from __future__ import annotations

from .router import (
    ModerationApiDependencies,
    bind_moderation_dependencies,
    create_moderation_router,
)

__all__ = [
    "ModerationApiDependencies",
    "bind_moderation_dependencies",
    "create_moderation_router",
]
