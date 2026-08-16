"""Job-queue and scheduler ports (pure abstractions, LAW-10 / rule 10/40).

The job queue provides at-least-once, lease-based delivery of durable jobs: a worker
:meth:`JobQueuePort.claim` s a ready job (taking a time-bounded lease), then reports the
outcome with :meth:`JobQueuePort.complete` (success) or :meth:`JobQueuePort.fail` (retry or
dead-letter). Because delivery is at-least-once and leases can expire, jobs are identified by
``(job_type, idempotency_key)`` and workers must be idempotent — failure/timeout is never
success. The :class:`SchedulerPort` enqueues jobs that have become due. :class:`QueueBacklogPort`
exposes queue lag/backlog so operators can observe it (NFR-OPS-005). No infrastructure here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from .job import Job, JobSpec


@runtime_checkable
class JobQueuePort(Protocol):
    """A durable, lease-based, idempotent job queue."""

    def enqueue(self, spec: JobSpec, *, now: datetime | None = None) -> Job:
        """Enqueue ``spec``; return the existing job if its idempotency key already exists."""
        ...

    def claim(
        self,
        queue: str,
        *,
        owner: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> Job | None:
        """Atomically lease one ready, due job on ``queue`` (or ``None`` if none is available)."""
        ...

    def complete(self, job_id: str, *, owner: str) -> Job:
        """Mark a leased job succeeded. Raises if the caller does not hold the lease."""
        ...

    def fail(
        self,
        job_id: str,
        *,
        owner: str,
        error: str,
        retry_in: timedelta | None = None,
        now: datetime | None = None,
    ) -> Job:
        """Record a failed attempt: reschedule for retry, or dead-letter when attempts exhausted."""
        ...

    def reclaim_expired(self, *, now: datetime | None = None) -> int:
        """Return expired leases to ``ready`` (timeout != success); return the count reclaimed."""
        ...


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    """A schedule entry: a job specification with the time it becomes due."""

    due_at: datetime
    spec: JobSpec

    def __post_init__(self) -> None:
        if self.due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware (UTC)")


@runtime_checkable
class SchedulerPort(Protocol):
    """Enqueues due scheduled jobs onto the job queue (idempotently)."""

    def enqueue_due(self, *, now: datetime | None = None) -> list[Job]:
        """Enqueue every schedule entry whose ``due_at`` has passed; return the enqueued jobs."""
        ...


@dataclass(frozen=True, slots=True)
class QueueBacklog:
    """Observable job-queue lag/backlog snapshot for one queue (NFR-OPS-005).

    ``ready_count`` is the number of jobs ready to run; ``oldest_available_at`` is the earliest
    ``available_at`` among them (``None`` when empty); ``lag_seconds`` is how long that oldest
    ready job has been waiting relative to the observation time.
    """

    queue: str
    ready_count: int
    oldest_available_at: datetime | None
    lag_seconds: float

    @classmethod
    def of(
        cls,
        *,
        queue: str,
        ready_count: int,
        oldest_available_at: datetime | None,
        now: datetime,
    ) -> QueueBacklog:
        """Build a snapshot, deriving non-negative ``lag_seconds`` from the oldest ready job."""
        from ..events.ports import compute_lag_seconds

        return cls(
            queue=queue,
            ready_count=ready_count,
            oldest_available_at=oldest_available_at,
            lag_seconds=compute_lag_seconds(oldest_available_at, now),
        )


@runtime_checkable
class QueueBacklogPort(Protocol):
    """Reports the current backlog/lag for a queue so operators can alert on it."""

    def backlog(self, queue: str, *, now: datetime | None = None) -> QueueBacklog: ...
