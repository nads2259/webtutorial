"""Governance HTTP API (thin inbound adapter over the kernel buses, LAW-04)."""

from __future__ import annotations

from .router import (
    GovernanceApiDependencies,
    bind_governance_dependencies,
    create_governance_router,
)

__all__ = [
    "GovernanceApiDependencies",
    "bind_governance_dependencies",
    "create_governance_router",
]
