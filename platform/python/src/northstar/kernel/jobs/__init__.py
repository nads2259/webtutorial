"""Kernel job domain: job value objects and queue/scheduler/backlog ports.

Pure (LAW-02/LAW-12): value objects + Protocols only, no infrastructure. Concrete
SQLAlchemy-backed queue, scheduler and backlog implementations live under
``northstar.adapters.persistence_sqlalchemy``.
"""

from __future__ import annotations

from .job import Job, JobSpec, JobStatus
from .ports import (
    JobQueuePort,
    QueueBacklog,
    QueueBacklogPort,
    ScheduledJob,
    SchedulerPort,
)

__all__ = [
    "Job",
    "JobQueuePort",
    "JobSpec",
    "JobStatus",
    "QueueBacklog",
    "QueueBacklogPort",
    "ScheduledJob",
    "SchedulerPort",
]
