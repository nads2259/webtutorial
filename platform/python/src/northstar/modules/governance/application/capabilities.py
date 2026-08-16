"""Governance capabilities: one authoritative implementation per action (LAW-04).

Each handler runs through the kernel command/query bus, so every mutation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope is taken from the authenticated
:class:`RequestContext` (``tenant_scope``), **never** from the payload (rule 50). On top of the
capability-layer grant, granting/revoking a control exception enforces a domain authorization rule:
only an **authorized approver** may grant or revoke (deny-by-default) — a control exception cannot
be created without both an approver and an explicit expiry (FR-GOV-002).

The five authoritative capabilities:

* ``governance.decision.record`` — record an immutable, traceable decision (EVAL-GOV-001);
* ``governance.decision.supersede`` — record a NEW decision that supersedes a prior (never mutates);
* ``governance.exception.grant`` — grant a scoped, approved, time-bounded exception (FR-GOV-002);
* ``governance.exception.revoke`` — revoke an exception (it is no longer honored);
* ``governance.exception.evaluate`` — a clock-driven query: is a control's exception still honored,
  i.e. ``no_expired_exception`` (EVAL-GOV-002)?
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from northstar.kernel.context import Actor
from northstar.kernel.errors import PolicyDenied

from ..domain.errors import (
    ControlExceptionNotFound,
    DecisionNotFound,
    GovernanceInvariantViolation,
    TenantScopeMissing,
)
from ..domain.model import (
    ControlException,
    DecisionLinks,
    DecisionRecord,
    DecisionStatus,
    ExceptionStatus,
    evaluate_exception,
    no_expired_exception,
)
from .ports import ApproverDirectoryPort, GovernanceRepositoryPort

CAP_VERSION = "1.0.0"

CAP_RECORD_DECISION = "governance.decision.record"
CAP_SUPERSEDE_DECISION = "governance.decision.supersede"
CAP_GRANT_EXCEPTION = "governance.exception.grant"
CAP_REVOKE_EXCEPTION = "governance.exception.revoke"
CAP_EVALUATE_EXCEPTION = "governance.exception.evaluate"

GOVERNANCE_CAPABILITIES: tuple[str, ...] = (
    CAP_RECORD_DECISION,
    CAP_SUPERSEDE_DECISION,
    CAP_GRANT_EXCEPTION,
    CAP_REVOKE_EXCEPTION,
    CAP_EVALUATE_EXCEPTION,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecordDecisionCommand:
    title: str
    rationale: str
    status: str = DecisionStatus.ACCEPTED_BASELINE.value
    controls: tuple[str, ...] = field(default=())
    requirements: tuple[str, ...] = field(default=())
    gates: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class SupersedeDecisionCommand:
    prior_decision_id: str
    title: str
    rationale: str
    status: str = DecisionStatus.ACCEPTED_BASELINE.value
    controls: tuple[str, ...] = field(default=())
    requirements: tuple[str, ...] = field(default=())
    gates: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision_id: str
    status: str
    title: str
    controls: tuple[str, ...]
    requirements: tuple[str, ...]
    gates: tuple[str, ...]
    supersedes: str | None


@dataclass(frozen=True, slots=True)
class GrantExceptionCommand:
    control: str
    subject: str
    expiry: datetime
    rationale: str


@dataclass(frozen=True, slots=True)
class RevokeExceptionCommand:
    exception_id: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExceptionResult:
    exception_id: str
    control: str
    subject: str
    status: str
    expiry: datetime
    approver_id: str
    active: bool


@dataclass(frozen=True, slots=True)
class EvaluateExceptionQuery:
    control: str


@dataclass(frozen=True, slots=True)
class EvaluateExceptionResult:
    control: str
    honored: bool
    evaluated_at: datetime
    active_exception_id: str | None
    expired_exception_ids: tuple[str, ...]


# ---------------------------------------------------------------------------
# Shared helpers
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
        raise TenantScopeMissing()
    return scope


def _actor(invocation: object) -> Actor:
    context = getattr(invocation, "context", None)
    return context.actor


def _status(value: str) -> DecisionStatus:
    try:
        return DecisionStatus(value)
    except ValueError as exc:
        raise GovernanceInvariantViolation(
            f"invalid decision status {value!r}", code="governance.decision.status"
        ) from exc


def _links(
    controls: Sequence[str], requirements: Sequence[str], gates: Sequence[str]
) -> DecisionLinks:
    return DecisionLinks.of(controls=controls, requirements=requirements, gates=gates)


def _deny(action: str, code: str, message: str) -> PolicyDenied:
    return PolicyDenied(action=action, decision_id="governance-authz", reasons=((code, message),))


def _require_approver(
    approvers: ApproverDirectoryPort, *, action: str, organization_id: str, actor: Actor
) -> None:
    """Deny-by-default: only an authorized approver may grant/revoke a control exception."""
    if approvers.is_approver(organization_id=organization_id, actor_id=actor.id):
        return
    raise _deny(
        action,
        "GOVERNANCE_NOT_AN_APPROVER",
        "only an authorized approver may grant or revoke a control exception",
    )


def _decision_result(decision: DecisionRecord) -> DecisionResult:
    return DecisionResult(
        decision_id=decision.decision_id,
        status=decision.status.value,
        title=decision.title,
        controls=decision.links.controls,
        requirements=decision.links.requirements,
        gates=decision.links.gates,
        supersedes=decision.supersedes,
    )


def _exception_result(exception: ControlException, *, now: datetime) -> ExceptionResult:
    return ExceptionResult(
        exception_id=exception.exception_id,
        control=exception.control,
        subject=exception.subject,
        status=exception.status.value,
        expiry=exception.expiry,
        approver_id=exception.approver.id,
        active=exception.is_active(now),
    )


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class RecordDecision:
    """``governance.decision.record`` — record an immutable, traceable decision (EVAL-GOV-001)."""

    def __init__(
        self, *, repository: GovernanceRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> DecisionResult:
        command = _typed(request, RecordDecisionCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        decision = DecisionRecord(
            decision_id=self._id_factory(),
            organization_id=organization_id,
            title=command.title,
            status=_status(command.status),
            rationale=command.rationale,
            decider=actor,
            recorded_at=self._clock(),
            links=_links(command.controls, command.requirements, command.gates),
        )
        self._repo.add_decision(decision)
        return _decision_result(decision)


class SupersedeDecision:
    """``governance.decision.supersede`` — record a NEW decision superseding a prior (immutable).

    The prior record is loaded (tenant-scoped) purely to link the new record back to it; it is
    never modified, honoring the immutability invariant (EVAL-GOV-001, LAW-07).
    """

    def __init__(
        self, *, repository: GovernanceRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> DecisionResult:
        command = _typed(request, SupersedeDecisionCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        prior = self._repo.get_decision(
            organization_id=organization_id, decision_id=command.prior_decision_id
        )
        if prior is None:
            raise DecisionNotFound()
        successor = prior.supersede(
            decision_id=self._id_factory(),
            title=command.title,
            rationale=command.rationale,
            decider=actor,
            recorded_at=self._clock(),
            links=_links(command.controls, command.requirements, command.gates),
            status=_status(command.status),
        )
        self._repo.add_decision(successor)
        return _decision_result(successor)


class GrantException:
    """``governance.exception.grant`` — grant a scoped, approved, time-bounded exception.

    Deny-by-default: only an authorized approver may grant. The domain refuses construction without
    both an approver and an explicit expiry (FR-GOV-002); the acting authorized approver IS the
    recorded approver.
    """

    def __init__(
        self,
        *,
        repository: GovernanceRepositoryPort,
        approvers: ApproverDirectoryPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._approvers = approvers
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> ExceptionResult:
        command = _typed(request, GrantExceptionCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        _require_approver(
            self._approvers,
            action=CAP_GRANT_EXCEPTION,
            organization_id=organization_id,
            actor=actor,
        )
        if command.expiry is None:  # type: ignore[redundant-expr] defensive: explicit expiry
            raise GovernanceInvariantViolation(
                "a control exception requires an explicit expiry",
                code="governance.exception.expiry_required",
            )
        if not command.rationale:
            raise GovernanceInvariantViolation(
                "a control exception requires a rationale (compensating control, auditable)",
                code="governance.exception.rationale_required",
            )
        now = self._clock()
        exception = ControlException(
            exception_id=self._id_factory(),
            organization_id=organization_id,
            control=command.control,
            subject=command.subject,
            approver=actor,
            granted_by=actor,
            rationale=command.rationale,
            expiry=command.expiry,
            granted_at=now,
        )
        self._repo.add_exception(exception)
        return _exception_result(exception, now=now)


class RevokeException:
    """``governance.exception.revoke`` — revoke an exception (deny-by-default; then not honored)."""

    def __init__(
        self,
        *,
        repository: GovernanceRepositoryPort,
        approvers: ApproverDirectoryPort,
        clock: Clock,
    ) -> None:
        self._repo = repository
        self._approvers = approvers
        self._clock = clock

    def handle(self, request: object) -> ExceptionResult:
        command = _typed(request, RevokeExceptionCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        _require_approver(
            self._approvers,
            action=CAP_REVOKE_EXCEPTION,
            organization_id=organization_id,
            actor=actor,
        )
        exception = self._repo.get_exception(
            organization_id=organization_id, exception_id=command.exception_id
        )
        if exception is None:
            raise ControlExceptionNotFound()
        now = self._clock()
        if exception.status is ExceptionStatus.REVOKED:
            # Idempotent revoke: already revoked stays revoked, no error.
            return _exception_result(exception, now=now)
        revoked = exception.revoke(actor=actor, at=now)
        self._repo.update_exception(revoked)
        return _exception_result(revoked, now=now)


class EvaluateException:
    """``governance.exception.evaluate`` (query) — clock-driven ``no_expired_exception`` check.

    Loads every exception scoped to the control (tenant-scoped) and evaluates them under the
    injected clock: ``honored`` is ``True`` only when a non-expired, approved exception exists
    (EVAL-GOV-002). Expired/revoked exceptions are reported separately as evidence.
    """

    def __init__(self, *, repository: GovernanceRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> EvaluateExceptionResult:
        query = _typed(request, EvaluateExceptionQuery)
        organization_id = _tenant(request)
        now = self._clock()
        exceptions = list(
            self._repo.list_exceptions_for_control(
                organization_id=organization_id, control=query.control
            )
        )
        honored = no_expired_exception(query.control, now, exceptions)
        active_id = next(
            (e.exception_id for e in exceptions if evaluate_exception(e, now)),
            None,
        )
        expired_ids = tuple(
            e.exception_id
            for e in exceptions
            if e.status is ExceptionStatus.ACTIVE and e.is_expired(now)
        )
        return EvaluateExceptionResult(
            control=query.control,
            honored=honored,
            evaluated_at=now,
            active_exception_id=active_id,
            expired_exception_ids=expired_ids,
        )
