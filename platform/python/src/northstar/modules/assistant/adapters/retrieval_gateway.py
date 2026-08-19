"""Retrieval gateway: grounds assistant questions via the released ``retrieval.search`` (LAW-13).

The assistant never reads another module's tables; it dispatches ``retrieval.search`` on the
authorized query bus with a context derived from the request, so ACL/tenant isolation is enforced by
the retrieval capability itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from northstar.kernel.context import Actor, ActorType, RequestContext
from northstar.kernel.messaging import Query, QueryBus
from northstar.modules.retrieval.application import capabilities as retrieval

from ..domain.model import RetrievedPassage


class BusRetrievalGateway:
    """Adapts the query bus to the assistant's :class:`RetrievalGatewayPort`."""

    def __init__(self, *, query_bus: QueryBus) -> None:
        self._bus = query_bus

    def search(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        text: str,
        top_k: int,
    ) -> Sequence[RetrievedPassage]:
        context = RequestContext(
            actor=Actor(type=ActorType.USER, id=subject_id),
            correlation_id=correlation_id,
            tenant_scope=organization_id,
        )
        query = Query(
            capability=retrieval.CAP_SEARCH,
            version=retrieval.CAP_VERSION,
            parameters=retrieval.SearchParameters(text=text, top_k=top_k, locale="en"),
        )
        try:
            result = self._bus.dispatch(query, context).value
        except Exception:  # noqa: BLE001 - grounding is best-effort; the model still answers
            return ()
        return tuple(
            RetrievedPassage(
                object_id=r.object_id,
                revision_id=r.revision_id,
                block_id=r.block_id,
                text=r.text,
                score=r.score,
            )
            for r in result.results
        )
