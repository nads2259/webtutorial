"""Support domain model: cases, lifecycle, validated intake, and support-access grants.

Mirrors docs/29 §6 and the ``support-case.schema.json`` contract. Every type here is pure and
infrastructure-free (rule 10, LAW-02): the domain enforces the same invariants as the JSON Schema by
construction, and a contract test independently validates a produced case dict against the schema.

Key invariants enforced by construction (never weakened):

* :func:`validate_intake` REJECTS malformed / oversized / injection-shaped input; only a clean,
  bounded submission becomes a case (FR-SUP-001);
* a :class:`SupportCase` has an owner (``requester_id``) and a governed lifecycle — only the allowed
  status transitions are permitted (FR-SUP-002);
* support staff see a MINIMIZED projection by default (:func:`minimized_view`); the full/elevated
  projection (:func:`elevated_view`) is only ever returned behind an active
  :class:`SupportAccessGrant`, which is deny-by-default and TIME-BOUNDED (FR-SUP-003).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .errors import (
    IntakeValidationError,
    InvalidLifecycleTransition,
    SupportAccessInvalid,
)

# Stable resource vocabulary (contract): a support case is the tenant-scoped resource.
RES_SUPPORT_CASE = "support.case"

_MAX_SUBJECT = 200
_MIN_SUBJECT = 3
_MAX_CATEGORY = 100
_MAX_BODY = 10_000

# Injection-shaped markers rejected at intake (FR-SUP-001). Deny-by-default: any markup/script,
# template-injection, protocol handler, SQL signature or control byte is refused.
_HTML_TAG = re.compile(r"<\s*[a-zA-Z/!]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_INJECTION_MARKERS = (
    "javascript:",
    "{{",
    "${",
    "<script",
    "union select",
    "drop table",
    "'; --",
    "' or '1'='1",
)


class CaseStatus(StrEnum):
    """Support case lifecycle status (schema ``status``)."""

    NEW = "new"
    OPEN = "open"
    WAITING_USER = "waiting_user"
    WAITING_INTERNAL = "waiting_internal"
    RESOLVED = "resolved"
    CLOSED = "closed"


class CasePriority(StrEnum):
    """Support case priority (schema ``priority``)."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class MessageVisibility(StrEnum):
    """Message visibility (schema ``messages[].visibility``)."""

    REQUESTER = "requester"
    INTERNAL = "internal"


class AuthorType(StrEnum):
    """Message author type (schema ``messages[].author_type``)."""

    REQUESTER = "requester"
    AGENT = "agent"
    SYSTEM = "system"


# Allowed lifecycle transitions (FR-SUP-002): deny-by-default — anything not listed is rejected.
_ALLOWED_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.NEW: frozenset({CaseStatus.OPEN, CaseStatus.CLOSED}),
    CaseStatus.OPEN: frozenset(
        {
            CaseStatus.WAITING_USER,
            CaseStatus.WAITING_INTERNAL,
            CaseStatus.RESOLVED,
            CaseStatus.CLOSED,
        }
    ),
    CaseStatus.WAITING_USER: frozenset({CaseStatus.OPEN, CaseStatus.RESOLVED, CaseStatus.CLOSED}),
    CaseStatus.WAITING_INTERNAL: frozenset(
        {CaseStatus.OPEN, CaseStatus.RESOLVED, CaseStatus.CLOSED}
    ),
    CaseStatus.RESOLVED: frozenset({CaseStatus.OPEN, CaseStatus.CLOSED}),
    CaseStatus.CLOSED: frozenset(),
}


def _looks_injection(text: str) -> bool:
    lowered = text.lower()
    if _CONTROL.search(text):
        return True
    if _HTML_TAG.search(text):
        return True
    return any(marker in lowered for marker in _INJECTION_MARKERS)


@dataclass(frozen=True, slots=True)
class IntakeContent:
    """The validated, bounded content that becomes a support case + its first message."""

    subject: str
    category: str
    body: str
    priority: CasePriority


