"""Impersonation + break-glass capabilities: one authoritative implementation per action (LAW-04).

Every handler runs through the kernel command bus, so each invocation is authorized deny-by-default
and recorded as tamper-evident audit evidence (rule 50, LAW-14). Tenant scope and the acting
operator come from the authenticated :class:`RequestContext`, NEVER from the payload (rule 50).
Handlers depend only on :class:`ImpersonationRepositoryPort` and the pure :mod:`..domain`.

Capabilities (FR-IDN-007/008, closing EVAL-IDN-006/007):

* ``identity.impersonation.start`` — open a time-bounded, reasoned, (optionally) approved support
  impersonation session and return its VISIBLE INDICATION (``is_impersonation`` + both identities).
* ``identity.impersonation.end`` — end an impersonation session (idempotent).
* ``identity.breakglass.invoke`` — invoke exceptional, justified, time-bounded break-glass access,
  recorded high-severity and AUTO-ENQUEUING a mandatory post-use review.
* ``identity.breakglass.review.resolve`` — resolve the mandatory post-use review (single-effect).

:func:`build_impersonated_context` is the context builder that marks an impersonated request: it
produces a :class:`RequestContext` whose actor is the impersonated subject with ``delegated_by`` set
to the real operator, so every action dispatched under it is audited with BOTH identities.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from northstar.kernel.context import Actor, ActorType, RequestContext

from ..domain.impersonation import (
    BREAK_GLASS_SEVERITY,
    BreakGlassAccess,
    ImpersonationApprovalRequired,
    ImpersonationContext,
    ImpersonationGrant,
    PostUseReview,
    PostUseReviewNotFound,
    ReviewStatus,
)
from ..domain.impersonation import (
    ImpersonationInvalid as _ImpersonationInvalid,
)

CAP_VERSION = "1.0.0"

CAP_IMPERSONATION_START = "identity.impersonation.start"
CAP_IMPERSONATION_END = "identity.impersonation.end"
CAP_BREAKGLASS_INVOKE = "identity.breakglass.invoke"
CAP_BREAKGLASS_REVIEW_RESOLVE = "identity.breakglass.review.resolve"

IMPERSONATION_CAPABILITIES: tuple[str, ...] = (
    CAP_IMPERSONATION_START,
    CAP_IMPERSONATION_END,
    CAP_BREAKGLASS_INVOKE,
    CAP_BREAKGLASS_REVIEW_RESOLVE,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Repository port (a module owns its data — rule 10/13)
# ---------------------------------------------------------------------------


@runtime_checkable
class ImpersonationRepositoryPort(Protocol):
    """Persists impersonation grants, break-glass accesses and their post-use reviews.

    Every method is tenant-scoped: the store filters by ``tenant_scope`` and (on PostgreSQL) sets
    the tenant GUC so FORCED Row-Level Security applies as defense-in-depth (rule 50).
    """

    def add_impersonation(self, *, tenant_scope: str, grant: ImpersonationGrant) -> None: ...

    def get_impersonation(
        self, *, tenant_scope: str, grant_id: str
    ) -> ImpersonationGrant | None: ...

    def save_impersonation(self, *, tenant_scope: str, grant: ImpersonationGrant) -> None: ...

    def add_break_glass(self, *, tenant_scope: str, access: BreakGlassAccess) -> None: ...

    def add_post_use_review(self, *, tenant_scope: str, review: PostUseReview) -> None: ...

    def get_post_use_review(self, *, tenant_scope: str, review_id: str) -> PostUseReview | None: ...

    def save_post_use_review(self, *, tenant_scope: str, review: PostUseReview) -> None: ...


# ---------------------------------------------------------------------------
# Command payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StartImpersonationCommand:
    subject_id: str
    reason: str
    ttl_seconds: int = 900
    requires_approval: bool = False
    approver: str | None = None


@dataclass(frozen=True, slots=True)
class StartImpersonationResult:
    grant_id: str
    real_actor_id: str
    impersonated_subject_id: str
    is_impersonation: bool
    expires_at: str
    approved_by: str | None


@dataclass(frozen=True, slots=True)
class EndImpersonationCommand:
    grant_id: str


@dataclass(frozen=True, slots=True)
class EndImpersonationResult:
    grant_id: str
    ended: bool


@dataclass(frozen=True, slots=True)
class InvokeBreakGlassCommand:
    justification: str
    ttl_seconds: int = 900
    authorizer: str | None = None


@dataclass(frozen=True, slots=True)
class InvokeBreakGlassResult:
    access_id: str
    review_id: str
    severity: str
    review_status: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class ResolveReviewCommand:
    review_id: str
    resolution: str


@dataclass(frozen=True, slots=True)
class ResolveReviewResult:
    review_id: str
    status: str


# ---------------------------------------------------------------------------
# Invocation helpers (context is authoritative, never the payload — rule 50)
# ---------------------------------------------------------------------------


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    scope = getattr(context, "tenant_scope", None)
    if not scope:
        raise _ImpersonationInvalid(
            "a tenant scope is required (derived from the authenticated context)"
        )
    return str(scope)


def _actor_id(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    actor = getattr(context, "actor", None)
    actor_id = getattr(actor, "id", None)
    if not actor_id:
        raise _ImpersonationInvalid("an authenticated actor is required")
    return str(actor_id)


# ---------------------------------------------------------------------------
# Impersonated request-context builder (marks + dual-identity audits — FR-IDN-007)
# ---------------------------------------------------------------------------


def build_impersonated_context(
    context: ImpersonationContext,
    *,
    correlation_id: str,
    idempotency_key: str | None = None,
) -> RequestContext:
    """Build the request context an impersonated action runs under (both identities visible).

    The actor is the impersonated subject with ``delegated_by`` set to the real operator, so the
    command bus records BOTH identities on every audited action (dual-actor audit, docs/07 §11).
    The tenant scope is the impersonation context's scope (derived server-side, never a payload).
    """
    actor = Actor(
        type=ActorType.OPERATOR,
        id=context.impersonated_subject_id,
        delegated_by=context.real_actor_id,
    )
    return RequestContext(
        actor=actor,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        tenant_scope=context.tenant_scope,
    )


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class StartImpersonation:
    """``identity.impersonation.start`` — open a time-bounded, reasoned session (FR-IDN-007)."""

    def __init__(
        self, *, repository: ImpersonationRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> StartImpersonationResult:
        command = _typed(request, StartImpersonationCommand)
        tenant = _tenant(request)
        operator = _actor_id(request)
        # Approval where policy dictates: a subject flagged as requiring approval must name an
        # approver, else the session is refused before it exists (deny-by-default, FR-IDN-007).
        if command.requires_approval and not (command.approver and command.approver.strip()):
            raise ImpersonationApprovalRequired()
        now = self._clock()
        grant = ImpersonationGrant(
            grant_id=self._id_factory(),
            real_actor_id=operator,
            impersonated_subject_id=command.subject_id,
            reason=command.reason,
            started_at=now,
            expires_at=now + timedelta(seconds=max(1, command.ttl_seconds)),
            tenant_scope=tenant,
            approved_by=command.approver,
        )
        self._repo.add_impersonation(tenant_scope=tenant, grant=grant)
        return StartImpersonationResult(
            grant_id=grant.grant_id,
            real_actor_id=grant.real_actor_id,
            impersonated_subject_id=grant.impersonated_subject_id,
            is_impersonation=True,
            expires_at=grant.expires_at.isoformat(),
            approved_by=grant.approved_by,
        )


class EndImpersonation:
    """``identity.impersonation.end`` — end an impersonation session (idempotent, FR-IDN-007)."""

    def __init__(self, *, repository: ImpersonationRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> EndImpersonationResult:
        command = _typed(request, EndImpersonationCommand)
        tenant = _tenant(request)
        grant = self._repo.get_impersonation(tenant_scope=tenant, grant_id=command.grant_id)
        if grant is None or grant.ended_at is not None:
            # Idempotent: ending an unknown/already-ended session makes no change.
            return EndImpersonationResult(grant_id=command.grant_id, ended=False)
        self._repo.save_impersonation(tenant_scope=tenant, grant=grant.ended(self._clock()))
        return EndImpersonationResult(grant_id=command.grant_id, ended=True)


class InvokeBreakGlass:
    """``identity.breakglass.invoke`` — justified, time-bounded access + review (FR-IDN-008)."""

    def __init__(
        self, *, repository: ImpersonationRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> InvokeBreakGlassResult:
        command = _typed(request, InvokeBreakGlassCommand)
        tenant = _tenant(request)
        operator = _actor_id(request)
        now = self._clock()
        access = BreakGlassAccess(
            access_id=self._id_factory(),
            operator_id=operator,
            justification=command.justification,
            invoked_at=now,
            expires_at=now + timedelta(seconds=max(1, command.ttl_seconds)),
            tenant_scope=tenant,
            authorized_by=command.authorizer,
        )
        review = access.open_review(review_id=self._id_factory())
        # Exceptional + monitored: persist the high-severity access AND auto-enqueue the mandatory
        # post-use review that must be resolved (FR-IDN-008).
        self._repo.add_break_glass(tenant_scope=tenant, access=access)
        self._repo.add_post_use_review(tenant_scope=tenant, review=review)
        return InvokeBreakGlassResult(
            access_id=access.access_id,
            review_id=review.review_id,
            severity=BREAK_GLASS_SEVERITY,
            review_status=review.status.value,
            expires_at=access.expires_at.isoformat(),
        )


class ResolveBreakGlassReview:
    """``identity.breakglass.review.resolve`` — resolve the mandatory review (FR-IDN-008)."""

    def __init__(self, *, repository: ImpersonationRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> ResolveReviewResult:
        command = _typed(request, ResolveReviewCommand)
        tenant = _tenant(request)
        reviewer = _actor_id(request)
        review = self._repo.get_post_use_review(tenant_scope=tenant, review_id=command.review_id)
        if review is None:
            raise PostUseReviewNotFound()
        resolved = review.resolved(
            now=self._clock(), resolved_by=reviewer, resolution=command.resolution
        )
        self._repo.save_post_use_review(tenant_scope=tenant, review=resolved)
        return ResolveReviewResult(review_id=resolved.review_id, status=ReviewStatus.RESOLVED.value)


__all__ = [
    "CAP_BREAKGLASS_INVOKE",
    "CAP_BREAKGLASS_REVIEW_RESOLVE",
    "CAP_IMPERSONATION_END",
    "CAP_IMPERSONATION_START",
    "CAP_VERSION",
    "IMPERSONATION_CAPABILITIES",
    "EndImpersonation",
    "EndImpersonationCommand",
    "EndImpersonationResult",
    "ImpersonationRepositoryPort",
    "InvokeBreakGlass",
    "InvokeBreakGlassCommand",
    "InvokeBreakGlassResult",
    "ResolveBreakGlassReview",
    "ResolveReviewCommand",
    "ResolveReviewResult",
    "StartImpersonation",
    "StartImpersonationCommand",
    "StartImpersonationResult",
    "build_impersonated_context",
]
