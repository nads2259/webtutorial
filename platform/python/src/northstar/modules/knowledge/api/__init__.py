"""Knowledge HTTP API (thin inbound adapter over the kernel buses, LAW-04)."""

from __future__ import annotations

from .router import (
    KnowledgeApiDependencies,
    bind_knowledge_dependencies,
    create_knowledge_router,
)

__all__ = [
    "KnowledgeApiDependencies",
    "bind_knowledge_dependencies",
    "create_knowledge_router",
]
