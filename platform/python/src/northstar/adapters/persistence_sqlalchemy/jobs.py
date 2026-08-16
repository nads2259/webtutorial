"""SQLAlchemy-backed durable job queue, scheduler and backlog metrics (adapter layer).

Implements the kernel :mod:`northstar.kernel.jobs.ports`:

* :class:`SqlAlchemyJobQueue` — a lease-based, idempotent
  :class:`~northstar.kernel.jobs.ports.JobQueuePort` (and
  :class:`~northstar.kernel.jobs.ports.QueueBacklogPort`). ``enqueue`` de-duplicates on
  ``(job_type, idempotency_key)`` so replays never create a second job; ``claim`` leases one due
  job (``FOR UPDATE SKIP LOCKED`` on PostgreSQL); ``complete``/``fail`` record terminal or retry
  outcomes — a failed or timed-out attempt is never treated as success and dead-letters once
  ``max_attempts`` is exhausted.
* :class:`SqlAlchemyScheduler` — a :class:`~northstar.kernel.jobs.ports.SchedulerPort` that
  enqueues due schedule entries onto the queue (idempotently via the queue's dedup key).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Row, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from northstar.kernel.jobs.job import Job, JobSpec, JobStatus
from northstar.kernel.jobs.ports import QueueBacklog, ScheduledJob

from .runtime_tables import RUNTIME_TABLES, RuntimeTables

_DEFAULT_RETRY_BACKOFF = timedelta(seconds=30)


class JobNotFoundError(RuntimeError):
    """The referenced job does not exist."""


class LeaseNotHeldError(RuntimeError):
    """The caller does not hold an active lease on the job (idempotency/ownership guard)."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _row_to_job(row: Row) -> Job:
    created = _as_utc(row.created_at)
    available = _as_utc(row.available_at)
    assert created is not None  # noqa: S101 - NOT NULL columns
    assert available is not None  # noqa: S101
    return Job(
        job_id=str(row.job_id),
        job_type=row.job_type,
        job_version=row.job_version,
        queue=row.queue,
        idempotency_key=row.idempotency_key,
        payload=dict(row.payload),
        status=JobStatus(row.status),
        available_at=available,
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        created_at=created,
        lease_owner=row.lease_owner,
        lease_expires_at=_as_utc(row.lease_expires_at),
        last_error=row.last_error,
    )


