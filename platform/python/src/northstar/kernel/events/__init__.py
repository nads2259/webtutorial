"""Kernel event domain: canonical domain-event value object and outbox/publisher ports.

Pure (LAW-02/LAW-12): value objects + Protocols only, no infrastructure. Concrete
SQLAlchemy-backed outbox and relay implementations live under
``northstar.adapters.persistence_sqlalchemy``.
"""

from __future__ import annotations

from .domain_event import (
    DATACONTENTTYPE,
    SPECVERSION,
    DomainEvent,
    EventScope,
)
from .ports import (
    EventPublisherPort,
    OutboxBacklog,
    OutboxBacklogPort,
    OutboxPort,
    compute_lag_seconds,
)

__all__ = [
    "DATACONTENTTYPE",
    "SPECVERSION",
    "DomainEvent",
    "EventPublisherPort",
    "EventScope",
    "OutboxBacklog",
    "OutboxBacklogPort",
    "OutboxPort",
    "compute_lag_seconds",
]
