"""Organization HTTP API: the ``/organizations`` router bound at the composition root."""

from __future__ import annotations

from .router import (
    OrganizationApiDependencies,
    bind_organization_dependencies,
    create_organization_router,
)

__all__ = [
    "OrganizationApiDependencies",
    "bind_organization_dependencies",
    "create_organization_router",
]
