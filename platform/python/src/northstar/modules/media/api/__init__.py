"""Media HTTP API (thin inbound adapter over the kernel buses, LAW-04)."""

from __future__ import annotations

from .router import (
    MediaApiDependencies,
    bind_media_dependencies,
    create_media_router,
)

__all__ = [
    "MediaApiDependencies",
    "bind_media_dependencies",
    "create_media_router",
]
