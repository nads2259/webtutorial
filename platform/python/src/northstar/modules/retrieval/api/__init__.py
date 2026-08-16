"""Retrieval HTTP API (thin inbound adapter over the kernel buses, LAW-04)."""

from __future__ import annotations

from .router import (
    RetrievalApiDependencies,
    bind_retrieval_dependencies,
    create_retrieval_router,
)

__all__ = [
    "RetrievalApiDependencies",
    "bind_retrieval_dependencies",
    "create_retrieval_router",
]
