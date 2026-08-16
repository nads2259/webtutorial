"""Research HTTP API (thin inbound adapter over the kernel buses, LAW-04)."""

from __future__ import annotations

from .router import (
    ResearchApiDependencies,
    bind_research_dependencies,
    create_research_router,
)

__all__ = [
    "ResearchApiDependencies",
    "bind_research_dependencies",
    "create_research_router",
]
