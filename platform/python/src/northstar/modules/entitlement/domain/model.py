"""Entitlement domain: grants, quotas, origins and the decision function (docs/07 §8, ARCH-019).

Pure and infrastructure-free (rule 10). An :class:`EntitlementGrant` records *who* (subject or
organization), *what* (a capability/resource scope), *how much* (quota), *when* (validity) and
*why* (an :class:`GrantOrigin` — an origin **type**, never a plan/payment name). The
:func:`decide` function combines the active grants for a subject into an
:class:`EntitlementDecision` whose reason codes are generic (ARCH-019, FR-POL-005): callers learn
*whether* they are entitled, never *which* plan, order or provider produced the grant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

RESULT_ALLOW = "allow"
RESULT_DENY = "deny"

REASON_GRANT_ACTIVE = "ENTITLEMENT_GRANT_ACTIVE"
REASON_NO_GRANT = "ENTITLEMENT_NO_ACTIVE_GRANT"
REASON_QUOTA_EXCEEDED = "ENTITLEMENT_QUOTA_EXCEEDED"

POLICY_VERSION = "1.0.0"


class GrantOrigin(StrEnum):
    """Where a grant came from (docs/07 §8). An origin *type* — not a plan/payment name."""

    FREE_POLICY = "free_policy"
    PURCHASE = "purchase"
    SUBSCRIPTION = "subscription"
    ENTERPRISE_AGREEMENT = "enterprise_agreement"
    PROMOTION = "promotion"
    SPONSORSHIP = "sponsorship"
    STAFF_GRANT = "staff_grant"
    MIGRATION = "migration"


class QuotaDisposition(StrEnum):
    """How a quota boundary is enforced (docs/07 §8)."""

    HARD_DENY = "hard_deny"
    SOFT_LIMIT = "soft_limit"
    WARNING = "warning"
    OVERAGE = "overage"


class EntitlementInvariantViolation(ValueError):  # noqa: N818 canonical error name
    """A grant was constructed with contradictory validity or quota values."""


@dataclass(frozen=True, slots=True)
class EntitlementGrant:
    """A commercial/contractual grant of a capability scope to a subject or organization."""

    grant_id: str
    subject_id: str
    capability: str
    origin: GrantOrigin
    starts_at: datetime
    ends_at: datetime | None = None
    quota_limit: int | None = None
    quota_used: int = 0
    quota_disposition: QuotaDisposition = QuotaDisposition.HARD_DENY
    organization_id: str | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.grant_id:
            raise EntitlementInvariantViolation("grant_id must be non-empty")
        if not self.subject_id:
            raise EntitlementInvariantViolation("subject_id must be non-empty")
        if not self.capability:
            raise EntitlementInvariantViolation("capability scope must be non-empty")
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise EntitlementInvariantViolation("ends_at must be after starts_at")
        if self.quota_limit is not None and self.quota_limit < 0:
            raise EntitlementInvariantViolation("quota_limit must not be negative")
        if self.quota_used < 0:
            raise EntitlementInvariantViolation("quota_used must not be negative")

    def is_active(self, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        if now < self.starts_at:
            return False
        return self.ends_at is None or now < self.ends_at

    def covers(self, action: str) -> bool:
        """True when this grant's capability scope covers ``action`` (exact or dotted prefix)."""
        return action == self.capability or action.startswith(f"{self.capability}.")

    @property
    def quota_available(self) -> bool:
        if self.quota_limit is None:
            return True
        if self.quota_disposition in (QuotaDisposition.SOFT_LIMIT, QuotaDisposition.OVERAGE):
            return True  # soft limits/overage never hard-deny; they warn/meter instead
        return self.quota_used < self.quota_limit


@dataclass(frozen=True, slots=True)
class GrantView:
    """A non-disclosing projection of a grant for the decision contract (no plan names)."""

    grant_type: str
    source: str
    limits: dict[str, int]
    valid_until: datetime | None


@dataclass(frozen=True, slots=True)
class EntitlementDecision:
    """An entitlement decision matching the ``entitlement-decision`` contract shape."""

    decision_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    effect: str
    decided_at: datetime
    policy_version: str = POLICY_VERSION
    reason_codes: tuple[str, ...] = ()
    grants: tuple[GrantView, ...] = ()
    expires_at: datetime | None = None
    obligations: tuple[dict[str, object], ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.effect == RESULT_ALLOW

    def to_contract(self) -> dict[str, object]:
        """Serialise to the ``entitlement-decision`` JSON contract (schema-valid)."""
        payload: dict[str, object] = {
            "decision_id": self.decision_id,
            "actor_id": self.actor_id,
            "action": self.action,
            "resource": {"type": self.resource_type, "id": self.resource_id},
            "effect": self.effect,
            "decided_at": _iso(self.decided_at),
            "policy_version": self.policy_version,
            "reason_codes": list(self.reason_codes),
            "obligations": [dict(o) for o in self.obligations],
            "grants": [
                {
                    "grant_type": g.grant_type,
                    "source": g.source,
                    "limits": dict(g.limits),
                    "valid_until": _iso(g.valid_until) if g.valid_until else None,
                }
                for g in self.grants
            ],
            "expires_at": _iso(self.expires_at) if self.expires_at else None,
        }
        return payload


def _iso(value: datetime) -> str:
    return value.isoformat()


def decide(
    *,
    decision_id: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    grants: tuple[EntitlementGrant, ...],
    now: datetime,
) -> EntitlementDecision:
    """Combine a subject's grants into a decision (deny-by-default; never exposes plan names)."""
    active = [g for g in grants if g.is_active(now) and g.covers(action)]
    if not active:
        return EntitlementDecision(
            decision_id=decision_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            effect=RESULT_DENY,
            decided_at=now,
            reason_codes=(REASON_NO_GRANT,),
        )
    with_quota = [g for g in active if g.quota_available]
    if not with_quota:
        return EntitlementDecision(
            decision_id=decision_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            effect=RESULT_DENY,
            decided_at=now,
            reason_codes=(REASON_QUOTA_EXCEEDED,),
        )
    views = tuple(
        GrantView(
            grant_type="capability_grant",
            source=g.origin.value,  # origin *type*, never a plan/payment name (ARCH-019)
            limits=({"quota": g.quota_limit} if g.quota_limit is not None else {}),
            valid_until=g.ends_at,
        )
        for g in with_quota
    )
    soonest = min((g.ends_at for g in with_quota if g.ends_at is not None), default=None)
    return EntitlementDecision(
        decision_id=decision_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        effect=RESULT_ALLOW,
        decided_at=now,
        reason_codes=(REASON_GRANT_ACTIVE,),
        grants=views,
        expires_at=soonest,
    )
