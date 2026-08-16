"""FastAPI HTTP adapter (IMPL-006, ARCH-004 / FR-KRN-006).

A thin inbound adapter that exposes the kernel command/query pipeline over HTTP: it validates
the request envelope, builds a canonical :class:`~northstar.kernel.context.RequestContext`, calls
the injected command/query buses (the single authoritative path, LAW-04) and maps typed kernel
errors to RFC 9457 ``application/problem+json``. FastAPI is confined to this adapter and the
``processes/api`` layer; the kernel stays infrastructure-free (rule 10). No business logic lives
here.
"""

from __future__ import annotations

from .app import create_app
from .dependencies import AppDependencies
from .problem_details import PROBLEM_CONTENT_TYPE, ProblemDetail, install_exception_handlers
from .rate_limit import (
    InMemoryRateLimiter,
    RateLimitGuard,
    RateLimitMiddleware,
    default_entry_point_resolver,
)

__all__ = [
    "PROBLEM_CONTENT_TYPE",
    "AppDependencies",
    "InMemoryRateLimiter",
    "ProblemDetail",
    "RateLimitGuard",
    "RateLimitMiddleware",
    "create_app",
    "default_entry_point_resolver",
    "install_exception_handlers",
]
