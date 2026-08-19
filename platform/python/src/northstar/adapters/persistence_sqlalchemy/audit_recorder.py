"""Durable SQLAlchemy audit recorder (implements :class:`AuditRecorderPort`, LAW-14 / IMPL-004).

Subclasses the kernel's :class:`InMemoryAuditRecorder` so it keeps the exact reference behaviour —
sealing each entry with a stable ``record_sha256`` and exposing the append-only ``records`` trail the
Governance Studio explorer reads — and ADDS durable persistence: every sealed record is written to
``northstar_audit.audit_record`` so the audit trail survives a process restart (the in-memory
recorder loses it). Persistence is a separate unit of work from the audited command's own
transaction; a persistence failure degrades gracefully to the in-memory trail rather than failing the
audited action (availability over strictness for the reference sink — a production sink can choose
fail-closed).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.kernel.audit.ports import AuditOutcome, AuditRecord
from northstar.kernel.audit.reference import (
    InMemoryAuditRecorder,
    _new_evidence_id,
    _utc_now_iso,
)
from northstar.kernel.context import Actor, ResourceRef

from .audit_tables import build_audit_table
from .unit_of_work import SqlAlchemyUnitOfWork


class SqlAlchemyAuditRecorder(InMemoryAuditRecorder):
    """Append-only recorder that seals each entry AND persists it durably to PostgreSQL."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        clock: Callable[[], str] = _utc_now_iso,
        id_factory: Callable[[], str] = _new_evidence_id,
    ) -> None:
        super().__init__(clock=clock, id_factory=id_factory)
        self._session_factory = session_factory
        # A private MetaData so the table definition never collides with other builders.
        from sqlalchemy import MetaData

        self._table = build_audit_table(MetaData())

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
    ) -> AuditRecord:
        record = super().record(
            event_type=event_type,
            actor=actor,
            action=action,
            outcome=outcome,
            correlation_id=correlation_id,
            resource=resource,
            decision_ref=decision_ref,
            reason_codes=reason_codes,
        )
        self._persist(record)
        return record

    def _persist(self, record: AuditRecord) -> None:
        try:
            with SqlAlchemyUnitOfWork(self._session_factory) as uow:
                uow.session.execute(
                    insert(self._table).values(
                        evidence_id=record.evidence_id,
                        event_type=record.event_type,
                        occurred_at=record.occurred_at,
                        actor_type=record.actor.type.value,
                        actor_id=record.actor.id,
                        actor_delegated_by=record.actor.delegated_by,
                        action=record.action,
                        outcome=record.outcome.value,
                        correlation_id=record.correlation_id,
                        resource_type=None if record.resource is None else record.resource.type,
                        resource_id=None if record.resource is None else record.resource.id,
                        decision_ref=record.decision_ref,
                        reason_codes=list(record.reason_codes),
                        record_sha256=record.record_sha256,
                        created_at=datetime.now(UTC),
                    )
                )
                uow.commit()
        except SQLAlchemyError:
            # The sealed record remains in the in-memory trail; durability degrades gracefully rather
            # than failing the audited action (e.g. if the audit schema is absent in a portable test).
            pass
