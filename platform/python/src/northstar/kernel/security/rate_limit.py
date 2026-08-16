"""Pure anti-automation / rate-limit policy (LAW-02/08, rule 50, EVAL-SEC-008, NFR-SEC-008).

Stdlib-only and infrastructure-free: this module is the single authoritative rate-limit decision.
It computes whether a request fits within a per-key budget using a deterministic **fixed-window**
counter — given the current window state and the current time it returns the new state and a
decision (allowed, remaining, ``retry_after_seconds``). It never stores anything; the reference
in-memory adapter (behind :class:`RateLimiterPort`) owns the mutable window state, so a production
distributed limiter (Redis/etc.) is a drop-in adapter swap without touching this policy.

Doc 08 §7 requires *separate budgets* for the sensitive entry points (login, search, AI, support
intake, campaigns). :class:`GuardedEntryPoint` enumerates them and :func:`default_budgets` gives the
reference budgets; the exact numbers are a deployment default (the spec mandates *policy-aligned
throttling with separate budgets*, not fixed integers). Auth fails **safe** (never fail-open) so a
limiter outage cannot open a credential-stuffing window (:attr:`GuardedEntryPoint.fail_open`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..errors import Diagnostic, KernelError


class RateLimitExceeded(KernelError):  # noqa: N818 canonical error name
    """A request exceeded its per-key budget at a guarded entry point (deny, EVAL-SEC-008).

    Carries the ``entry_point``, the offending ``key`` and ``retry_after_seconds`` so the trust
    boundary can emit an RFC 9457 ``429`` with a ``Retry-After`` header and the audit trail records
    a tamper-evident throttle event. The ``detail`` never echoes secrets or attacker-controlled
    payload — only the stable entry-point label and the window's retry hint.
    """

    def __init__(self, *, entry_point: str, key: str, retry_after_seconds: int) -> None:
        self.entry_point = entry_point
        self.key = key
        self.retry_after_seconds = retry_after_seconds
        diag = Diagnostic(
            code="rate_limit.exceeded",
            message=f"rate limit exceeded for entry point '{entry_point}'",
            detail=f"retry_after={retry_after_seconds}s",
        )
        super().__init__(f"rate limit exceeded for '{entry_point}'", (diag,))


@dataclass(frozen=True, slots=True)
class RateBudget:
    """A fixed-window budget: at most ``limit`` requests per ``window_seconds`` window."""

    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("rate budget limit must be >= 1")
        if self.window_seconds < 1:
            raise ValueError("rate budget window_seconds must be >= 1")


@dataclass(frozen=True, slots=True)
class WindowState:
    """Immutable fixed-window counter state: when the window opened and how many hits it holds."""

    window_start_epoch: int
    count: int


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """The outcome of a budget check plus the window state to persist for the next check."""

    allowed: bool
    remaining: int
    retry_after_seconds: int
    state: WindowState


def _to_epoch(now: datetime) -> int:
    return int(now.timestamp())


def evaluate(budget: RateBudget, state: WindowState | None, now: datetime) -> RateLimitDecision:
    """Return the fixed-window decision for one request (pure, deterministic).

    A new window opens when there is no prior state or the current window has fully elapsed; the
    first request in a window is always admitted. Within an open window a request is admitted iff
    the count is still below ``limit``; otherwise it is denied with a ``retry_after`` equal to the
    seconds remaining until the window rolls over. The returned :attr:`RateLimitDecision.state` is
    what the adapter stores (unchanged on a deny, so a rejected request never consumes budget).
    """
    now_epoch = _to_epoch(now)
    if state is None or now_epoch >= state.window_start_epoch + budget.window_seconds:
        opened = WindowState(window_start_epoch=now_epoch, count=1)
        return RateLimitDecision(
            allowed=True,
            remaining=budget.limit - 1,
            retry_after_seconds=0,
            state=opened,
        )
    if state.count < budget.limit:
        advanced = WindowState(window_start_epoch=state.window_start_epoch, count=state.count + 1)
        return RateLimitDecision(
            allowed=True,
            remaining=budget.limit - advanced.count,
            retry_after_seconds=0,
            state=advanced,
        )
    retry_after = state.window_start_epoch + budget.window_seconds - now_epoch
    return RateLimitDecision(
        allowed=False,
        remaining=0,
        retry_after_seconds=max(retry_after, 1),
        state=state,
    )


class GuardedEntryPoint(StrEnum):
    """The sensitive entry points that carry a separate anti-automation budget (doc 08 §7).

    ``fail_open`` is ``False`` for authentication so a limiter outage FAILS SAFE (blocks) rather
    than opening a credential-stuffing window; the other surfaces prefer availability and fail open
    on a limiter *error* (never on a normal over-budget decision, which always denies).
    """

    AUTHENTICATION = "authentication"
    AI_ANSWER = "ai_answer"
    SEARCH = "search"
    SUPPORT_INTAKE = "support_intake"
    CAMPAIGN_SEND = "campaign_send"

    @property
    def fail_open(self) -> bool:
        return self is not GuardedEntryPoint.AUTHENTICATION


def default_budgets() -> dict[GuardedEntryPoint, RateBudget]:
    """Reference per-entry-point budgets (a deployment default; separate budget per surface)."""
    return {
        GuardedEntryPoint.AUTHENTICATION: RateBudget(limit=5, window_seconds=60),
        GuardedEntryPoint.AI_ANSWER: RateBudget(limit=20, window_seconds=60),
        GuardedEntryPoint.SEARCH: RateBudget(limit=60, window_seconds=60),
        GuardedEntryPoint.SUPPORT_INTAKE: RateBudget(limit=5, window_seconds=60),
        GuardedEntryPoint.CAMPAIGN_SEND: RateBudget(limit=10, window_seconds=60),
    }


@dataclass(frozen=True, slots=True)
class RateLimitKey:
    """A canonical, per-actor/tenant/IP throttle key scoped to one entry point (doc 08 §7).

    ``dimension`` records which identity axis keys the bucket (``ip`` for anonymous/credential
    stuffing, ``actor`` for an authenticated caller). Abuse controls must not rely on IP alone, so
    the tenant scope and the acting-identity axis are both folded into the canonical string.
    """

    entry_point: GuardedEntryPoint
    dimension: str
    identifier: str
    tenant: str | None = None

    def canonical(self) -> str:
        return f"{self.entry_point.value}:{self.tenant or '-'}:{self.dimension}:{self.identifier}"


@runtime_checkable
class RateLimiterPort(Protocol):
    """Applies a :class:`RateBudget` to a key and returns the decision (LAW-12).

    The reference implementation is an in-memory fixed-window counter; a production limiter (a
    shared Redis/token-bucket service) is a straight adapter swap behind this port. Implementations
    MUST persist the returned :attr:`RateLimitDecision.state` so a subsequent check for the same key
    sees the advanced window.
    """

    def check(self, *, key: str, budget: RateBudget, now: datetime) -> RateLimitDecision: ...


__all__ = [
    "GuardedEntryPoint",
    "RateBudget",
    "RateLimitDecision",
    "RateLimitExceeded",
    "RateLimitKey",
    "RateLimiterPort",
    "WindowState",
    "default_budgets",
    "evaluate",
]
