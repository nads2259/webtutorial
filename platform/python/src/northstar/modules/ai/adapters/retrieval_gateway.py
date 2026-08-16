"""Retrieval gateway adapters implementing ``RetrievalPort`` (FR-AI-005, ARCH-009).

The AI module reaches retrieval ONLY through its published ``retrieval.search`` capability — never
by touching retrieval's tables (no cross-module DB access, LAW-13). Two adapters:

* :class:`BusRetrievalGateway` dispatches ``retrieval.search`` on the kernel query bus, so the call
  is authorized deny-by-default (authorize-before-retrieval) and retrieval applies the tenant/
  visibility ACL INSIDE the query and re-checks before returning any passage (zero leakage).
* :class:`InMemoryRetrievalGateway` drives the same retrieval ``Search`` handler with an in-memory
  repository for fast, deterministic unit/red-team tests (still ACL-enforced by retrieval).

Both map retrieval's identity-bearing results to the AI domain :class:`PassageRef`.
"""

from __future__ import annotations

from dataclasses import dataclass

from northstar.kernel.context import Actor, ActorType, RequestContext, ResourceRef
from northstar.kernel.messaging import Query, QueryBus
from northstar.modules.retrieval.application import capabilities as retrieval

from ..domain.model import PassageRef


def _to_passages(view: object) -> tuple[PassageRef, ...]:
    results = getattr(view, "results", ())
    return tuple(
        PassageRef(
            object_id=item.object_id,
            revision_id=item.revision_id,
            block_id=item.block_id,
            chunk_id=item.chunk_id,
            text=item.text,
            score=item.score,
        )
        for item in results
    )


def _context(*, organization_id: str, subject_id: str, correlation_id: str) -> RequestContext:
    # The AI actor operates with the delegated subject's scope so retrieval's ACL resolves the
    # caller's authorized (public/organization/own-private) passages — never another tenant's.
    return RequestContext(
        actor=Actor(type=ActorType.AI_ACTOR, id=subject_id, delegated_by=subject_id),
        correlation_id=correlation_id,
        tenant_scope=organization_id,
    )


class BusRetrievalGateway:
    """Retrieves via the kernel query bus (authorized + ACL-in-query, production wiring)."""

    def __init__(self, *, query_bus: QueryBus) -> None:
        self._query_bus = query_bus

    def search(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        text: str,
        top_k: int,
        locale: str,
    ) -> tuple[PassageRef, ...]:
        context = _context(
            organization_id=organization_id,
            subject_id=subject_id,
            correlation_id=correlation_id,
        )
        query = Query(
            capability=retrieval.CAP_SEARCH,
            version=retrieval.CAP_VERSION,
            parameters=retrieval.SearchParameters(text=text, top_k=top_k, locale=locale),
            resource=ResourceRef(type=retrieval.RES_CORPUS, id=organization_id),
        )
        result = self._query_bus.dispatch(query, context)
        return _to_passages(result.value)


@dataclass(frozen=True, slots=True)
class _Invocation:
    context: RequestContext
    parameters: object


class InMemoryRetrievalGateway:
    """Drives the retrieval ``Search`` handler directly for tests (retrieval still enforces ACL)."""

    def __init__(self, *, search: retrieval.Search) -> None:
        self._search = search

    def search(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        text: str,
        top_k: int,
        locale: str,
    ) -> tuple[PassageRef, ...]:
        context = _context(
            organization_id=organization_id,
            subject_id=subject_id,
            correlation_id=correlation_id,
        )
        view = self._search.handle(
            _Invocation(
                context=context,
                parameters=retrieval.SearchParameters(text=text, top_k=top_k, locale=locale),
            )
        )
        return _to_passages(view)
