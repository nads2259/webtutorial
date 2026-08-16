"""Privacy inbound API adapter (thin FastAPI router over the kernel buses)."""

from __future__ import annotations

from .router import (
    PrivacyApiDependencies,
    bind_privacy_dependencies,
    create_privacy_router,
)

__all__ = [
    "PrivacyApiDependencies",
    "bind_privacy_dependencies",
    "create_privacy_router",
]
