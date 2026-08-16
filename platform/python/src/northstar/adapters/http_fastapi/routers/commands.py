"""Command execution router: the thin inbound edge over the kernel command bus (LAW-04).

``POST /v1/commands/{command_type}`` validates the inbound envelope, builds a canonical
:class:`~northstar.kernel.context.RequestContext` (actor from the envelope; ``correlation_id`` and
``idempotency_key`` preferring the transport headers per docs/05 §5) and a
:class:`~northstar.kernel.messaging.Command`, then dispatches through the injected
:class:`~northstar.kernel.messaging.CommandBus`. It holds NO business logic: authorization, audit
and idempotency all live in the bus/kernel; typed kernel errors surface as ``problem+json``.

``{command_type}`` is the capability coordinate the kernel dispatches on (dotted capability name);
``version`` in the envelope selects the capability version. The envelope's own PascalCase
``command_type`` field is the logical command name carried for provenance.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Response

from northstar.kernel.context import Actor, ActorType, RequestContext, ResourceRef
from northstar.kernel.messaging import Command

from ..dependencies import AppDependencies, get_dependencies
from ..schemas import (
    AuditView,
    CommandEnvelope,
    CommandResponse,
    DecisionView,
)

router = APIRouter(prefix="/v1/commands", tags=["commands"])


def _resource_of(envelope: CommandEnvelope) -> ResourceRef | None:
    if envelope.target is None:
        return None
    return ResourceRef(type=envelope.target.resource_type, id=envelope.target.resource_id)


def _context_of(
    envelope: CommandEnvelope,
    *,
    correlation_header: str | None,
    idempotency_header: str | None,
) -> RequestContext:
    actor = Actor(
        type=ActorType(envelope.actor.type),
        id=envelope.actor.id,
        delegated_by=envelope.actor.delegated_by,
    )
    # Transport headers win over the envelope body (docs/05 §5). Tenant scope is intentionally
    # NOT taken from the payload (rule 50); it is derived from the authenticated context once
    # authentication lands (IMPL-007), so it stays None at this pre-auth edge.
    return RequestContext(
        actor=actor,
        correlation_id=correlation_header or envelope.correlation_id,
        idempotency_key=idempotency_header or envelope.idempotency_key,
        tenant_scope=None,
    )


@router.post("/{command_type}", response_model=CommandResponse)
def execute_command(
    envelope: CommandEnvelope,
    response: Response,
    command_type: Annotated[str, Path(min_length=1)],
    deps: Annotated[AppDependencies, Depends(get_dependencies)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
) -> CommandResponse:
    context = _context_of(
        envelope,
        correlation_header=x_correlation_id,
        idempotency_header=idempotency_key,
    )
    command = Command(
        capability=command_type,
        version=envelope.version,
        payload=envelope.payload,
        resource=_resource_of(envelope),
    )
    result = deps.command_bus.dispatch(command, context)
    response.headers["X-Correlation-Id"] = context.correlation_id
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return CommandResponse(
        value=result.value,
        decision=DecisionView(
            decision_id=result.decision.decision_id,
            effect=result.decision.effect.value,
        ),
        audit=AuditView(
            evidence_id=result.audit.evidence_id,
            outcome=result.audit.outcome.value,
            record_sha256=result.audit.record_sha256,
        ),
        replayed=result.replayed,
        correlation_id=context.correlation_id,
    )
