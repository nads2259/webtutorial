"""Governance Studio HTTP API: the ``/studio`` router bound at the composition root."""

from __future__ import annotations

from .router import (
    GovernanceStudioApiDependencies,
    bind_governance_studio_dependencies,
    create_governance_studio_router,
)

__all__ = [
    "GovernanceStudioApiDependencies",
    "bind_governance_studio_dependencies",
    "create_governance_studio_router",
]
