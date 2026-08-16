"""Event/outbox ports (pure abstractions, LAW-10 / rule 10/40).

Domain events request state to have changed and are recorded as past-tense facts through a
transactional **outbox**: :class:`OutboxPort.append` writes the event in the *same* unit of work
as the state change, so an event is never emitted without a committed change (and vice versa).
A relay later publishes them at-least-once through an :class:`EventPublisherPort`; consumers
de-duplicate by the envelope ``id``. :class:`OutboxBacklogPort` exposes queue lag so operators
can observe undispatched backlog (NFR-OPS-005). No infrastructure imports live here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .domain_event import DomainEvent


@runtime_checkable
class OutboxPort(Protocol):
    """Appends a domain event to the transactional outbox within the active unit of work.

    Implementations MUST enlist the write in the caller's transaction so it commits atomically
    with the domain state change (no autonomous commit). Appending the same ``event_id`` twice
    is a caller error; the store enforces uniqueness on the event id.
    """

    def append(self, event: DomainEvent) -> None: ...


@runtime_checkable
class EventPublisherPort(Protocol):
    """Publishes a canonical event envelope to downstream consumers (at-least-once).

    The relay passes the exact wire envelope (``DomainEvent.to_envelope``). Delivery is
    at-least-once, so publishers/consumers MUST treat redelivery of the same envelope ``id`` as
    idempotent. Raising signals a failed dispatch and leaves the event undispatched for retry.
    """

    def publish(self, envelope: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class OutboxBacklog:
    """Observable outbox lag/backlog snapshot (NFR-OPS-005).

    ``undispatched_count`` is the number of events awaiting dispatch; ``oldest_undispatched_at``
    is the ``occurred_at`` of the oldest such event (``None`` when the backlog is empty); and
    ``lag_seconds`` is the age of that oldest event relative to the observation time.
    """

    undispatched_count: int
    oldest_undispatched_at: datetime | None
    lag_seconds: float

    @classmethod
    def of(
        cls, *, undispatched_count: int, oldest_undispatched_at: datetime | None, now: datetime
    ) -> OutboxBacklog:
        """Build a snapshot, deriving non-negative ``lag_seconds`` from the oldest event age."""
        lag = compute_lag_seconds(oldest_undispatched_at, now)
        return cls(
            undispatched_count=undispatched_count,
            oldest_undispatched_at=oldest_undispatched_at,
            lag_seconds=lag,
        )


def compute_lag_seconds(oldest: datetime | None, now: datetime) -> float:
    """Return the non-negative age in seconds of ``oldest`` at ``now`` (``0.0`` when empty).

    Pure lag computation shared by the outbox and job-queue backlog observers (NFR-OPS-005): a
    backlog with nothing pending has zero lag, and clock skew never yields a negative lag.
    """
    if oldest is None:
        return 0.0
    return max(0.0, (now - oldest).total_seconds())


@runtime_checkable
class OutboxBacklogPort(Protocol):
    """Reports the current outbox backlog so operators can alert on dispatch lag."""

    def backlog(self, *, now: datetime | None = None) -> OutboxBacklog: ...
