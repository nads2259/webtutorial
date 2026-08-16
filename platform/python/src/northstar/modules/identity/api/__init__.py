"""Identity inbound HTTP adapter: the ``/auth/*`` FastAPI router (thin edge, rule 10).

Holds no business logic — it validates transport concerns (cookies, CSRF header), builds the
kernel :class:`~northstar.kernel.context.RequestContext` and dispatches capabilities through the
command/query buses (LAW-04). Mounted by the API composition root (``processes/api``).
"""

from __future__ import annotations

from .router import (
    IdentityApiDependencies,
    IdentityCookieConfig,
    bind_identity_dependencies,
    create_identity_router,
)

__all__ = [
    "IdentityApiDependencies",
    "IdentityCookieConfig",
    "bind_identity_dependencies",
    "create_identity_router",
]
