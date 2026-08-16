"""Rate-limit / anti-automation at the HTTP trust boundary (EVAL-SEC-008, NFR-SEC-008).

Three pieces sit behind the kernel's pure rate-limit policy (:mod:`northstar.kernel.security`):

* :class:`InMemoryRateLimiter` — the reference ``RateLimiterPort``: a fixed-window counter store. A
  production distributed limiter (shared Redis/token-bucket service) is a drop-in adapter swap.
* :class:`RateLimitGuard` — applies the per-entry-point budget to a request's key, records a
  tamper-evident audit event on a throttle, and FAILS SAFE for authentication (a limiter *error*
  never opens a credential-stuffing window; other surfaces prefer availability and fail open on
  a limiter error only, never on a normal over-budget decision).
* :class:`RateLimitMiddleware` — maps a request to its :class:`GuardedEntryPoint` and per-actor/
  tenant/IP key, calls the guard and returns an RFC 9457 ``429`` ``problem+json`` with a
  ``Retry-After`` header when over budget (within budget the request proceeds untouched).

Keying uses IP for the anonymous/credential surface and the authenticated actor otherwise, scoped by
tenant — abuse controls never rely on IP alone (``abuse-case-catalog.md``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from northstar.kernel.audit.ports import AuditOutcome, AuditRecorderPort
from northstar.kernel.context import Actor, ActorType, ResourceRef
from northstar.kernel.security.rate_limit import (
    GuardedEntryPoint,
    RateBudget,
    RateLimitDecision,
    RateLimitExceeded,
    RateLimitKey,
    WindowState,
    default_budgets,
    evaluate,
)

from .problem_details import problem_from_rate_limit, problem_response

_LIMIT_ACTOR = Actor(type=ActorType.SERVICE, id="platform.rate-limiter")
_LIMIT_EVENT = "security.rate_limit.decision"
_LIMIT_ACTION = "platform.rate_limit.enforce"


class InMemoryRateLimiter:
    """Reference ``RateLimiterPort``: an in-process fixed-window counter (thread-safe)."""

    def __init__(self) -> None:
        self._windows: dict[str, WindowState] = {}
        self._lock = threading.Lock()

    def check(self, *, key: str, budget: RateBudget, now: datetime) -> RateLimitDecision:
        with self._lock:
            decision = evaluate(budget, self._windows.get(key), now)
            if decision.allowed:
                self._windows[key] = decision.state
            return decision

    def reset(self) -> None:
        """Clear all window state (test helper; never used on the production path)."""
        with self._lock:
            self._windows.clear()


class RateLimitGuard:
    """Applies budgets to keys, audits throttles and enforces the fail-safe rule for auth."""

    def __init__(
        self,
        *,
        limiter: InMemoryRateLimiter,
        audit: AuditRecorderPort | None = None,
        budgets: dict[GuardedEntryPoint, RateBudget] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._limiter = limiter
        self._audit = audit
        self._budgets = budgets or default_budgets()
        self._clock = clock

    def _audit_throttle(self, *, key: RateLimitKey, correlation_id: str) -> None:
        if self._audit is None:
            return
        self._audit.record(
            event_type=_LIMIT_EVENT,
            actor=_LIMIT_ACTOR,
            action=_LIMIT_ACTION,
            outcome=AuditOutcome.DENIED,
            correlation_id=correlation_id,
            resource=ResourceRef(type="rate_limit.entry_point", id=key.entry_point.value),
            reason_codes=("rate_limit.exceeded", f"dimension.{key.dimension}"),
        )

    def enforce(self, key: RateLimitKey, *, correlation_id: str) -> None:
        """Admit the request or raise :class:`RateLimitExceeded` (over budget / fail-safe deny).

        On a limiter *error*: authentication fails closed (raises) so the credential-stuffing window
        never opens; other entry points fail open (return) to preserve availability. A normal
        over-budget decision always denies, regardless of ``fail_open``.
        """
        budget = self._budgets[key.entry_point]
        try:
            decision = self._limiter.check(key=key.canonical(), budget=budget, now=self._clock())
        except Exception as exc:  # limiter backend error — apply the fail-safe policy
            if key.entry_point.fail_open:
                return
            self._audit_throttle(key=key, correlation_id=correlation_id)
            raise RateLimitExceeded(
                entry_point=key.entry_point.value,
                key=key.canonical(),
                retry_after_seconds=budget.window_seconds,
            ) from exc
        if not decision.allowed:
            self._audit_throttle(key=key, correlation_id=correlation_id)
            raise RateLimitExceeded(
                entry_point=key.entry_point.value,
                key=key.canonical(),
                retry_after_seconds=decision.retry_after_seconds,
            )


# Resolve a request (method + path) to its guarded entry point, or None to skip throttling.
EntryPointResolver = Callable[[str, str], GuardedEntryPoint | None]


def default_entry_point_resolver(method: str, path: str) -> GuardedEntryPoint | None:
    """Map the reference API's sensitive routes to their guarded entry point (doc 08 §7)."""
    if method == "POST":
        if path.startswith("/messaging/campaigns/") and path.endswith("/send"):
            return GuardedEntryPoint.CAMPAIGN_SEND
        if path == "/ai/answer" or path == "/learning/tutor/ask":
            return GuardedEntryPoint.AI_ANSWER
        if path == "/retrieval/search":
            return GuardedEntryPoint.SEARCH
        if path == "/support/cases":
            return GuardedEntryPoint.SUPPORT_INTAKE
        if path.startswith("/auth/mfa"):
            return GuardedEntryPoint.AUTHENTICATION
    if path in ("/auth/login", "/auth/callback"):
        return GuardedEntryPoint.AUTHENTICATION
    return None


