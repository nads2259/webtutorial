"""Peer-review journey: a deterministic, audited state machine (EVAL-RSH-002, docs/37 §5).

Pure and infrastructure-free (rule 10). A :class:`DocumentReview` moves through a fixed set of
states via :func:`next_status`, which is a total function over the legal ``(status, action)`` pairs
and raises :class:`IllegalReviewTransition` for anything else — so the machine is deterministic and
illegal transitions are impossible to persist. Authorization is deny-by-default (LAW-19/rule 50):
only an assigned reviewer may drive a review action; only an author may submit or resubmit
(:func:`authorize_actor`). Each applied transition is recorded as an immutable :class:`ReviewEvent`
so every transition leaves a tamper-evident audit trail (LAW-14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .errors import (
    IllegalReviewTransition,
    ResearchInvariantViolation,
    ReviewAuthorizationDenied,
)


def _require(condition: bool, message: str, code: str = "research.review.invalid") -> None:
    if not condition:
        raise ResearchInvariantViolation(message, code=code)


class ReviewStatus(StrEnum):
    """Peer-review lifecycle states (docs/37 §5)."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    REVISIONS_REQUESTED = "revisions_requested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ReviewAction(StrEnum):
    """The transitions an actor can request on a review."""

    SUBMIT = "submit"
    START_REVIEW = "start_review"
    REQUEST_REVISIONS = "request_revisions"
    RESUBMIT = "resubmit"
    ACCEPT = "accept"
    REJECT = "reject"


# The single authoritative transition table (deterministic): submit -> under-review ->
# revisions-requested -> resubmit -> under-review -> accept/reject. ACCEPTED/REJECTED are terminal
# (absent as a source, so any further action raises IllegalReviewTransition).
_TRANSITIONS: dict[tuple[ReviewStatus, ReviewAction], ReviewStatus] = {
    (ReviewStatus.DRAFT, ReviewAction.SUBMIT): ReviewStatus.SUBMITTED,
    (ReviewStatus.SUBMITTED, ReviewAction.START_REVIEW): ReviewStatus.UNDER_REVIEW,
    (ReviewStatus.UNDER_REVIEW, ReviewAction.REQUEST_REVISIONS): ReviewStatus.REVISIONS_REQUESTED,
    (ReviewStatus.REVISIONS_REQUESTED, ReviewAction.RESUBMIT): ReviewStatus.SUBMITTED,
    (ReviewStatus.UNDER_REVIEW, ReviewAction.ACCEPT): ReviewStatus.ACCEPTED,
    (ReviewStatus.UNDER_REVIEW, ReviewAction.REJECT): ReviewStatus.REJECTED,
}

TERMINAL_STATES: frozenset[ReviewStatus] = frozenset({ReviewStatus.ACCEPTED, ReviewStatus.REJECTED})

_AUTHOR_ACTIONS: frozenset[ReviewAction] = frozenset({ReviewAction.SUBMIT, ReviewAction.RESUBMIT})
_REVIEWER_ACTIONS: frozenset[ReviewAction] = frozenset(
    {
        ReviewAction.START_REVIEW,
        ReviewAction.REQUEST_REVISIONS,
        ReviewAction.ACCEPT,
        ReviewAction.REJECT,
    }
)


def next_status(current: ReviewStatus, action: ReviewAction) -> ReviewStatus:
    """Return the state reached by ``action`` from ``current``; raise on an illegal transition."""
    try:
        return _TRANSITIONS[(current, action)]
    except KeyError:
        raise IllegalReviewTransition(current, action) from None


def authorize_actor(
    action: ReviewAction,
    *,
    subject_id: str,
    authors: tuple[str, ...],
    reviewers: tuple[str, ...],
) -> None:
    """Deny-by-default actor check for a review action.

    Only an author may ``submit``/``resubmit``; only an assigned reviewer may
    ``start_review``/``request_revisions``/``accept``/``reject``.
    """
    if action in _AUTHOR_ACTIONS:
        if subject_id not in authors:
            raise ReviewAuthorizationDenied(action, "only an author may submit or resubmit")
    elif action in _REVIEWER_ACTIONS:
        if subject_id not in reviewers:
            raise ReviewAuthorizationDenied(action, "only an assigned reviewer may review")
    else:  # pragma: no cover - defensive; every ReviewAction is classified above
        raise ReviewAuthorizationDenied(action, "unknown review action")


@dataclass(frozen=True, slots=True)
class DocumentReview:
    """A peer/editorial review of a research document (tenant-scoped, FR-RSH-002)."""

    review_id: str
    organization_id: str
    document_id: str
    status: ReviewStatus
    authors: tuple[str, ...]
    reviewers: tuple[str, ...]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.review_id), "review_id required", code="research.review.id")
        _require(
            bool(self.organization_id), "organization_id required", code="research.review.scope"
        )
        _require(bool(self.document_id), "document_id required", code="research.review.document")
        _require(len(self.authors) >= 1, "a review must have at least one author")


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    """An immutable record of one applied review transition (audit evidence, LAW-14)."""

    event_id: str
    organization_id: str
    review_id: str
    from_status: ReviewStatus
    to_status: ReviewStatus
    action: ReviewAction
    actor: str
    occurred_at: datetime
    note: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.event_id), "event_id required", code="research.review.event.id")
        _require(
            bool(self.actor), "review event actor required", code="research.review.event.actor"
        )