def validate_intake(
    *, subject: str, category: str, body: str, priority: str = "normal"
) -> IntakeContent:
    """Validate a support intake submission, rejecting malformed/oversized/injection input.

    Raises :class:`IntakeValidationError` for any violation (deny-by-default, FR-SUP-001):
    length bounds on subject/category/body, empty body, injection-shaped markup/script/SQL/template
    markers, or control bytes.
    """
    subject = subject.strip()
    category = category.strip()
    if not (_MIN_SUBJECT <= len(subject) <= _MAX_SUBJECT):
        raise IntakeValidationError(
            f"subject length must be between {_MIN_SUBJECT} and {_MAX_SUBJECT} characters"
        )
    if not category or len(category) > _MAX_CATEGORY:
        raise IntakeValidationError(
            f"category is required and must be at most {_MAX_CATEGORY} characters"
        )
    if not body.strip():
        raise IntakeValidationError("message body must not be empty")
    if len(body) > _MAX_BODY:
        raise IntakeValidationError(f"message body must be at most {_MAX_BODY} characters")
    for value in (subject, category, body):
        if _looks_injection(value):
            raise IntakeValidationError("input contains disallowed markup/script/injection markers")
    try:
        parsed_priority = CasePriority(priority)
    except ValueError as exc:
        raise IntakeValidationError(f"invalid priority {priority!r}") from exc
    return IntakeContent(subject=subject, category=category, body=body, priority=parsed_priority)


@dataclass(frozen=True, slots=True)
class SupportMessage:
    """A message on a case; ``visibility`` separates requester-facing from internal notes."""

    message_id: str
    author_type: AuthorType
    body_ref: str
    visibility: MessageVisibility
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "message_id": self.message_id,
            "author_type": self.author_type.value,
            "body_ref": self.body_ref,
            "created_at": self.created_at.isoformat(),
            "visibility": self.visibility.value,
        }


@dataclass(frozen=True, slots=True)
class SupportCase:
    """A governed support case with an owner (requester) and a lifecycle (docs/29 §6)."""

    case_id: str
    requester_id: str
    status: CaseStatus
    priority: CasePriority
    category: str
    created_at: datetime
    audit_scope: str
    subject: str | None = None
    organization_id: str | None = None
    assignee_id: str | None = None
    updated_at: datetime | None = None
    retention_policy: str | None = None
    messages: tuple[SupportMessage, ...] = ()
    related_resources: tuple[dict[str, str], ...] = ()

    def assigned(self, *, assignee_id: str, now: datetime) -> SupportCase:
        """Assign the case to a staff member; a NEW case becomes OPEN on first assignment."""
        status = CaseStatus.OPEN if self.status is CaseStatus.NEW else self.status
        return self._replace(assignee_id=assignee_id, status=status, updated_at=now)

    def transitioned(self, *, to_status: CaseStatus, now: datetime) -> SupportCase:
        """Move the case to ``to_status`` if the transition is allowed (else reject)."""
        if to_status not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidLifecycleTransition(self.status.value, to_status.value)
        return self._replace(status=to_status, updated_at=now)

    def with_message(self, message: SupportMessage) -> SupportCase:
        return self._replace(messages=(*self.messages, message))

    def with_messages(self, messages: tuple[SupportMessage, ...]) -> SupportCase:
        """Return a copy with ``messages`` replaced (used by repositories on load/store)."""
        return self._replace(messages=tuple(messages))

    def _replace(self, **changes: object) -> SupportCase:
        data = {
            "case_id": self.case_id,
            "requester_id": self.requester_id,
            "status": self.status,
            "priority": self.priority,
            "category": self.category,
            "created_at": self.created_at,
            "audit_scope": self.audit_scope,
            "subject": self.subject,
            "organization_id": self.organization_id,
            "assignee_id": self.assignee_id,
            "updated_at": self.updated_at,
            "retention_policy": self.retention_policy,
            "messages": self.messages,
            "related_resources": self.related_resources,
        }
        data.update(changes)
        return SupportCase(**data)  # type: ignore[arg-type]

    def to_contract(self) -> dict[str, object]:
        """Serialise to the ``support-case`` JSON contract (schema-valid, full/elevated view)."""
        payload: dict[str, object] = {
            "case_id": self.case_id,
            "requester_id": self.requester_id,
            "organization_id": self.organization_id,
            "status": self.status.value,
            "priority": self.priority.value,
            "category": self.category,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "messages": [m.to_dict() for m in self.messages],
            "related_resources": [dict(r) for r in self.related_resources],
            "audit_scope": self.audit_scope,
            "retention_policy": self.retention_policy,
        }
        if self.subject is not None:
            payload["subject"] = self.subject
        return payload


