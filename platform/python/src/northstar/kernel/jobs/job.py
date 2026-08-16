"""Job value objects for the durable, idempotent job queue (ARCH-010, FR-KRN-005).

Pure and stdlib-only (LAW-02, rule 10). A :class:`Job` is an immutable snapshot of a queued
unit of work. Jobs are **idempotent**: a ``(job_type, idempotency_key)`` pair identifies a
single logical job, so enqueueing the same key twice never produces two effects, and workers
must be replay-safe. :class:`JobStatus` mirrors the persisted state machine (mirroring
``spec/reference/one-touch/db/migrations/000002_runtime_outbox_jobs.sql``): a job is ``READY``,
leased to a worker (``LEASED``), then terminal ``SUCCEEDED``/``FAILED``, or ``DEAD`` (dead-letter)
once retries are exhausted. Failure and timeout are never success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    """Lifecycle state of a queued job (mirrors the persisted ``status`` check constraint)."""

    READY = "ready"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"

    @property
    def is_terminal(self) -> bool:
        """Whether no further processing will occur for a job in this state."""
        return self in (JobStatus.SUCCEEDED, JobStatus.DEAD)


@dataclass(frozen=True, slots=True)
class JobSpec:
    """The caller-supplied definition of a job to enqueue.

    ``idempotency_key`` de-duplicates enqueues within ``job_type``: submitting the same key
    again returns the existing job rather than creating a duplicate.
    """

    job_type: str
    job_version: str
    queue: str
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    max_attempts: int = 10
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.job_type:
            raise ValueError("job_type must be a non-empty string")
        if not self.queue:
            raise ValueError("queue must be a non-empty string")
        if not self.idempotency_key:
            raise ValueError("idempotency_key must be a non-empty string")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.available_at is not None and self.available_at.tzinfo is None:
            raise ValueError("available_at must be timezone-aware (UTC)")


@dataclass(frozen=True, slots=True)
class Job:
    """An immutable snapshot of a persisted job row."""

    job_id: str
    job_type: str
    job_version: str
    queue: str
    idempotency_key: str
    payload: dict[str, Any]
    status: JobStatus
    available_at: datetime
    attempt_count: int
    max_attempts: int
    created_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    last_error: str | None = None