class SqlAlchemyJobQueue:
    """A durable, lease-based, idempotent job queue backed by the ``job`` table."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        tables: RuntimeTables = RUNTIME_TABLES,
        default_retry_backoff: timedelta = _DEFAULT_RETRY_BACKOFF,
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables
        self._default_retry_backoff = default_retry_backoff

    def enqueue(self, spec: JobSpec, *, now: datetime | None = None) -> Job:
        observed = now or _utcnow()
        available_at = spec.available_at or observed
        table = self._tables.job
        with self._session_factory() as session:
            existing = self._find_by_key(session, spec.job_type, spec.idempotency_key)
            if existing is not None:
                return _row_to_job(existing)
            try:
                session.execute(
                    insert(table).values(
                        job_id=str(uuid.uuid4()),
                        job_type=spec.job_type,
                        job_version=spec.job_version,
                        queue=spec.queue,
                        idempotency_key=spec.idempotency_key,
                        payload=dict(spec.payload),
                        status=JobStatus.READY.value,
                        available_at=available_at,
                        lease_owner=None,
                        lease_expires_at=None,
                        attempt_count=0,
                        max_attempts=spec.max_attempts,
                        last_error=None,
                        created_at=observed,
                    )
                )
                session.commit()
            except IntegrityError:
                # Concurrent enqueue of the same key won the race: return the persisted job.
                session.rollback()
                existing = self._find_by_key(session, spec.job_type, spec.idempotency_key)
                if existing is None:
                    raise
                return _row_to_job(existing)
            return _row_to_job(self._find_by_key(session, spec.job_type, spec.idempotency_key))

    def claim(
        self,
        queue: str,
        *,
        owner: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> Job | None:
        observed = now or _utcnow()
        table = self._tables.job
        with self._session_factory() as session:
            stmt = (
                select(table)
                .where(
                    table.c.queue == queue,
                    table.c.status == JobStatus.READY.value,
                    table.c.available_at <= observed,
                )
                .order_by(table.c.available_at)
                .limit(1)
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)
            row = session.execute(stmt).first()
            if row is None:
                return None
            session.execute(
                update(table)
                .where(table.c.job_id == row.job_id)
                .values(
                    status=JobStatus.LEASED.value,
                    lease_owner=owner,
                    lease_expires_at=observed + lease_duration,
                    attempt_count=row.attempt_count + 1,
                )
            )
            session.commit()
            return _row_to_job(self._get(session, row.job_id))

    def complete(self, job_id: str, *, owner: str) -> Job:
        table = self._tables.job
        with self._session_factory() as session:
            row = self._get_or_none(session, job_id)
            if row is None:
                raise JobNotFoundError(job_id)
            if row.status != JobStatus.LEASED.value or row.lease_owner != owner:
                raise LeaseNotHeldError(job_id)
            session.execute(
                update(table)
                .where(table.c.job_id == job_id)
                .values(
                    status=JobStatus.SUCCEEDED.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error=None,
                )
            )
            session.commit()
            return _row_to_job(self._get(session, job_id))

    def fail(
        self,
        job_id: str,
        *,
        owner: str,
        error: str,
        retry_in: timedelta | None = None,
        now: datetime | None = None,
    ) -> Job:
        observed = now or _utcnow()
        backoff = self._default_retry_backoff if retry_in is None else retry_in
        table = self._tables.job
        with self._session_factory() as session:
            row = self._get_or_none(session, job_id)
            if row is None:
                raise JobNotFoundError(job_id)
            if row.status != JobStatus.LEASED.value or row.lease_owner != owner:
                raise LeaseNotHeldError(job_id)
            if row.attempt_count >= row.max_attempts:
                values: dict[str, Any] = {
                    "status": JobStatus.DEAD.value,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "last_error": error,
                }
            else:
                values = {
                    "status": JobStatus.READY.value,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "available_at": observed + backoff,
                    "last_error": error,
                }
            session.execute(update(table).where(table.c.job_id == job_id).values(**values))
            session.commit()
            return _row_to_job(self._get(session, job_id))

    def reclaim_expired(self, *, now: datetime | None = None) -> int:
        observed = now or _utcnow()
        table = self._tables.job
        with self._session_factory() as session:
            result = session.execute(
                update(table)
                .where(
                    table.c.status == JobStatus.LEASED.value,
                    table.c.lease_expires_at < observed,
                )
                .values(status=JobStatus.READY.value, lease_owner=None, lease_expires_at=None)
            )
            session.commit()
            return int(result.rowcount or 0)

    def backlog(self, queue: str, *, now: datetime | None = None) -> QueueBacklog:
        observed = now or _utcnow()
        table = self._tables.job
        with self._session_factory() as session:
            row = session.execute(
                select(
                    func.count().label("cnt"),
                    func.min(table.c.available_at).label("oldest"),
                ).where(table.c.queue == queue, table.c.status == JobStatus.READY.value)
            ).one()
        count = int(row.cnt)
        oldest = _as_utc(row.oldest)
        return QueueBacklog.of(
            queue=queue, ready_count=count, oldest_available_at=oldest, now=observed
        )

    def _find_by_key(self, session: Session, job_type: str, idempotency_key: str) -> Row | None:
        table = self._tables.job
        return session.execute(
            select(table).where(
                table.c.job_type == job_type, table.c.idempotency_key == idempotency_key
            )
        ).first()

    def _get_or_none(self, session: Session, job_id: str) -> Row | None:
        table = self._tables.job
        return session.execute(select(table).where(table.c.job_id == job_id)).first()

    def _get(self, session: Session, job_id: object) -> Row:
        row = self._get_or_none(session, str(job_id))
        if row is None:
            raise JobNotFoundError(str(job_id))
        return row


class SqlAlchemyScheduler:
    """Enqueues due scheduled jobs onto a :class:`SqlAlchemyJobQueue` (idempotent via dedup key)."""

    def __init__(self, queue: SqlAlchemyJobQueue, schedule: Sequence[ScheduledJob]) -> None:
        self._queue = queue
        self._schedule = tuple(schedule)

    def enqueue_due(self, *, now: datetime | None = None) -> list[Job]:
        observed = now or _utcnow()
        enqueued: list[Job] = []
        for entry in self._schedule:
            if entry.due_at <= observed:
                enqueued.append(self._queue.enqueue(entry.spec, now=observed))
        return enqueued
