"""FastAPI application factory (thin inbound adapter over the kernel buses).

:func:`create_app` wires the command/query/health routers onto a fresh :class:`FastAPI` app,
installs the RFC 9457 problem-details handlers and binds the injected :class:`AppDependencies`
to ``app.state`` (no globals, rule 20 §D). The app is deliberately business-logic-free: it
validates the request envelope, calls the bus and maps the typed result/errors. FastAPI serves an
OpenAPI **3.1.0** document (docs/05 §2) at ``/openapi.json`` by default.
"""

from __future__ import annotations

from fastapi import FastAPI

from .dependencies import AppDependencies, bind_dependencies
from .problem_details import install_exception_handlers
from .rate_limit import RateLimitMiddleware
from .routers import commands, health, queries

API_TITLE = "Northstar HTTP API"
API_VERSION = "v1"


def create_app(deps: AppDependencies) -> FastAPI:
    """Build the HTTP adapter app bound to ``deps`` (constructor injection)."""
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        summary="Thin inbound HTTP adapter over the Northstar kernel command/query buses.",
    )
    bind_dependencies(app.state, deps)
    install_exception_handlers(app)
    # Anti-automation throttling at the edge (EVAL-SEC-008): over-budget requests to sensitive
    # entry points get a 429 problem+json before reaching a handler. Only wired when a limiter is
    # injected, so narrow unit tests can opt out.
    if deps.rate_limiter is not None:
        app.add_middleware(RateLimitMiddleware, guard=deps.rate_limiter)
    app.include_router(commands.router)
    app.include_router(queries.router)
    app.include_router(health.router)
    return app
