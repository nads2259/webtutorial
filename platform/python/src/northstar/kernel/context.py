"""Actor and request context value objects (ARCH-004, FR-KRN-002).

Stdlib-only (LAW-02). These frozen value objects carry the canonical actor model shared by
the command, query and event envelopes (contracts ``command-envelope``/``query-envelope``):
``actor: {type, id, delegated_by}`` with ``type`` drawn from a fixed enum. The kernel derives
tenant scope from the authenticated context, never from a request payload (rule 50).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ActorType(StrEnum):
    """Canonical actor kinds (D-R02). Identical enum across every envelope."""

    ANONYMOUS = "anonymous"
    USER = "user"
    SERVICE = "service"
    EXTENSION = "extension"
    AI_ACTOR = "ai_actor"
    OPERATOR = "operator"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is acting: canonical ``{type, id, delegated_by}`` shape.

    ``id`` is opaque and non-empty; ``delegated_by`` records on-behalf-of delegation
    (for example an AI actor acting for a user) and is ``None`` for direct action.
    """

    type: ActorType
    id: str
    delegated_by: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("actor id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """A stable reference to the resource an action targets."""

    type: str
    id: str

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("resource type must be a non-empty string")
        if not self.id:
            raise ValueError("resource id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Ambient request identity threaded through the command/query pipeline.

    Carries the authenticated ``actor``, the ``correlation_id`` that ties every audit and
    event back to the originating request, an optional ``idempotency_key`` (replaying the
    same key must not duplicate a command's effect) and the ``tenant_scope`` derived from
    the authenticated context — never from the request payload (rule 50, tenant isolation).
    """

    actor: Actor
    correlation_id: str
    idempotency_key: str | None = None
    tenant_scope: str | None = None

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise ValueError("correlation_id must be a non-empty string")
