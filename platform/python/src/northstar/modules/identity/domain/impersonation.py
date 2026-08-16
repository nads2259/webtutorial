"""Support-impersonation and break-glass domain (pure, docs/07 §11-12, FR-IDN-007/008).

These frozen value objects model the two exceptional access modes the identity module governs.
They are infrastructure-free (rule 10, LAW-02): every invariant is enforced by construction and
state transitions return new immutable instances. The kernel policy engine
(:mod:`northstar.kernel.policy`) already makes the *authorization decision* for an impersonated or
break-glass action; the objects here model the auditable session-mode *workflow* the identity
module owns — the grant/access records, their time bounds, the visible-indication context, and the
mandatory post-use review.

Invariants (never weakened):

* :class:`ImpersonationGrant` is TIME-BOUNDED (``expires_at`` strictly after ``started_at``),
  carries a stated non-empty ``reason``, records both the real ``operator`` and the
  ``impersonated_subject`` and, where policy dictates, an ``approved_by`` approver. Past
  ``expires_at`` (or once ended) it is no longer active (:meth:`ImpersonationGrant.is_active`).
* :class:`ImpersonationContext` is the VISIBLE INDICATION carried on the request:
  ``is_impersonation`` is always ``True`` and it names both identities, so every downstream action
  is dual-actor audited.
* :class:`BreakGlassAccess` requires an explicit ``justification``, is TIME-BOUNDED and is a
  high-severity event; :class:`PostUseReview` is the mandatory post-use review it enqueues, which
  starts :data:`ReviewStatus.PENDING` and must be resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from .errors import IdentityError

# Break-glass is always recorded at high severity (docs/07 §12, rule 50/LAW-14).
BREAK_GLASS_SEVERITY = "high"

# Stable resource vocabulary (audit/policy): the impersonation session and break-glass access.
RES_IMPERSONATION = "identity.impersonation"
RES_BREAK_GLASS = "identity.breakglass"


class ImpersonationInvalid(IdentityError):  # noqa: N818 canonical error name
    """An impersonation grant was constructed with missing/contradictory fields."""

    def __init__(self, message: str) -> None:
        from northstar.kernel.errors import Diagnostic

        super().__init__(
            message, (Diagnostic(code="identity.impersonation.invalid", message=message),)
        )


class ImpersonationExpired(IdentityError):  # noqa: N818 canonical error name
    """An impersonation session past its expiry (or ended) is not honored (FR-IDN-007)."""

    def __init__(self) -> None:
        from northstar.kernel.errors import Diagnostic

        message = "the impersonation session is not active"
        super().__init__(
            message, (Diagnostic(code="identity.impersonation.expired", message=message),)
        )


class ImpersonationApprovalRequired(IdentityError):  # noqa: N818 canonical error name
    """Impersonating this subject needs a named approver that was not supplied (FR-IDN-007)."""

    def __init__(self) -> None:
        from northstar.kernel.errors import Diagnostic

        message = "impersonation requires a named approver"
        super().__init__(
            message, (Diagnostic(code="identity.impersonation.approval-required", message=message),)
        )


class BreakGlassInvalid(IdentityError):  # noqa: N818 canonical error name
    """A break-glass access was constructed with missing/contradictory fields."""

    def __init__(self, message: str) -> None:
        from northstar.kernel.errors import Diagnostic

        super().__init__(
            message, (Diagnostic(code="identity.breakglass.invalid", message=message),)
        )


class PostUseReviewNotFound(IdentityError):  # noqa: N818 canonical error name
    """No post-use review exists for the referenced id/tenant."""

    def __init__(self) -> None:
        from northstar.kernel.errors import Diagnostic

        message = "the post-use review does not exist"
        super().__init__(
            message, (Diagnostic(code="identity.breakglass.review-not-found", message=message),)
        )


class PostUseReviewAlreadyResolved(IdentityError):  # noqa: N818 canonical error name
    """The post-use review has already been resolved (resolution is single-effect)."""

    def __init__(self) -> None:
        from northstar.kernel.errors import Diagnostic

        message = "the post-use review is already resolved"
        super().__init__(
            message, (Diagnostic(code="identity.breakglass.review-resolved", message=message),)
        )


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None:
        raise ImpersonationInvalid(f"{field} must be timezone-aware (UTC)")


@dataclass(frozen=True, slots=True)
class ImpersonationContext:
    """The VISIBLE INDICATION that a request runs under support impersonation (FR-IDN-007).

    ``is_impersonation`` is always ``True`` for this value; it names BOTH the real operator and the
    impersonated subject so any consumer (context builder, audit) can record both identities.
    """

    real_actor_id: str
    impersonated_subject_id: str
    tenant_scope: str | None = None
    is_impersonation: bool = True

    def __post_init__(self) -> None:
        if not self.real_actor_id:
            raise ImpersonationInvalid("impersonation context requires a real actor id")
        if not self.impersonated_subject_id:
            raise ImpersonationInvalid("impersonation context requires an impersonated subject id")
        if not self.is_impersonation:
            raise ImpersonationInvalid(
                "an impersonation context is always flagged is_impersonation"
            )


@dataclass(frozen=True, slots=True)
class ImpersonationGrant:
    """A time-bounded, reasoned support-impersonation grant (FR-IDN-007, docs/07 §11).

    An operator (``real_actor_id``) is authorized to act AS ``impersonated_subject_id`` within
    ``tenant_scope`` from ``started_at`` until ``expires_at``, tied to a stated ``reason`` and,
    where policy dictates, an ``approved_by`` approver. It is not active once ended or expired.
    """

    grant_id: str
    real_actor_id: str
    impersonated_subject_id: str
    reason: str
    started_at: datetime
    expires_at: datetime
    tenant_scope: str | None = None
    approved_by: str | None = None
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.grant_id:
            raise ImpersonationInvalid("impersonation grant requires a grant id")
        if not self.real_actor_id:
            raise ImpersonationInvalid("impersonation grant requires a real actor id")
        if not self.impersonated_subject_id:
            raise ImpersonationInvalid("impersonation grant requires an impersonated subject id")
        if self.real_actor_id == self.impersonated_subject_id:
            raise ImpersonationInvalid("an operator cannot impersonate itself")
        if not self.reason.strip():
            raise ImpersonationInvalid("impersonation grant requires a non-empty reason")
        _require_utc(self.started_at, "impersonation.started_at")
        _require_utc(self.expires_at, "impersonation.expires_at")
        if self.expires_at <= self.started_at:
            raise ImpersonationInvalid(
                "impersonation grant must be time-bounded (expires_at > started_at)"
            )

    def is_active(self, now: datetime) -> bool:
        """True iff the grant is neither ended nor past its expiry (deny-by-default)."""
        if self.ended_at is not None:
            return False
        return self.started_at <= now < self.expires_at

    def ended(self, now: datetime) -> ImpersonationGrant:
        """Return a copy marked ended at ``now`` (idempotent for an already-ended grant)."""
        if self.ended_at is not None:
            return self
        _require_utc(now, "impersonation.ended_at")
        return replace(self, ended_at=now)

    def context(self) -> ImpersonationContext:
        """The visible-indication context this grant projects onto a request."""
        return ImpersonationContext(
            real_actor_id=self.real_actor_id,
            impersonated_subject_id=self.impersonated_subject_id,
            tenant_scope=self.tenant_scope,
        )


def require_active_impersonation(
    grant: ImpersonationGrant | None, now: datetime
) -> ImpersonationGrant:
    """Return ``grant`` if it is active at ``now``, else raise :class:`ImpersonationExpired`.

    The single authoritative guard used before an action runs under an impersonation session, so an
    expired/ended/absent grant is never honored (FR-IDN-007).
    """
    if grant is None or not grant.is_active(now):
        raise ImpersonationExpired()
    return grant


class ReviewStatus(StrEnum):
    """Lifecycle of a break-glass post-use review (docs/07 §12)."""

    PENDING = "pending"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class BreakGlassAccess:
    """An exceptional, monitored, time-bounded break-glass access (FR-IDN-008, docs/07 §12).

    Requires an explicit ``justification`` and is short-lived (``expires_at`` after ``invoked_at``).
    It is recorded as a high-severity event (:data:`BREAK_GLASS_SEVERITY`) and always enqueues a
    :class:`PostUseReview`. ``authorized_by`` records a second-party authorizer where present.
    """

    access_id: str
    operator_id: str
    justification: str
    invoked_at: datetime
    expires_at: datetime
    tenant_scope: str | None = None
    authorized_by: str | None = None
    severity: str = BREAK_GLASS_SEVERITY

    def __post_init__(self) -> None:
        if not self.access_id:
            raise BreakGlassInvalid("break-glass access requires an access id")
        if not self.operator_id:
            raise BreakGlassInvalid("break-glass access requires an operator id")
        if not self.justification.strip():
            raise BreakGlassInvalid("break-glass access requires a non-empty justification")
        _require_utc(self.invoked_at, "breakglass.invoked_at")
        _require_utc(self.expires_at, "breakglass.expires_at")
        if self.expires_at <= self.invoked_at:
            raise BreakGlassInvalid(
                "break-glass access must be time-bounded (expires_at > invoked_at)"
            )

    def is_active(self, now: datetime) -> bool:
        return self.invoked_at <= now < self.expires_at

    def open_review(self, *, review_id: str) -> PostUseReview:
        """Create the mandatory PENDING post-use review this access auto-enqueues (FR-IDN-008)."""
        return PostUseReview(
            review_id=review_id,
            access_id=self.access_id,
            opened_at=self.invoked_at,
            tenant_scope=self.tenant_scope,
        )


@dataclass(frozen=True, slots=True)
class PostUseReview:
    """The mandatory post-use review a break-glass access enqueues (FR-IDN-008, docs/07 §12).

    It starts :data:`ReviewStatus.PENDING` and must be resolved by a reviewer with a stated
    resolution; resolution is single-effect (a resolved review cannot be resolved again).
    """

    review_id: str
    access_id: str
    opened_at: datetime
    tenant_scope: str | None = None
    status: ReviewStatus = ReviewStatus.PENDING
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution: str | None = None

    def __post_init__(self) -> None:
        if not self.review_id:
            raise BreakGlassInvalid("post-use review requires a review id")
        if not self.access_id:
            raise BreakGlassInvalid("post-use review requires an access id")
        _require_utc(self.opened_at, "review.opened_at")

    @property
    def is_pending(self) -> bool:
        return self.status is ReviewStatus.PENDING

    def resolved(self, *, now: datetime, resolved_by: str, resolution: str) -> PostUseReview:
        """Resolve the review; raise if already resolved or the resolution is empty."""
        if self.status is ReviewStatus.RESOLVED:
            raise PostUseReviewAlreadyResolved()
        if not resolved_by:
            raise BreakGlassInvalid("resolving a post-use review requires a reviewer id")
        if not resolution.strip():
            raise BreakGlassInvalid("resolving a post-use review requires a resolution note")
        _require_utc(now, "review.resolved_at")
        return replace(
            self,
            status=ReviewStatus.RESOLVED,
            resolved_at=now,
            resolved_by=resolved_by,
            resolution=resolution,
        )


__all__ = [
    "BREAK_GLASS_SEVERITY",
    "RES_BREAK_GLASS",
    "RES_IMPERSONATION",
    "BreakGlassAccess",
    "BreakGlassInvalid",
    "ImpersonationApprovalRequired",
    "ImpersonationContext",
    "ImpersonationExpired",
    "ImpersonationGrant",
    "ImpersonationInvalid",
    "PostUseReview",
    "PostUseReviewAlreadyResolved",
    "PostUseReviewNotFound",
    "ReviewStatus",
    "require_active_impersonation",
]
