"""Moderation case aggregate + deterministic lifecycle state machine (FR-ANN-007, EVAL-MOD-001).

A :class:`ModerationCase` is a report over a piece of reportable content (an annotation or a
comment) that moves through a **deterministic** lifecycle:

``reported -> triaged/assigned -> decided -> action-applied -> [appealed -> appeal-resolved]``

The transition table (:data:`_ALLOWED`) is the single source of truth; :func:`next_state` rejects
every illegal transition (EVAL-MOD-001). Enforcement produced by a decision is **reversible**: an
upheld removal/hide is restored when an appeal is granted, and the reversal is recorded on the
aggregate so the trail is auditable (FR-ANN-007, LAW-14). Pure and infrastructure-free (rule 10,
LAW-02): the only import is the kernel actor value object.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from northstar.kernel.context import Actor

from .errors import IllegalCaseTransition, ModerationInvariantViolation

RES_MODERATION_CASE = "moderation.case"

SCHEMA_VERSION = "1.0"


class CaseState(StrEnum):
    """The deterministic lifecycle state of a moderation case (EVAL-MOD-001)."""

    REPORTED = "reported"
    TRIAGED = "triaged"
    DECIDED = "decided"
    ACTION_APPLIED = "action_applied"
    APPEALED = "appealed"
    APPEAL_RESOLVED = "appeal_resolved"


class CaseTransition(StrEnum):
    """The trigger that advances a case from one state to the next (a pure state-machine edge)."""

    TRIAGE = "triage"
    ASSIGN = "assign"
    DECIDE = "decide"
    APPLY_ACTION = "apply_action"
    APPEAL = "appeal"
    RESOLVE_APPEAL = "resolve_appeal"


# The authoritative transition table: (from_state, transition) -> to_state. Any pair absent here is
# an ILLEGAL transition and is rejected (deny-by-default lifecycle, EVAL-MOD-001).
_ALLOWED: dict[tuple[CaseState, CaseTransition], CaseState] = {
    (CaseState.REPORTED, CaseTransition.TRIAGE): CaseState.TRIAGED,
    (CaseState.REPORTED, CaseTransition.ASSIGN): CaseState.TRIAGED,
    (CaseState.TRIAGED, CaseTransition.ASSIGN): CaseState.TRIAGED,
    (CaseState.TRIAGED, CaseTransition.DECIDE): CaseState.DECIDED,
    (CaseState.DECIDED, CaseTransition.APPLY_ACTION): CaseState.ACTION_APPLIED,
    (CaseState.ACTION_APPLIED, CaseTransition.APPEAL): CaseState.APPEALED,
    (CaseState.APPEALED, CaseTransition.RESOLVE_APPEAL): CaseState.APPEAL_RESOLVED,
}

# A case is "open" (still coalesces duplicate reports) until an appeal is resolved.
_TERMINAL_STATES: frozenset[CaseState] = frozenset({CaseState.APPEAL_RESOLVED})


def next_state(current: CaseState, transition: CaseTransition) -> CaseState:
    """Return the state reached by applying ``transition`` to ``current`` (pure).

    Raises :class:`IllegalCaseTransition` for any transition not in the authoritative table, so an
    out-of-order lifecycle (e.g. deciding before triage, appealing before action) is rejected.
    """
    try:
        return _ALLOWED[(current, transition)]
    except KeyError as exc:
        raise IllegalCaseTransition(from_state=current.value, transition=transition.value) from exc


def can_transition(current: CaseState, transition: CaseTransition) -> bool:
    """True when ``transition`` is legal from ``current`` (non-raising probe)."""
    return (current, transition) in _ALLOWED


class Disposition(StrEnum):
    """A moderator's decision on a case (the five canonical dispositions, FR-ANN-007)."""

    UPHOLD = "uphold"
    REMOVE = "remove"
    HIDE = "hide"
    WARN = "warn"
    DISMISS = "dismiss"


class EnforcementKind(StrEnum):
    """The content-level enforcement a disposition produces (``none`` = no content mutation)."""

    REMOVE = "remove"
    HIDE = "hide"
    WARN = "warn"
    NONE = "none"


# Only REMOVE/HIDE mutate the reportable content and are therefore reversible on a granted appeal.
_ENFORCEMENT_FOR_DISPOSITION: dict[Disposition, EnforcementKind] = {
    Disposition.REMOVE: EnforcementKind.REMOVE,
    Disposition.HIDE: EnforcementKind.HIDE,
    Disposition.WARN: EnforcementKind.WARN,
    Disposition.UPHOLD: EnforcementKind.NONE,
    Disposition.DISMISS: EnforcementKind.NONE,
}

_REVERSIBLE_KINDS: frozenset[EnforcementKind] = frozenset(
    {EnforcementKind.REMOVE, EnforcementKind.HIDE}
)


def enforcement_for(disposition: Disposition) -> EnforcementKind:
    """The enforcement kind a disposition applies (pure mapping)."""
    return _ENFORCEMENT_FOR_DISPOSITION[disposition]


class AppealResolution(StrEnum):
    """The outcome of an appeal review."""

    GRANTED = "granted"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class ReportableRef:
    """A tenant-scoped reference to reportable content (an annotation or comment) + its author.

    ``author_id`` is the affected content author — the only actor (or their delegate) permitted to
    appeal a decision against this content (deny-by-default authorization).
    """

    content_type: str
    content_id: str
    author_id: str

    def __post_init__(self) -> None:
        if not self.content_type or not self.content_id:
            raise ModerationInvariantViolation(
                "a reportable reference requires a content type and id",
                code="moderation.target.invalid",
            )


