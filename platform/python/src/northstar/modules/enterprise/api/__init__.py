"""Enterprise HTTP API (thin inbound adapter over the kernel buses, LAW-04)."""

from __future__ import annotations

from .router import (
    EnterpriseApiDependencies,
    bind_enterprise_dependencies,
    create_enterprise_router,
)

__all__ = [
    "EnterpriseApiDependencies",
    "bind_enterprise_dependencies",
    "create_enterprise_router",
]
