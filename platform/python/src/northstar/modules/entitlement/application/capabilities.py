"""Entitlement capabilities and the authoritative entitlement service (LAW-04, ARCH-019).

``entitlement.grant.create`` records a grant; ``entitlement.decision.evaluate`` returns an
entitlement decision. :class:`EntitlementService` is the one authoritative :class:`EntitlementPort`
and also adapts to the kernel :class:`EntitlementCheckPort` so the policy engine can gate a
governed action on an entitlement **without ever learning a plan/payment name** (ARCH-019).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from northstar.kernel.context import ResourceRef

from ..domain.model import (
    EntitlementDecision,
    EntitlementGrant,
    GrantOrigin,
    QuotaDisposition,
    decide,
)
from .ports import EntitlementPort, EntitlementRepositoryPort

CAP_VERSION = "1.0.0"
CAP_CREATE_GRANT = "entitlement.grant.create"
CAP_EVALUATE_DECISION = "entitlement.decision.evaluate"

# Resource type used when an entitlement decision is not tied to a concrete resource id.
ENTITLEMENT_RESOURCE_TYPE = "entitlement.capability"

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CreateGrantCommand:
    subject_id: str
    capability: str
    origin: GrantOrigin
    starts_at: datetime
    ends_at: datetime | None = None
    quota_limit: int | None = None
    quota_disposition: QuotaDisposition = QuotaDisposition.HARD_DENY
    organization_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreateGrantResult:
    grant_id: str
    subject_id: str
    capability: str


@dataclass(frozen=True, slots=True)
class EvaluateEntitlementQuery:
    subject_id: str
    action: str
    resource_type: str = ENTITLEMENT_RESOURCE_TYPE
    resource_id: str = "capability"


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


class EntitlementService(EntitlementPort):
    """The authoritative entitlement decision service (docs/07 §8)."""

    def __init__(
        self,
        *,
        repository: EntitlementRepositoryPort,
        clock: Clock = _utc_now,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def decide(
        self,
        *,
        subject_id: str,
        action: str,
        resource: ResourceRef,
        now: datetime | None = None,
    ) -> EntitlementDecision:
        grants = tuple(self._repo.list_grants_for_subject(subject_id))
        return decide(
            decision_id=self._id_factory(),
            actor_id=subject_id,
            action=action,
            resource_type=resource.type,
            resource_id=resource.id,
            grants=grants,
            now=now or self._clock(),
        )

    def is_entitled(self, actor_id: str, action: str, resource: ResourceRef | None) -> bool:
        """Kernel :class:`EntitlementCheckPort` adapter: boolean, plan-name-free (ARCH-019)."""
        target = resource or ResourceRef(type=ENTITLEMENT_RESOURCE_TYPE, id="capability")
        return self.decide(subject_id=actor_id, action=action, resource=target).allowed


class CreateGrant:
    """``entitlement.grant.create`` — record a commercial/contractual grant (docs/07 §8)."""

    def __init__(self, *, repository: EntitlementRepositoryPort, id_factory: IdFactory) -> None:
        self._repo = repository
        self._id_factory = id_factory

    def handle(self, request: object) -> CreateGrantResult:
        command = _typed(request, CreateGrantCommand)
        grant = EntitlementGrant(
            grant_id=self._id_factory(),
            subject_id=command.subject_id,
            capability=command.capability,
            origin=command.origin,
            starts_at=command.starts_at,
            ends_at=command.ends_at,
            quota_limit=command.quota_limit,
            quota_disposition=command.quota_disposition,
            organization_id=command.organization_id,
        )
        self._repo.add_grant(grant)
        return CreateGrantResult(
            grant_id=grant.grant_id, subject_id=grant.subject_id, capability=grant.capability
        )


class EvaluateEntitlement:
    """``entitlement.decision.evaluate`` (query) — return an entitlement decision."""

    def __init__(self, *, service: EntitlementService) -> None:
        self._service = service

    def handle(self, request: object) -> EntitlementDecision:
        query = _typed(request, EvaluateEntitlementQuery)
        return self._service.decide(
            subject_id=query.subject_id,
            action=query.action,
            resource=ResourceRef(type=query.resource_type, id=query.resource_id),
        )
