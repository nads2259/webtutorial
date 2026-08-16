"""SQLAlchemy-backed transactional outbox and at-least-once relay (adapter layer).

Implements the kernel :mod:`northstar.kernel.events.ports`:

* :class:`SqlAlchemyOutbox` — an :class:`~northstar.kernel.events.ports.OutboxPort` bound to an
  *active session*. ``append`` inserts the event on that session **without committing**, so the
  event is written in the same unit of work as the domain state change (atomicity, LAW-10): if
  the caller's transaction rolls back, the event is gone; if it commits, the event is durable.
* :class:`SqlAlchemyOutboxRelay` — reads undispatched rows and publishes their canonical
  envelopes at-least-once through an injected
  :class:`~northstar.kernel.events.ports.EventPublisherPort`, preserving ``correlation_id`` and
  the ``id`` used by consumers for de-duplication. On success the row is marked dispatched; on
  failure the attempt is recorded and retried with backoff (never silently dropped).
* :class:`SqlAlchemyOutboxBacklog` — an
  :class:`~northstar.kernel.events.ports.OutboxBacklogPort` exposing undispatched backlog + lag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from northstar.kernel.events.domain_event import DomainEvent
from northstar.kernel.events.ports import EventPublisherPort, OutboxBacklog

from .runtime_tables import RUNTIME_TABLES, RuntimeTables


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _split_envelope(event: DomainEvent) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(payload, metadata)`` where metadata is the envelope minus its ``data``."""
    envelope = event.to_envelope()
    payload = envelope["data"]
    metadata = {k: v for k, v in envelope.items() if k != "data"}
    return payload, metadata


class SqlAlchemyOutbox:
    """Appends domain events into the outbox table on the caller's active session (no commit)."""

    def __init__(self, session: Session, *, tables: RuntimeTables = RUNTIME_TABLES) -> None:
        self._session = session
        self._tables = tables

    def append(self, event: DomainEvent) -> None:
        payload, metadata = _split_envelope(event)
        self._session.execute(
            insert(self._tables.outbox_event).values(
                event_id=event.event_id,
                event_type=event.event_type,
                event_version=event.event_version,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                occurred_at=event.occurred_at,
                payload=payload,
                metadata=metadata,
                dispatched_at=None,
                attempt_count=0,
                next_attempt_at=event.occurred_at,
                last_error=None,
            )
        )


class SqlAlchemyOutboxRelay:
    """Publishes undispatched outbox events at-least-once and marks them dispatched."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        publisher: EventPublisherPort,
        *,
        tables: RuntimeTables = RUNTIME_TABLES,
        retry_backoff: timedelta = timedelta(seconds=30),
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._tables = tables
        self._retry_backoff = retry_backoff

    def relay_batch(self, *, limit: int = 100, now: datetime | None = None) -> int:
        """Publish up to ``limit`` due, undispatched events; return the count dispatched."""
        observed = now or _utcnow()
        table = self._tables.outbox_event
        dispatched = 0
        with self._session_factory() as session:
            rows = self._claim_rows(session, limit=limit, now=observed)
            for row in rows:
                envelope = {**row.metadata, "data": row.payload}
                try:
                    self._publisher.publish(envelope)
                except Exception as exc:  # relay records failure and retries later
                    session.execute(
                        update(table)
                        .where(table.c.event_id == row.event_id)
                        .values(
                            attempt_count=row.attempt_count + 1,
                            next_attempt_at=observed + self._retry_backoff,
                            last_error=str(exc),
                        )
                    )
                    continue
                session.execute(
                    update(table)
                    .where(table.c.event_id == row.event_id)
                    .values(dispatched_at=observed, last_error=None)
                )
                dispatched += 1
            session.commit()
        return dispatched

    def _claim_rows(self, session: Session, *, limit: int, now: datetime) -> list[Any]:
        table = self._tables.outbox_event
        stmt = (
            select(table)
            .where(table.c.dispatched_at.is_(None), table.c.next_attempt_at <= now)
            .order_by(table.c.occurred_at)
            .limit(limit)
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return list(session.execute(stmt).all())


class SqlAlchemyOutboxBacklog:
    """Reports undispatched outbox backlog and dispatch lag (NFR-OPS-005)."""

    def __init__(
        self, session_factory: sessionmaker[Session], *, tables: RuntimeTables = RUNTIME_TABLES
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def backlog(self, *, now: datetime | None = None) -> OutboxBacklog:
        observed = now or _utcnow()
        table = self._tables.outbox_event
        with self._session_factory() as session:
            row = session.execute(
                select(
                    func.count().label("cnt"),
                    func.min(table.c.occurred_at).label("oldest"),
                ).where(table.c.dispatched_at.is_(None))
            ).one()
        count = int(row.cnt)
        oldest = row.oldest
        if oldest is not None and oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        return OutboxBacklog.of(
            undispatched_count=count, oldest_undispatched_at=oldest, now=observed
        )