@dataclass(frozen=True, slots=True)
class Report:
    """One report against a target (evidence). Duplicate reports coalesce into one case."""

    report_id: str
    reporter_id: str
    reason: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Decision:
    """A moderator's recorded decision: disposition + rationale + who/when (auditable)."""

    disposition: Disposition
    rationale: str
    decided_by: Actor
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class EnforcementAction:
    """The recorded, reversible enforcement applied for a decision (FR-ANN-007, LAW-14)."""

    kind: EnforcementKind
    applied: bool
    applied_by: Actor
    applied_at: datetime
    receipt: str | None = None
    reversed: bool = False
    reversed_by: Actor | None = None
    reversed_at: datetime | None = None

    @property
    def is_reversible(self) -> bool:
        return self.kind in _REVERSIBLE_KINDS

    def reverse(self, *, actor: Actor, at: datetime) -> EnforcementAction:
        """Return a reversed copy (restore an upheld removal/hide on a granted appeal)."""
        return replace(self, reversed=True, reversed_by=actor, reversed_at=at)


@dataclass(frozen=True, slots=True)
class Appeal:
    """An appeal by the affected author (or delegate) and its optional resolution."""

    appeal_id: str
    appellant_id: str
    rationale: str
    created_at: datetime
    resolution: AppealResolution | None = None
    resolved_by: Actor | None = None
    resolved_at: datetime | None = None
    resolution_rationale: str | None = None


@dataclass(frozen=True, slots=True)
class CaseEvent:
    """An append-only, tamper-evident lifecycle event (the auditable transition trail, LAW-14)."""

    event_id: str
    case_id: str
    organization_id: str
    action: str
    from_state: CaseState | None
    to_state: CaseState
    actor: Actor
    created_at: datetime
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class ModerationCase:
    """The moderation case aggregate root (deterministic lifecycle over reportable content)."""

    case_id: str
    organization_id: str
    target: ReportableRef
    state: CaseState
    reports: tuple[Report, ...]
    created_at: datetime
    updated_at: datetime
    assignee_id: str | None = None
    decision: Decision | None = None
    enforcement: EnforcementAction | None = None
    appeal: Appeal | None = None

    # -- lifecycle transitions (each returns a new aggregate; illegal edges raise) --------------

    def with_report(self, report: Report, *, at: datetime) -> ModerationCase:
        """Coalesce another report into this open case (state unchanged, FR-ANN-007)."""
        if self.state in _TERMINAL_STATES:
            raise ModerationInvariantViolation(
                "a resolved case cannot accept further reports", code="moderation.case.closed"
            )
        return replace(self, reports=(*self.reports, report), updated_at=at)

    def triaged(self, *, at: datetime) -> ModerationCase:
        return replace(self, state=next_state(self.state, CaseTransition.TRIAGE), updated_at=at)

    def assigned(self, *, assignee_id: str, at: datetime) -> ModerationCase:
        state = next_state(self.state, CaseTransition.ASSIGN)
        return replace(self, state=state, assignee_id=assignee_id, updated_at=at)

    def decided(self, decision: Decision, *, at: datetime) -> ModerationCase:
        state = next_state(self.state, CaseTransition.DECIDE)
        return replace(self, state=state, decision=decision, updated_at=at)

    def action_applied(self, enforcement: EnforcementAction, *, at: datetime) -> ModerationCase:
        if self.decision is None:
            raise ModerationInvariantViolation(
                "an action requires a recorded decision", code="moderation.action.no_decision"
            )
        state = next_state(self.state, CaseTransition.APPLY_ACTION)
        return replace(self, state=state, enforcement=enforcement, updated_at=at)

    def appealed(self, appeal: Appeal, *, at: datetime) -> ModerationCase:
        state = next_state(self.state, CaseTransition.APPEAL)
        return replace(self, state=state, appeal=appeal, updated_at=at)

    def appeal_resolved(
        self,
        *,
        resolution: AppealResolution,
        resolved_by: Actor,
        at: datetime,
        rationale: str | None = None,
    ) -> tuple[ModerationCase, bool]:
        """Resolve the appeal; on a GRANT restore a reversible enforcement (FR-ANN-007).

        Returns the updated case and a flag indicating whether an enforcement was restored, so the
        caller can drive the reversible-enforcement side effect through its port and audit it.
        """
        if self.appeal is None:
            raise ModerationInvariantViolation(
                "no appeal exists to resolve", code="moderation.appeal.absent"
            )
        state = next_state(self.state, CaseTransition.RESOLVE_APPEAL)
        restored = False
        enforcement = self.enforcement
        if (
            resolution is AppealResolution.GRANTED
            and enforcement is not None
            and enforcement.applied
            and enforcement.is_reversible
            and not enforcement.reversed
        ):
            enforcement = enforcement.reverse(actor=resolved_by, at=at)
            restored = True
        appeal = replace(
            self.appeal,
            resolution=resolution,
            resolved_by=resolved_by,
            resolved_at=at,
            resolution_rationale=rationale,
        )
        updated = replace(self, state=state, appeal=appeal, enforcement=enforcement, updated_at=at)
        return updated, restored

    @property
    def report_count(self) -> int:
        return len(self.reports)


@dataclass(frozen=True, slots=True)
class ModeratorContext:
    """A resolved authorization context: whether the actor may moderate this case."""

    is_moderator: bool = False
    is_assignee: bool = False

    @property
    def may_moderate(self) -> bool:
        return self.is_moderator or self.is_assignee
