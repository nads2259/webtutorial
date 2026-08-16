"""Governance domain: immutable decision records + a time-bounded control-exception engine.

Two pure aggregates, no infrastructure (rule 10, LAW-02 — the only import is the kernel actor
value object):

* :class:`DecisionRecord` (EVAL-GOV-001, FR-GOV-001) — an architectural/security/privacy/legal/AI
  or release decision with an explicit ``decider``, ``rationale``, ``timestamp`` and links to the
  affected controls/requirements/gates it governs. A recorded decision is **immutable**: it exposes
  no mutator, and a *correction* is expressed by :meth:`DecisionRecord.supersede`, which returns a
  brand-new record linked back to the prior via ``supersedes`` — the prior is never mutated
  (LAW-07: published revisions are immutable; corrections create new revisions).

* :class:`ControlException` (EVAL-GOV-002, FR-GOV-002) — a scoped, approved, time-bounded waiver of
  a specific control/gate for a subject. It **cannot be constructed without both an approver and an
  explicit expiry**. It AUTO-EXPIRES under an injectable clock: :meth:`ControlException.is_active`
  is ``False`` once ``now > expiry`` (or once revoked), so an expired exception is never honored.

The two pure evaluations at the bottom (:func:`evaluate_exception`, :func:`no_expired_exception`)
are the deterministic, clock-driven checks a release gate uses: a gate's ``no_expired_exception``
machine-check returns ``True`` for a control only when a non-expired, approved exception exists for
it (matching ``spec/evaluations/release-gates.yaml``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from northstar.kernel.context import Actor

from .errors import GovernanceInvariantViolation

RES_GOVERNANCE_DECISION = "governance.decision"
RES_GOVERNANCE_EXCEPTION = "governance.exception"

SCHEMA_VERSION = "1.0"


class DecisionStatus(StrEnum):
    """The lifecycle status of a governance decision record (docs/21 §1 ADR status vocabulary)."""

    PROPOSED = "proposed"
    ACCEPTED_BASELINE = "accepted_baseline"
    ACCEPTED_FINAL = "accepted_final"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ExceptionStatus(StrEnum):
    """The lifecycle status of a control exception (independent of clock-driven expiry)."""

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class DecisionLinks:
    """Traceability links from a decision to what it governs (FR-GOV-001).

    A decision must point at *something* it affects — at least one control, requirement or gate —
    so the decision trace is never a dangling record. Each collection is a de-duplicated, ordered
    tuple of stable identifiers (e.g. ``FR-GOV-002``, ``EVAL-GOV-002``, ``GATE-GOVERNANCE``).
    """

    controls: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (self.controls or self.requirements or self.gates):
            raise GovernanceInvariantViolation(
                "a decision must link to at least one control, requirement or gate",
                code="governance.decision.links_required",
            )

    @property
    def is_empty(self) -> bool:
        return not (self.controls or self.requirements or self.gates)

    @classmethod
    def of(
        cls,
        *,
        controls: Iterable[str] = (),
        requirements: Iterable[str] = (),
        gates: Iterable[str] = (),
    ) -> DecisionLinks:
        """Build links from any iterables, de-duplicating while preserving first-seen order."""
        return cls(
            controls=_dedupe(controls),
            requirements=_dedupe(requirements),
            gates=_dedupe(gates),
        )


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        if value:
            seen.setdefault(value, None)
    return tuple(seen)


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """An immutable, traceable governance decision record (EVAL-GOV-001, FR-GOV-001).

    Frozen and mutator-free: once recorded it never changes. A correction/replacement is a NEW
    record produced by :meth:`supersede`, linked to the prior through ``supersedes`` — the prior
    stays byte-for-byte intact and discoverable (LAW-07).
    """

    decision_id: str
    organization_id: str
    title: str
    status: DecisionStatus
    rationale: str
    decider: Actor
    recorded_at: datetime
    links: DecisionLinks
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not self.decision_id:
            raise GovernanceInvariantViolation(
                "a decision requires an id", code="governance.decision.id_required"
            )
        if not self.title:
            raise GovernanceInvariantViolation(
                "a decision requires a title", code="governance.decision.title_required"
            )
        if not self.rationale:
            raise GovernanceInvariantViolation(
                "a decision requires a rationale (auditable)",
                code="governance.decision.rationale_required",
            )

    def supersede(
        self,
        *,
        decision_id: str,
        title: str,
        rationale: str,
        decider: Actor,
        recorded_at: datetime,
        links: DecisionLinks,
        status: DecisionStatus = DecisionStatus.ACCEPTED_BASELINE,
    ) -> DecisionRecord:
        """Return a NEW decision record that supersedes this one (never mutates ``self``).

        The new record carries ``supersedes=self.decision_id`` so the trace from the replacement
        back to the prior is explicit. ``self`` is returned unchanged to the caller's reference —
        immutability is guaranteed by the frozen dataclass, so the prior remains authoritative and
        discoverable (EVAL-GOV-001).
        """
        if decision_id == self.decision_id:
            raise GovernanceInvariantViolation(
                "a superseding decision must have a new id",
                code="governance.decision.supersede_same_id",
            )
        return DecisionRecord(
            decision_id=decision_id,
            organization_id=self.organization_id,
            title=title,
            status=status,
            rationale=rationale,
            decider=decider,
            recorded_at=recorded_at,
            links=links,
            supersedes=self.decision_id,
        )


@dataclass(frozen=True, slots=True)
class ControlException:
    """A scoped, approved, time-bounded control exception that AUTO-EXPIRES (FR-GOV-002).

    Scoped to a specific ``control`` (a control/gate id) and a ``subject``; it REQUIRES an
    ``approver`` and an explicit ``expiry`` — construction fails without both. It is honored only
    while :meth:`is_active` is ``True``: active status, an approver present, and ``now <= expiry``.
    """

    exception_id: str
    organization_id: str
    control: str
    subject: str
    approver: Actor
    granted_by: Actor
    rationale: str
    expiry: datetime
    granted_at: datetime
    status: ExceptionStatus = ExceptionStatus.ACTIVE
    revoked_by: Actor | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.exception_id:
            raise GovernanceInvariantViolation(
                "a control exception requires an id", code="governance.exception.id_required"
            )
        if not self.control:
            raise GovernanceInvariantViolation(
                "a control exception must be scoped to a specific control/gate",
                code="governance.exception.control_required",
            )
        if not self.subject:
            raise GovernanceInvariantViolation(
                "a control exception must be scoped to a subject",
                code="governance.exception.subject_required",
            )
        if self.approver is None:  # type: ignore[redundant-expr] defensive: approver is mandatory
            raise GovernanceInvariantViolation(
                "a control exception requires an approver",
                code="governance.exception.approver_required",
            )
        if self.expiry is None:  # type: ignore[redundant-expr] defensive: expiry is mandatory
            raise GovernanceInvariantViolation(
                "a control exception requires an explicit expiry",
                code="governance.exception.expiry_required",
            )
        if self.expiry <= self.granted_at:
            raise GovernanceInvariantViolation(
                "a control exception expiry must be after it was granted",
                code="governance.exception.expiry_not_in_future",
            )

    def is_active(self, now: datetime) -> bool:
        """True only while this exception may be honored (approved, not revoked, not expired).

        Auto-expiry crux (EVAL-GOV-002): once ``now > expiry`` this returns ``False`` under the
        injected clock, so an expired exception is never honored.
        """
        if self.status is not ExceptionStatus.ACTIVE:
            return False
        return now <= self.expiry

    def is_expired(self, now: datetime) -> bool:
        """True once the wall clock has passed the expiry (independent of revocation)."""
        return now > self.expiry

    def revoke(self, *, actor: Actor, at: datetime) -> ControlException:
        """Return a revoked copy (a revoked exception is no longer honored, EVAL-GOV-002)."""
        return replace(self, status=ExceptionStatus.REVOKED, revoked_by=actor, revoked_at=at)


# ---------------------------------------------------------------------------
# Pure gate-style evaluations (deterministic, clock-driven — EVAL-GOV-002)
# ---------------------------------------------------------------------------


def evaluate_exception(exception: ControlException, now: datetime) -> bool:
    """Return whether a single exception is honored at ``now`` (approved + non-expired + active).

    Pure alias of :meth:`ControlException.is_active` for the application/evaluation layer.
    """
    return exception.is_active(now)


def no_expired_exception(
    control: str, now: datetime, exceptions: Sequence[ControlException]
) -> bool:
    """Gate-style check: ``True`` only if a non-expired, approved exception exists for ``control``.

    Matches the ``no_expired_exception`` term in every gate's ``machine_check``
    (``spec/evaluations/release-gates.yaml``): a release may only rely on a control exception that
    is still live. If the only exceptions scoped to ``control`` are expired or revoked (or there are
    none), this returns ``False`` — the gate must not honor an expired exception (EVAL-GOV-002).
    """
    return any(
        exception.control == control and exception.is_active(now) for exception in exceptions
    )


@dataclass(frozen=True, slots=True)
class ApproverContext:
    """A resolved authorization context: whether the actor may grant/revoke exceptions."""

    is_approver: bool = False
    approver_controls: tuple[str, ...] = field(default=())

    @property
    def may_approve(self) -> bool:
        return self.is_approver
