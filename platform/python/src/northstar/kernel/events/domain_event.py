"""Typed domain-event value object matching the canonical envelope (ARCH-010, FR-KRN-005).

Pure and stdlib-only (LAW-02, rule 10): no SQLAlchemy/psycopg imports here. :class:`DomainEvent`
mirrors ``spec/contracts/schemas/domain-event.schema.json`` (CloudEvents-aligned, rule 40):
``specversion == "1.0"``; ``type`` = ``northstar.<domain>.<name>.vN``; the canonical actor model
``{type, id, delegated_by}``; a ``correlation_id`` tying the event to its originating request;
and a structured ``data`` payload. :meth:`DomainEvent.to_envelope` renders the exact wire shape
that the transactional outbox stores and the relay publishes at-least-once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..context import Actor

_TYPE_PATTERN = re.compile(r"^northstar\.[a-z0-9-]+(?:\.[a-z0-9-]+)+\.v\d+$")
_CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "restricted"})

DATACONTENTTYPE = "application/json"
SPECVERSION = "1.0"


@dataclass(frozen=True, slots=True)
class EventScope:
    """Tenant scope carried on the envelope (``scope`` object; all fields optional/nullable)."""

    organization_id: str | None = None
    workspace_id: str | None = None
    product_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "product_id": self.product_id,
        }


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """A domain event ready to be committed to the outbox and published on the wire.

    ``event_id`` is the envelope ``id`` (a stable, opaque identifier used by consumers as the
    at-least-once de-duplication key). ``event_type`` is the canonical CloudEvents ``type``.
    ``aggregate_type``/``aggregate_id`` identify the entity the event is about (the aggregate id
    is also projected as the envelope ``subject``). ``data`` is the structured payload; the
    remaining fields carry provenance/routing metadata required by the envelope schema.
    """

    event_id: str
    event_type: str
    source: str
    correlation_id: str
    actor: Actor
    aggregate_type: str
    aggregate_id: str
    occurred_at: datetime
    data: dict[str, Any]
    dataschema: str
    classification: str = "internal"
    scope: EventScope = field(default_factory=EventScope)
    causation_id: str | None = None

    def __post_init__(self) -> None:
        if len(self.event_id) < 8 or len(self.event_id) > 128:
            raise ValueError("event_id must be a stable id of length 8..128")
        if not _TYPE_PATTERN.match(self.event_type):
            raise ValueError(
                f"event_type must match 'northstar.<domain>.<name>.vN' (got {self.event_type!r})"
            )
        if self.classification not in _CLASSIFICATIONS:
            raise ValueError(f"classification must be one of {sorted(_CLASSIFICATIONS)}")
        if not self.aggregate_type:
            raise ValueError("aggregate_type must be a non-empty string")
        if not self.aggregate_id:
            raise ValueError("aggregate_id must be a non-empty string")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware (UTC)")

    @property
    def event_version(self) -> str:
        """The ``vN`` version segment of the canonical event type (e.g. ``v1``)."""
        return self.event_type.rsplit(".", 1)[1]

    def to_envelope(self) -> dict[str, Any]:
        """Render the canonical CloudEvents-aligned envelope (schema domain-event/1.0.0)."""
        return {
            "specversion": SPECVERSION,
            "id": self.event_id,
            "type": self.event_type,
            "source": self.source,
            "subject": self.aggregate_id,
            "time": self.occurred_at.isoformat(),
            "datacontenttype": DATACONTENTTYPE,
            "dataschema": self.dataschema,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "actor": {
                "type": self.actor.type.value,
                "id": self.actor.id,
                "delegated_by": self.actor.delegated_by,
            },
            "scope": self.scope.to_dict(),
            "classification": self.classification,
            "data": self.data,
        }