# ---------------------------------------------------------------------------
# Data minimization projections (FR-SUP-003)
# ---------------------------------------------------------------------------


def minimized_view(case: SupportCase) -> dict[str, object]:
    """The MINIMUM data support staff see by default: no requester PII, no internal notes.

    Excludes ``requester_id``, related resources, message bodies and every internal message; a
    counter and an ``has_internal_notes`` flag let staff triage without a privileged read
    (deny-by-default, FR-SUP-003).
    """
    requester_visible = [m for m in case.messages if m.visibility is MessageVisibility.REQUESTER]
    return {
        "case_id": case.case_id,
        "status": case.status.value,
        "priority": case.priority.value,
        "category": case.category,
        "subject": case.subject,
        "assignee_id": case.assignee_id,
        "requester_visible_message_count": len(requester_visible),
        "has_internal_notes": any(
            m.visibility is MessageVisibility.INTERNAL for m in case.messages
        ),
        "minimized": True,
    }


def elevated_view(case: SupportCase) -> dict[str, object]:
    """The full case (incl. requester id, internal notes, related resources) — grant-gated only."""
    payload = case.to_contract()
    payload["minimized"] = False
    return payload


# ---------------------------------------------------------------------------
# Support-access grant (audited, deny-by-default, time-bounded) — FR-SUP-003
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupportAccessGrant:
    """A time-bounded, audited grant letting one staff member read one case in full (docs/29 §6).

    Construction requires ``expires_at`` strictly after ``starts_at`` (time-bounded) and a non-empty
    reason: a broad/perpetual grant cannot be constructed. :meth:`is_active` denies once revoked or
    expired (deny-by-default).
    """

    grant_id: str
    case_id: str
    staff_id: str
    granted_by: str
    reason: str
    starts_at: datetime
    expires_at: datetime
    scope: str = "full_case"
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.grant_id or not self.case_id or not self.staff_id:
            raise SupportAccessInvalid("support access grant requires grant/case/staff ids")
        if not self.granted_by:
            raise SupportAccessInvalid("support access grant requires an authorizing granter")
        if not self.reason.strip():
            raise SupportAccessInvalid("support access grant requires a non-empty reason code")
        if self.expires_at <= self.starts_at:
            raise SupportAccessInvalid(
                "support access grant must be time-bounded (expires_at > starts_at)"
            )

    def is_active(self, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        return self.starts_at <= now < self.expires_at

    def revoked(self, *, now: datetime) -> SupportAccessGrant:
        return SupportAccessGrant(
            grant_id=self.grant_id,
            case_id=self.case_id,
            staff_id=self.staff_id,
            granted_by=self.granted_by,
            reason=self.reason,
            starts_at=self.starts_at,
            expires_at=self.expires_at,
            scope=self.scope,
            revoked_at=now,
        )


__all__ = [
    "RES_SUPPORT_CASE",
    "AuthorType",
    "CasePriority",
    "CaseStatus",
    "IntakeContent",
    "MessageVisibility",
    "SupportAccessGrant",
    "SupportCase",
    "SupportMessage",
    "elevated_view",
    "minimized_view",
    "validate_intake",
]
