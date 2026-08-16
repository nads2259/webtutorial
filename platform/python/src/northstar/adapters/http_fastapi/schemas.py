"""Wire DTOs for the HTTP adapter (pydantic v2, validated at the trust boundary).

These models mirror the canonical ``command-envelope``/``query-envelope`` contracts
(``spec/contracts/schemas/*-envelope.schema.json``, rule 40): the canonical actor model
``{type, id, delegated_by}``, a ``correlation_id`` and an optional ``idempotency_key``. They
exist only to validate the inbound envelope and carry no business logic — the adapter builds a
kernel :class:`~northstar.kernel.context.RequestContext` and command/query from them and calls
the bus (LAW-04). ``additionalProperties: false`` from the schema is enforced via
``model_config = {"extra": "forbid"}`` so undocumented fields are rejected (rule 40 §3).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_ACTOR_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
_SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

ActorTypeLiteral = Literal["anonymous", "user", "service", "extension", "ai_actor", "operator"]


class ActorModel(BaseModel):
    """Canonical actor envelope shape (identical across command/query/event contracts)."""

    model_config = ConfigDict(extra="forbid")

    type: ActorTypeLiteral
    id: str = Field(min_length=1, max_length=160, pattern=_ACTOR_ID_PATTERN)
    delegated_by: str | None = None


class ResourceModel(BaseModel):
    """The optional ``target``/resource an action addresses (``resource-ref`` shape)."""

    model_config = ConfigDict(extra="forbid")

    resource_type: str
    resource_id: str = Field(min_length=3, max_length=160, pattern=_ACTOR_ID_PATTERN)


class CommandEnvelope(BaseModel):
    """Inbound command envelope (``command-envelope/1.1.0`` fields).

    The capability coordinate the kernel dispatches on is taken from the URL path; this
    envelope carries the actor, correlation and payload the pipeline needs. ``version`` is the
    capability version to resolve; ``idempotency_key``/``correlation_id`` may also arrive as
    transport headers (the header wins) per docs/05 §5.
    """

    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1)
    command_type: str = Field(pattern=r"^[A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]+)*$")
    version: str = Field(pattern=_SEMVER_PATTERN)
    actor: ActorModel
    issued_at: str
    correlation_id: str = Field(min_length=1)
    causation_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    tenant_scope: str | None = Field(default=None, max_length=120)
    expected_revision: str | int | None = None
    target: ResourceModel | None = None
    payload: dict[str, Any]


class QueryEnvelope(BaseModel):
    """Inbound query envelope (``query-envelope/1.1.0`` fields)."""

    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=1)
    query_type: str = Field(min_length=1)
    version: str = Field(pattern=_SEMVER_PATTERN)
    actor: ActorModel
    issued_at: str
    correlation_id: str = Field(min_length=1)
    target: ResourceModel | None = None
    parameters: dict[str, Any]


class DecisionView(BaseModel):
    """Serialisable projection of a policy decision returned with a result."""

    decision_id: str
    effect: str


class AuditView(BaseModel):
    """Serialisable projection of the tamper-evident audit record (``record_sha256``)."""

    evidence_id: str
    outcome: str
    record_sha256: str


class CommandResponse(BaseModel):
    """The success projection of a command result (or an idempotent replay)."""

    value: Any
    decision: DecisionView
    audit: AuditView
    replayed: bool
    correlation_id: str


class QueryResponse(BaseModel):
    """The success projection of a query result."""

    value: Any
    decision: DecisionView
    correlation_id: str


class VersionView(BaseModel):
    """Framework version / schema-compatibility projection (FR-KRN-006)."""

    framework_version: str
    contract_api: str
    schema_compatible: bool


class HealthResponse(BaseModel):
    """A single probe projection: ``status`` + explainable detail + version info."""

    status: str
    detail: str | None
    version: VersionView
