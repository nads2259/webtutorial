"""Dependency-injection container for the HTTP adapter (no globals, rule 20 §D).

The buses and health/version ports are injected into :func:`create_app` as
:class:`AppDependencies` and stored on ``app.state``; routers read them through
:func:`get_dependencies`. Nothing is resolved via a global/service-locator — the process layer
constructs the concrete adapters and owns their lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from northstar.kernel.health.ports import HealthProbePort, VersionPort
from northstar.kernel.messaging import CommandBus, QueryBus

from .rate_limit import RateLimitGuard

_STATE_KEY = "northstar_http_dependencies"


@dataclass(frozen=True, slots=True)
class AppDependencies:
    """The ports the HTTP adapter needs, injected at construction (constructor injection).

    ``rate_limiter`` is optional: when provided, :func:`create_app` installs the anti-automation
    middleware (EVAL-SEC-008) that throttles the sensitive entry points; when ``None`` (e.g. narrow
    unit tests) the app serves unthrottled.
    """

    command_bus: CommandBus
    query_bus: QueryBus
    health: HealthProbePort
    version: VersionPort
    rate_limiter: RateLimitGuard | None = None


def get_dependencies(request: Request) -> AppDependencies:
    """Return the :class:`AppDependencies` bound to the running app (FastAPI dependency)."""
    deps: AppDependencies = getattr(request.app.state, _STATE_KEY)
    return deps


def bind_dependencies(app_state: object, deps: AppDependencies) -> None:
    """Attach ``deps`` to ``app.state`` under the adapter's private key."""
    setattr(app_state, _STATE_KEY, deps)
