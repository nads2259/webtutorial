"""Moderation capabilities: one authoritative implementation per action (LAW-04).

Each handler runs through the kernel command/query bus, so every mutation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope is taken from the authenticated
:class:`RequestContext` (``tenant_scope``), **never** from the payload (rule 50). On top of the
capability-layer grant, each transition enforces a domain authorization rule:

* only a **moderator** (or the case **assignee**) may triage, assign, decide, apply an action or
  resolve an appeal;
* only the **affected author** (or their delegate) may submit an appeal.

An unauthorized transition raises :class:`PolicyDenied` (deny-by-default), which the bus records as
a failed, audited outcome and the API surfaces as ``403``. The reportable content is referenced
through :class:`ReportableContentPort` (never its tables, LAW-13); enforcement is reversible through
:class:`EnforcementPort` — a granted appeal restores an upheld removal/hide.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from northstar.kernel.context import Actor
from northstar.kernel.errors import PolicyDenied

from ..domain.errors import (
    CaseNotFound,
    ModerationInvariantViolation,
    ReportableContentNotFound,
    TenantScopeMissing,
)
from ..domain.model import (
    Appeal,
    AppealResolution,
    CaseEvent,
    CaseState,
    CaseTransition,
    Decision,
    Disposition,
    EnforcementAction,
    EnforcementKind,
    ModerationCase,
    Report,
    ReportableRef,
    enforcement_for,
    next_state,
)
from .ports import (
    EnforcementPort,
    ModerationRepositoryPort,
    ModeratorDirectoryPort,
    ReportableContentPort,
)

CAP_VERSION = "1.0.0"

CAP_SUBMIT_REPORT = "moderation.report.submit"
CAP_TRIAGE = "moderation.case.triage"
CAP_ASSIGN = "moderation.case.assign"
CAP_DECIDE = "moderation.case.decide"
CAP_APPLY_ACTION = "moderation.action.apply"
CAP_SUBMIT_APPEAL = "moderation.appeal.submit"
CAP_RESOLVE_APPEAL = "moderation.appeal.resolve"
CAP_GET_CASE = "moderation.case.get"

MODERATION_CAPABILITIES: tuple[str, ...] = (
    CAP_SUBMIT_REPORT,
    CAP_TRIAGE,
    CAP_ASSIGN,
    CAP_DECIDE,
    CAP_APPLY_ACTION,
    CAP_SUBMIT_APPEAL,
    CAP_RESOLVE_APPEAL,
    CAP_GET_CASE,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubmitReportCommand:
    content_type: str
    content_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class SubmitReportResult:
    case_id: str
    state: str
    coalesced: bool
    report_count: int


@dataclass(frozen=True, slots=True)
class TriageCommand:
    case_id: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class AssignCommand:
    case_id: str
    assignee_id: str


@dataclass(frozen=True, slots=True)
class DecideCommand:
    case_id: str
    disposition: str
    rationale: str


@dataclass(frozen=True, slots=True)
class ApplyActionCommand:
    case_id: str


@dataclass(frozen=True, slots=True)
class SubmitAppealCommand:
    case_id: str
    rationale: str


@dataclass(frozen=True, slots=True)
class ResolveAppealCommand:
    case_id: str
    resolution: str
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class GetCaseQuery:
    case_id: str


@dataclass(frozen=True, slots=True)
class CaseTransitionResult:
    case_id: str
    state: str


@dataclass(frozen=True, slots=True)
class DecisionResult:
    case_id: str
    state: str
    disposition: str
    enforcement_kind: str


@dataclass(frozen=True, slots=True)
class ApplyActionResult:
    case_id: str
    state: str
    enforcement_kind: str
    applied: bool


@dataclass(frozen=True, slots=True)
class ResolveAppealResult:
    case_id: str
    state: str
    resolution: str
    enforcement_restored: bool


@dataclass(frozen=True, slots=True)
class CaseView:
    case_id: str
    state: str
    content_type: str
    content_id: str
    author_id: str
    assignee_id: str | None
    report_count: int
    disposition: str | None
    enforcement_kind: str | None
    enforcement_reversed: bool
    appeal_resolution: str | None


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


def _enum[EnumT](enum_type: type[EnumT], value: str, code: str) -> EnumT:
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except ValueError as exc:
        raise ModerationInvariantViolation(
            f"invalid {enum_type.__name__} {value!r}", code=code
        ) from exc


def _load(repo: ModerationRepositoryPort, *, organization_id: str, case_id: str) -> ModerationCase:
    case = repo.get_case(organization_id=organization_id, case_id=case_id)
    if case is None:
        raise CaseNotFound()
    return case


def _deny(action: str, code: str, message: str) -> PolicyDenied:
    return PolicyDenied(action=action, decision_id="moderation-authz", reasons=((code, message),))


def _require_moderator(
    moderators: ModeratorDirectoryPort,
    *,
    action: str,
    organization_id: str,
    actor: Actor,
    case: ModerationCase | None = None,
) -> None:
    """Deny-by-default: allow only a moderator, or the assignee of ``case`` if provided."""
    if moderators.is_moderator(organization_id=organization_id, actor_id=actor.id):
        return
    if case is not None and case.assignee_id is not None and case.assignee_id == actor.id:
        return
    raise _deny(
        action,
        "MODERATION_NOT_A_MODERATOR",
        "only a moderator or the case assignee may perform this action",
    )


def _require_author(action: str, *, case: ModerationCase, actor: Actor) -> None:
    """Deny-by-default: allow only the affected content author, or their delegate, to appeal."""
    author_id = case.target.author_id
    if actor.id == author_id:
        return
    if actor.delegated_by is not None and actor.delegated_by == author_id:
        return
    raise _deny(
        action,
        "MODERATION_NOT_THE_AUTHOR",
        "only the affected author or their delegate may appeal this decision",
    )


def _event(
    *,
    id_factory: IdFactory,
    case: ModerationCase,
    action: str,
    from_state: CaseState | None,
    actor: Actor,
    at: datetime,
    rationale: str | None = None,
) -> CaseEvent:
    return CaseEvent(
        event_id=id_factory(),
        case_id=case.case_id,
        organization_id=case.organization_id,
        action=action,
        from_state=from_state,
        to_state=case.state,
        actor=actor,
        created_at=at,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class SubmitReport:
    """``moderation.report.submit`` — report content; duplicates coalesce (FR-ANN-007)."""

    def __init__(
        self,
        *,
        repository: ModerationRepositoryPort,
        reportable: ReportableContentPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._reportable = reportable
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> SubmitReportResult:
        command = _typed(request, SubmitReportCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        content = self._reportable.describe(
            organization_id=organization_id,
            content_type=command.content_type,
            content_id=command.content_id,
        )
        if content is None:
            raise ReportableContentNotFound()
        now = self._clock()
        report = Report(
            report_id=self._id_factory(),
            reporter_id=actor.id,
            reason=command.reason,
            created_at=now,
        )
        existing = self._repo.find_open_case_for_target(
            organization_id=organization_id,
            content_type=command.content_type,
            content_id=command.content_id,
        )
        if existing is not None:
            updated = existing.with_report(report, at=now)
            event = _event(
                id_factory=self._id_factory,
                case=updated,
                action=CAP_SUBMIT_REPORT,
                from_state=existing.state,
                actor=actor,
                at=now,
                rationale=command.reason,
            )
            self._repo.update_case(updated, event)
            return SubmitReportResult(
                case_id=updated.case_id,
                state=updated.state.value,
                coalesced=True,
                report_count=updated.report_count,
            )
        target = ReportableRef(
            content_type=command.content_type,
            content_id=command.content_id,
            author_id=content.author_id,
        )
        case = ModerationCase(
            case_id=self._id_factory(),
            organization_id=organization_id,
            target=target,
            state=CaseState.REPORTED,
            reports=(report,),
            created_at=now,
            updated_at=now,
        )
        event = _event(
            id_factory=self._id_factory,
            case=case,
            action=CAP_SUBMIT_REPORT,
            from_state=None,
            actor=actor,
            at=now,
            rationale=command.reason,
        )
        self._repo.add_case(case, event)
        return SubmitReportResult(
            case_id=case.case_id,
            state=case.state.value,
            coalesced=False,
            report_count=case.report_count,
        )


class TriageCase:
    """``moderation.case.triage`` — a moderator triages a reported case."""

    def __init__(
        self,
        *,
        repository: ModerationRepositoryPort,
        moderators: ModeratorDirectoryPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._moderators = moderators
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> CaseTransitionResult:
        command = _typed(request, TriageCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        case = _load(self._repo, organization_id=organization_id, case_id=command.case_id)
        _require_moderator(
            self._moderators,
            action=CAP_TRIAGE,
            organization_id=organization_id,
            actor=actor,
            case=case,
        )
        now = self._clock()
        updated = case.triaged(at=now)
        self._repo.update_case(
            updated,
            _event(
                id_factory=self._id_factory,
                case=updated,
                action=CAP_TRIAGE,
                from_state=case.state,
                actor=actor,
                at=now,
                rationale=command.note,
            ),
        )
        return CaseTransitionResult(case_id=updated.case_id, state=updated.state.value)


class AssignCase:
    """``moderation.case.assign`` — a moderator assigns a case to an owner (moderator/assignee)."""

    def __init__(
        self,
        *,
        repository: ModerationRepositoryPort,
        moderators: ModeratorDirectoryPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._moderators = moderators
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> CaseTransitionResult:
        command = _typed(request, AssignCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        case = _load(self._repo, organization_id=organization_id, case_id=command.case_id)
        _require_moderator(
            self._moderators,
            action=CAP_ASSIGN,
            organization_id=organization_id,
            actor=actor,
            case=case,
        )
        if not command.assignee_id:
            raise ModerationInvariantViolation(
                "an assignment requires an assignee", code="moderation.assign.missing"
            )
        now = self._clock()
        updated = case.assigned(assignee_id=command.assignee_id, at=now)
        self._repo.update_case(
            updated,
            _event(
                id_factory=self._id_factory,
                case=updated,
                action=CAP_ASSIGN,
                from_state=case.state,
                actor=actor,
                at=now,
                rationale=f"assigned to {command.assignee_id}",
            ),
        )
        return CaseTransitionResult(case_id=updated.case_id, state=updated.state.value)


class DecideCase:
    """``moderation.case.decide`` — a moderator/assignee records a disposition + rationale."""

    def __init__(
        self,
        *,
        repository: ModerationRepositoryPort,
        moderators: ModeratorDirectoryPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._moderators = moderators
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> DecisionResult:
        command = _typed(request, DecideCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        case = _load(self._repo, organization_id=organization_id, case_id=command.case_id)
        _require_moderator(
            self._moderators,
            action=CAP_DECIDE,
            organization_id=organization_id,
            actor=actor,
            case=case,
        )
        disposition = _enum(Disposition, command.disposition, "moderation.disposition")
        if not command.rationale:
            raise ModerationInvariantViolation(
                "a decision requires a rationale (auditable)", code="moderation.decision.rationale"
            )
        now = self._clock()
        decision = Decision(
            disposition=disposition,
            rationale=command.rationale,
            decided_by=actor,
            decided_at=now,
        )
        updated = case.decided(decision, at=now)
        self._repo.update_case(
            updated,
            _event(
                id_factory=self._id_factory,
                case=updated,
                action=CAP_DECIDE,
                from_state=case.state,
                actor=actor,
                at=now,
                rationale=command.rationale,
            ),
        )
        return DecisionResult(
            case_id=updated.case_id,
            state=updated.state.value,
            disposition=disposition.value,
            enforcement_kind=enforcement_for(disposition).value,
        )


class ApplyAction:
    """``moderation.action.apply`` — apply the (reversible) enforcement a decision produced."""

    def __init__(
        self,
        *,
        repository: ModerationRepositoryPort,
        enforcement: EnforcementPort,
        moderators: ModeratorDirectoryPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._enforcement = enforcement
        self._moderators = moderators
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> ApplyActionResult:
        command = _typed(request, ApplyActionCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        case = _load(self._repo, organization_id=organization_id, case_id=command.case_id)
        _require_moderator(
            self._moderators,
            action=CAP_APPLY_ACTION,
            organization_id=organization_id,
            actor=actor,
            case=case,
        )
        if case.decision is None:
            # Surface the precise deterministic-lifecycle error first (e.g. applying before a
            # decision from the triaged state is an illegal transition, EVAL-MOD-001).
            next_state(case.state, CaseTransition.APPLY_ACTION)
            raise ModerationInvariantViolation(
                "an action requires a recorded decision", code="moderation.action.no_decision"
            )
        kind = enforcement_for(case.decision.disposition)
        now = self._clock()
        receipt: str | None = None
        if kind in (EnforcementKind.REMOVE, EnforcementKind.HIDE):
            receipt = self._enforcement.apply(
                organization_id=organization_id,
                target=case.target,
                kind=kind,
                actor_id=actor.id,
            )
        enforcement = EnforcementAction(
            kind=kind,
            applied=True,
            applied_by=actor,
            applied_at=now,
            receipt=receipt,
        )
        updated = case.action_applied(enforcement, at=now)
        self._repo.update_case(
            updated,
            _event(
                id_factory=self._id_factory,
                case=updated,
                action=CAP_APPLY_ACTION,
                from_state=case.state,
                actor=actor,
                at=now,
                rationale=f"enforcement {kind.value} applied",
            ),
        )
        return ApplyActionResult(
            case_id=updated.case_id,
            state=updated.state.value,
            enforcement_kind=kind.value,
            applied=True,
        )


class SubmitAppeal:
    """``moderation.appeal.submit`` — the affected author (or delegate) appeals a decision."""

    def __init__(
        self,
        *,
        repository: ModerationRepositoryPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> CaseTransitionResult:
        command = _typed(request, SubmitAppealCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        case = _load(self._repo, organization_id=organization_id, case_id=command.case_id)
        _require_author(CAP_SUBMIT_APPEAL, case=case, actor=actor)
        if not command.rationale:
            raise ModerationInvariantViolation(
                "an appeal requires a rationale", code="moderation.appeal.rationale"
            )
        now = self._clock()
        appeal = Appeal(
            appeal_id=self._id_factory(),
            appellant_id=actor.id,
            rationale=command.rationale,
            created_at=now,
        )
        updated = case.appealed(appeal, at=now)
        self._repo.update_case(
            updated,
            _event(
                id_factory=self._id_factory,
                case=updated,
                action=CAP_SUBMIT_APPEAL,
                from_state=case.state,
                actor=actor,
                at=now,
                rationale=command.rationale,
            ),
        )
        return CaseTransitionResult(case_id=updated.case_id, state=updated.state.value)


class ResolveAppeal:
    """``moderation.appeal.resolve`` — a moderator grants/denies an appeal (restores on grant)."""

    def __init__(
        self,
        *,
        repository: ModerationRepositoryPort,
        enforcement: EnforcementPort,
        moderators: ModeratorDirectoryPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._enforcement = enforcement
        self._moderators = moderators
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> ResolveAppealResult:
        command = _typed(request, ResolveAppealCommand)
        organization_id = _tenant(request)
        actor = _actor(request)
        case = _load(self._repo, organization_id=organization_id, case_id=command.case_id)
        _require_moderator(
            self._moderators,
            action=CAP_RESOLVE_APPEAL,
            organization_id=organization_id,
            actor=actor,
            case=case,
        )
        resolution = _enum(AppealResolution, command.resolution, "moderation.appeal.resolution")
        now = self._clock()
        updated, restored = case.appeal_resolved(
            resolution=resolution,
            resolved_by=actor,
            at=now,
            rationale=command.rationale,
        )
        # Reverse the content-level enforcement first, so persistence only records a state we could
        # actually enforce (an upheld removal is restored on a granted appeal, FR-ANN-007).
        if restored and case.enforcement is not None:
            self._enforcement.restore(
                organization_id=organization_id,
                target=case.target,
                kind=case.enforcement.kind,
                actor_id=actor.id,
                receipt=case.enforcement.receipt,
            )
        self._repo.update_case(
            updated,
            _event(
                id_factory=self._id_factory,
                case=updated,
                action=CAP_RESOLVE_APPEAL,
                from_state=case.state,
                actor=actor,
                at=now,
                rationale=(
                    f"appeal {resolution.value}" + (" — enforcement restored" if restored else "")
                ),
            ),
        )
        return ResolveAppealResult(
            case_id=updated.case_id,
            state=updated.state.value,
            resolution=resolution.value,
            enforcement_restored=restored,
        )


class GetCase:
    """``moderation.case.get`` (query) — read a single case, tenant-scoped."""

    def __init__(self, *, repository: ModerationRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> CaseView:
        query = _typed(request, GetCaseQuery)
        organization_id = _tenant(request)
        case = _load(self._repo, organization_id=organization_id, case_id=query.case_id)
        return _view(case)


def _view(case: ModerationCase) -> CaseView:
    return CaseView(
        case_id=case.case_id,
        state=case.state.value,
        content_type=case.target.content_type,
        content_id=case.target.content_id,
        author_id=case.target.author_id,
        assignee_id=case.assignee_id,
        report_count=case.report_count,
        disposition=case.decision.disposition.value if case.decision else None,
        enforcement_kind=case.enforcement.kind.value if case.enforcement else None,
        enforcement_reversed=bool(case.enforcement and case.enforcement.reversed),
        appeal_resolution=(
            case.appeal.resolution.value if case.appeal and case.appeal.resolution else None
        ),
    )
