"""Query router: the thin inbound edge over the kernel query bus (LAW-04).

``POST /v1/queries/{query_type}`` validates the inbound envelope, builds the canonical
:class:`~northstar.kernel.context.RequestContext` and a
:class:`~northstar.kernel.messaging.Query`, then dispatches through the injected
:class:`~northstar.kernel.messaging.QueryBus`. Queries are side-effect-free and authorized on the
same deny-by-default boundary as commands (docs/05 §7); denials surface as ``problem+json``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Response

from northstar.kernel.context import Actor, ActorType, RequestContext, ResourceRef
from northstar.kernel.messaging import Query

from ..dependencies import AppDependencies, get_dependencies
from ..schemas import DecisionView, QueryEnvelope, QueryResponse

router = APIRouter(prefix="/v1/queries", tags=["queries"])


def _resource_of(envelope: QueryEnvelope) -> ResourceRef | None:
    if envelope.target is None:
        return None
    return ResourceRef(type=envelope.target.resource_type, id=envelope.target.resource_id)


@router.post("/{query_type}", response_model=QueryResponse)
def execute_query(
    envelope: QueryEnvelope,
    response: Response,
    query_type: Annotated[str, Path(min_length=1)],
    deps: Annotated[AppDependencies, Depends(get_dependencies)],
    x_correlation_id: Annotated[str | None, Header(alias="X-Correlation-Id")] = None,
) -> QueryResponse:
    actor = Actor(
        type=ActorType(envelope.actor.type),
        id=envelope.actor.id,
        delegated_by=envelope.actor.delegated_by,
    )
    context = RequestContext(
        actor=actor,
        correlation_id=x_correlation_id or envelope.correlation_id,
        tenant_scope=None,
    )
    query = Query(
        capability=query_type,
        version=envelope.version,
        parameters=envelope.parameters,
        resource=_resource_of(envelope),
    )
    result = deps.query_bus.dispatch(query, context)
    response.headers["X-Correlation-Id"] = context.correlation_id
    return QueryResponse(
        value=result.value,
        decision=DecisionView(
            decision_id=result.decision.decision_id,
            effect=result.decision.effect.value,
        ),
        correlation_id=context.correlation_id,
    )
