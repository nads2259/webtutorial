"""Support capabilities: one authoritative implementation per action (LAW-04, docs/29 §6).

Every handler runs through the kernel command/query bus, so each invocation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope + acting subject come from the
authenticated :class:`RequestContext`, NEVER from the payload (rule 50). Handlers depend only on
:mod:`.ports` and the pure :mod:`..domain`.

The support invariants are enforced here by construction and are never weakened:

* ``support.intake`` VALIDATES input and rejects malformed/oversized/injection-shaped submissions
  before a case exists (FR-SUP-001). A case has an owner (requester) + lifecycle (FR-SUP-002).
* ``support.case.assign`` / ``support.case.transition`` / ``support.case.reply`` drive the governed
  lifecycle; only allowed status transitions are permitted (FR-SUP-002).
* ``support.case.view`` returns the MINIMIZED projection by default; an elevated/broad read requires
  an ACTIVE, time-bounded support-access grant — an unauthorized elevated read is REFUSED and the
  attempt is LOGGED (FR-SUP-003).
* ``support.access.grant`` / ``support.access.revoke`` manage the audited, deny-by-default,
  time-bounded support-access grants (FR-SUP-003).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..domain.errors import CaseNotFound, SupportAccessDenied, TenantScopeMissing
from ..domain.model import (
    RES_SUPPORT_CASE,
    AuthorType,
    CaseStatus,
    MessageVisibility,
    SupportAccessGrant,
    SupportCase,
    SupportMessage,
    elevated_view,
    minimized_view,
    validate_intake,
)
from .ports import SupportRepositoryPort

CAP_VERSION = "1.0.0"

CAP_INTAKE = "support.intake"
CAP_ASSIGN = "support.case.assign"
CAP_TRANSITION = "support.case.transition"
CAP_REPLY = "support.case.reply"
CAP_VIEW = "support.case.view"
CAP_ACCESS_GRANT = "support.access.grant"
CAP_ACCESS_REVOKE = "support.access.revoke"

SUPPORT_CAPABILITIES: tuple[str, ...] = (
    CAP_INTAKE,
    CAP_ASSIGN,
    CAP_TRANSITION,
    CAP_REPLY,
    CAP_VIEW,
    CAP_ACCESS_GRANT,
    CAP_ACCESS_REVOKE,
)

_ACCESS_DECISION_GRANTED = "granted"
_ACCESS_DECISION_DENIED = "denied"
_SCOPE_MINIMIZED = "minimized"
_SCOPE_FULL = "full_case"

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubmitIntakeCommand:
    subject: str
    category: str
    body: str
    priority: str = "normal"


@dataclass(frozen=True, slots=True)
class SubmitIntakeResult:
    case_id: str
    status: str
    priority: str


@dataclass(frozen=True, slots=True)
class AssignCaseCommand:
    case_id: str
    assignee_id: str


@dataclass(frozen=True, slots=True)
class AssignCaseResult:
    case_id: str
    status: str
    assignee_id: str | None


@dataclass(frozen=True, slots=True)
class TransitionCaseCommand:
    case_id: str
    to_status: str


@dataclass(frozen=True, slots=True)
class TransitionCaseResult:
    case_id: str
    status: str


@dataclass(frozen=True, slots=True)
class ReplyCommand:
    case_id: str
    body: str
    visibility: str = "requester"
    author_type: str = "agent"


@dataclass(frozen=True, slots=True)
class ReplyResult:
    case_id: str
    message_id: str
    visibility: str


@dataclass(frozen=True, slots=True)
class ViewCaseQuery:
    case_id: str
    elevated: bool = False


@dataclass(frozen=True, slots=True)
class ViewCaseResult:
    case_id: str
    minimized: bool
    view: dict[str, object]


@dataclass(frozen=True, slots=True)
class GrantAccessCommand:
    case_id: str
    staff_id: str
    reason: str
    ttl_seconds: int = 3600


@dataclass(frozen=True, slots=True)
class GrantAccessResult:
    grant_id: str
    case_id: str
    staff_id: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class RevokeAccessCommand:
    grant_id: str


@dataclass(frozen=True, slots=True)
class RevokeAccessResult:
    grant_id: str
    revoked: bool


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
        raise TenantScopeMissing()
    return scope


def _subject(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    actor = getattr(context, "actor", None)
    subject = getattr(actor, "id", None)
    if not subject:
        raise TenantScopeMissing()
    return str(subject)


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class SubmitIntake:
    """``support.intake`` — validate input, then open a governed, owned case (FR-SUP-001/002)."""

    def __init__(
        self, *, repository: SupportRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> SubmitIntakeResult:
        command = _typed(request, SubmitIntakeCommand)
        organization_id = _tenant(request)
        requester_id = _subject(request)
        # Deny-by-default: malformed/oversized/injection-shaped input is rejected here.
        content = validate_intake(
            subject=command.subject,
            category=command.category,
            body=command.body,
            priority=command.priority,
        )
        now = self._clock()
        case_id = self._id_factory()
        message = SupportMessage(
            message_id=self._id_factory(),
            author_type=AuthorType.REQUESTER,
            body_ref=f"msgbody:{case_id}:1",
            visibility=MessageVisibility.REQUESTER,
            created_at=now,
        )
        case = SupportCase(
            case_id=case_id,
            requester_id=requester_id,
            status=CaseStatus.NEW,
            priority=content.priority,
            category=content.category,
            created_at=now,
            audit_scope=f"support:{case_id}",
            subject=content.subject,
            organization_id=organization_id,
        )
        self._repo.add_case(organization_id=organization_id, case=case)
        self._repo.add_message(
            organization_id=organization_id,
            case_id=case_id,
            message=message,
            body=content.body,
        )
        return SubmitIntakeResult(
            case_id=case_id, status=case.status.value, priority=case.priority.value
        )


class AssignCase:
    """``support.case.assign`` — assign a case to staff (a NEW case becomes OPEN)."""

    def __init__(self, *, repository: SupportRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> AssignCaseResult:
        command = _typed(request, AssignCaseCommand)
        organization_id = _tenant(request)
        case = self._repo.get_case(organization_id=organization_id, case_id=command.case_id)
        if case is None:
            raise CaseNotFound(command.case_id)
        assigned = case.assigned(assignee_id=command.assignee_id, now=self._clock())
        self._repo.save_case(organization_id=organization_id, case=assigned)
        return AssignCaseResult(
            case_id=assigned.case_id,
            status=assigned.status.value,
            assignee_id=assigned.assignee_id,
        )


class TransitionCase:
    """``support.case.transition`` — move a case through its governed lifecycle (FR-SUP-002)."""

    def __init__(self, *, repository: SupportRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> TransitionCaseResult:
        command = _typed(request, TransitionCaseCommand)
        organization_id = _tenant(request)
        case = self._repo.get_case(organization_id=organization_id, case_id=command.case_id)
        if case is None:
            raise CaseNotFound(command.case_id)
        moved = case.transitioned(to_status=CaseStatus(command.to_status), now=self._clock())
        self._repo.save_case(organization_id=organization_id, case=moved)
        return TransitionCaseResult(case_id=moved.case_id, status=moved.status.value)


class ReplyToCase:
    """``support.case.reply`` — append a requester-facing or internal message to a case."""

    def __init__(
        self, *, repository: SupportRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> ReplyResult:
        command = _typed(request, ReplyCommand)
        organization_id = _tenant(request)
        case = self._repo.get_case(organization_id=organization_id, case_id=command.case_id)
        if case is None:
            raise CaseNotFound(command.case_id)
        # Reuse the same intake validation so an internal note can never smuggle injection markup.
        content = validate_intake(
            subject=case.subject or command.case_id,
            category=case.category,
            body=command.body,
            priority=case.priority.value,
        )
        now = self._clock()
        message = SupportMessage(
            message_id=self._id_factory(),
            author_type=AuthorType(command.author_type),
            body_ref=f"msgbody:{command.case_id}:{len(case.messages) + 1}",
            visibility=MessageVisibility(command.visibility),
            created_at=now,
        )
        self._repo.add_message(
            organization_id=organization_id,
            case_id=command.case_id,
            message=message,
            body=content.body,
        )
        return ReplyResult(
            case_id=command.case_id,
            message_id=message.message_id,
            visibility=message.visibility.value,
        )


class ViewCase:
    """``support.case.view`` — minimized by default; elevated read requires a grant (FR-SUP-003)."""

    def __init__(
        self, *, repository: SupportRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> ViewCaseResult:
        query = _typed(request, ViewCaseQuery)
        organization_id = _tenant(request)
        staff_id = _subject(request)
        case = self._repo.get_case(organization_id=organization_id, case_id=query.case_id)
        if case is None:
            raise CaseNotFound(query.case_id)
        now = self._clock()
        if not query.elevated:
            self._log(
                organization_id,
                query.case_id,
                staff_id,
                _SCOPE_MINIMIZED,
                _ACCESS_DECISION_GRANTED,
                now,
            )
            return ViewCaseResult(case_id=case.case_id, minimized=True, view=minimized_view(case))
        grant = self._repo.active_grant_for(
            organization_id=organization_id,
            case_id=query.case_id,
            staff_id=staff_id,
            now=now,
        )
        if grant is None:
            # Deny-by-default: an unauthorized broad read is refused AND logged (FR-SUP-003).
            self._log(
                organization_id, query.case_id, staff_id, _SCOPE_FULL, _ACCESS_DECISION_DENIED, now
            )
            raise SupportAccessDenied(staff_id, query.case_id)
        self._log(
            organization_id, query.case_id, staff_id, _SCOPE_FULL, _ACCESS_DECISION_GRANTED, now
        )
        return ViewCaseResult(case_id=case.case_id, minimized=False, view=elevated_view(case))

    def _log(
        self,
        organization_id: str,
        case_id: str,
        staff_id: str,
        scope: str,
        decision: str,
        now: datetime,
    ) -> None:
        self._repo.record_access(
            organization_id=organization_id,
            log_id=self._id_factory(),
            case_id=case_id,
            staff_id=staff_id,
            scope=scope,
            decision=decision,
            now=now,
        )


class GrantSupportAccess:
    """``support.access.grant`` — create an audited, time-bounded support-access grant."""

    def __init__(
        self, *, repository: SupportRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> GrantAccessResult:
        command = _typed(request, GrantAccessCommand)
        organization_id = _tenant(request)
        granted_by = _subject(request)
        now = self._clock()
        # Construction enforces the time-bound (expires_at > starts_at) and a non-empty reason.
        grant = SupportAccessGrant(
            grant_id=self._id_factory(),
            case_id=command.case_id,
            staff_id=command.staff_id,
            granted_by=granted_by,
            reason=command.reason,
            starts_at=now,
            expires_at=now + timedelta(seconds=max(1, command.ttl_seconds)),
        )
        self._repo.add_access_grant(organization_id=organization_id, grant=grant)
        self._repo.record_access(
            organization_id=organization_id,
            log_id=self._id_factory(),
            case_id=command.case_id,
            staff_id=command.staff_id,
            scope=_SCOPE_FULL,
            decision="grant_created",
            now=now,
        )
        return GrantAccessResult(
            grant_id=grant.grant_id,
            case_id=grant.case_id,
            staff_id=grant.staff_id,
            expires_at=grant.expires_at.isoformat(),
        )


class RevokeSupportAccess:
    """``support.access.revoke`` — revoke a support-access grant idempotently + auditable."""

    def __init__(
        self, *, repository: SupportRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> RevokeAccessResult:
        command = _typed(request, RevokeAccessCommand)
        organization_id = _tenant(request)
        grant = self._repo.get_grant(organization_id=organization_id, grant_id=command.grant_id)
        if grant is None or grant.revoked_at is not None:
            # Idempotent: revoking an unknown/already-revoked grant makes no change.
            return RevokeAccessResult(grant_id=command.grant_id, revoked=False)
        now = self._clock()
        revoked = grant.revoked(now=now)
        self._repo.save_access_grant(organization_id=organization_id, grant=revoked)
        self._repo.record_access(
            organization_id=organization_id,
            log_id=self._id_factory(),
            case_id=grant.case_id,
            staff_id=grant.staff_id,
            scope=_SCOPE_FULL,
            decision="grant_revoked",
            now=now,
        )
        return RevokeAccessResult(grant_id=command.grant_id, revoked=True)


__all__ = [
    "CAP_ACCESS_GRANT",
    "CAP_ACCESS_REVOKE",
    "CAP_ASSIGN",
    "CAP_INTAKE",
    "CAP_REPLY",
    "CAP_TRANSITION",
    "CAP_VERSION",
    "CAP_VIEW",
    "RES_SUPPORT_CASE",
    "SUPPORT_CAPABILITIES",
    "AssignCase",
    "AssignCaseCommand",
    "AssignCaseResult",
    "GrantAccessCommand",
    "GrantAccessResult",
    "GrantSupportAccess",
    "ReplyCommand",
    "ReplyResult",
    "ReplyToCase",
    "RevokeAccessCommand",
    "RevokeAccessResult",
    "RevokeSupportAccess",
    "SubmitIntake",
    "SubmitIntakeCommand",
    "SubmitIntakeResult",
    "TransitionCase",
    "TransitionCaseCommand",
    "TransitionCaseResult",
    "ViewCase",
    "ViewCaseQuery",
    "ViewCaseResult",
]