@dataclass(frozen=True, slots=True)
class _KeyParts:
    dimension: str
    identifier: str
    tenant: str | None


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client is not None else "unknown"


def _resolve_key(entry_point: GuardedEntryPoint, request: Request) -> RateLimitKey:
    """Derive the throttle key: IP for the anonymous/auth surface, actor otherwise, tenant-scoped.

    The actor/tenant hints come from transport headers only (the request body is never consumed,
    so the downstream handler still reads it). This keeps keying cheap and side-effect-free.
    """
    tenant = request.headers.get("X-Tenant-Id") or request.headers.get("X-Tenant")
    actor = request.headers.get("X-Actor-Id")
    if entry_point is GuardedEntryPoint.AUTHENTICATION or not actor:
        parts = _KeyParts(dimension="ip", identifier=_client_ip(request), tenant=tenant)
    else:
        parts = _KeyParts(dimension="actor", identifier=actor, tenant=tenant)
    return RateLimitKey(
        entry_point=entry_point,
        dimension=parts.dimension,
        identifier=parts.identifier,
        tenant=parts.tenant,
    )


class RateLimitMiddleware:
    """ASGI middleware that throttles guarded entry points before the request reaches a handler."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        guard: RateLimitGuard,
        resolver: EntryPointResolver = default_entry_point_resolver,
    ) -> None:
        self._app = app
        self._guard = guard
        self._resolver = resolver

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        entry_point = self._resolver(request.method, request.url.path)
        if entry_point is None:
            await self._app(scope, receive, send)
            return
        key = _resolve_key(entry_point, request)
        correlation_id = request.headers.get("X-Correlation-Id") or f"rl-{key.canonical()}"
        try:
            self._guard.enforce(key, correlation_id=correlation_id)
        except RateLimitExceeded as exc:
            response: Response = problem_response(
                problem_from_rate_limit(exc, correlation_id=request.headers.get("X-Correlation-Id"))
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


__all__ = [
    "EntryPointResolver",
    "InMemoryRateLimiter",
    "RateLimitGuard",
    "RateLimitMiddleware",
    "default_entry_point_resolver",
]
