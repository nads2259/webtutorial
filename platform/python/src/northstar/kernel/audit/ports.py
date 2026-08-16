"""Audit / evidence port and tamper-evident record value object (LAW-14, FR-KRN-002).

Every meaningful action leaves a tamper-evident record (LAW-14). The :class:`AuditRecord`
mirrors the ``audit-evidence`` contract: ``actor``, ``action``, ``resource``, ``outcome``,
``correlation_id`` and an ``integrity.record_sha256`` computed over a canonical serialization.
Concrete durable sinks are adapters behind :class:`AuditRecorderPort` (LAW-12); the kernel
ships an in-memory reference recorder (see :mod:`northstar.kernel.audit.reference`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..context import Actor, ResourceRef


class AuditOutcome(StrEnum):
    """Outcome of an audited action (``audit-evidence`` contract enum)."""

    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"
    PENDING = "pending"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """A single tamper-evident audit-evidence record.

    ``record_sha256`` is a hex digest over the record's canonical, hash-excluded content, so
    any later mutation of the stored fields is detectable. Records are immutable value objects.
    """

    evidence_id: str
    event_type: str
    occurred_at: str
    actor: Actor
    action: str
    outcome: AuditOutcome
    correlation_id: str
    record_sha256: str
    resource: ResourceRef | None = None
    decision_ref: str | None = None
    reason_codes: tuple[str, ...] = ()


@runtime_checkable
class AuditRecorderPort(Protocol):
    """Records a tamper-evident audit-evidence entry and returns the sealed record.

    Implementations MUST compute and populate ``record_sha256``; callers never supply it.
    """

    def record(
        self,
        *,
        event_type: str,
        actor: Actor,
        action: str,
        outcome: AuditOutcome,
        correlation_id: str,
        resource: ResourceRef | None = None,
        decision_ref: str | None = None,
        reason_codes: tuple[str, ...] = (),
    ) -> AuditRecord: ...
