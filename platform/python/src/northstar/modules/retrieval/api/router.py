"""``/retrieval`` FastAPI router (docs/06 §7, FR-RET-002/006/007).

A thin inbound adapter over the kernel query bus (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope used for the ACL — is resolved by an injected
authenticator (server-side session), NEVER from the request body or a client header (rule 50). The
route dispatches ``retrieval.search`` on the query bus, which authorizes deny-by-default before the
capability applies the ACL inside the query and re-checks before returning results. Policy denials
surface as ``403 application/problem+json``; typed domain errors as ``422``. No business logic here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.kernel.context import RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Query, QueryBus

from ..application.capabilities import (
    CAP_SEARCH,
    CAP_VERSION,
    RES_CORPUS,
    SearchParameters,
)

_STATE_KEY = "northstar_retrieval_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class RetrievalApiDependencies:
    """Collaborators the ``/retrieval`` router needs, injected at the composition root."""

    query_bus: QueryBus
    authenticate: Authenticator


def bind_retrieval_dependencies(app_state: object, deps: RetrievalApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> RetrievalApiDependencies:
    return getattr(request.app.state, _STATE_KEY)


def _problem(status: int, code: str, detail: str, correlation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type=_PROBLEM_CONTENT_TYPE,
        content={
            "type": f"https://errors.northstar.example/{code.replace('.', '/')}",
            "title": detail,
            "status": status,
            "detail": detail,
            "code": code,
            "correlation_id": correlation_id,
            "retryable": False,
        },
    )


def create_retrieval_router() -> APIRouter:
    """Build the ``/retrieval`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/retrieval", tags=["retrieval"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    @router.post("/search")
    async def search(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        query = Query(
            capability=CAP_SEARCH,
            version=CAP_VERSION,
            parameters=SearchParameters(
                text=str(body.get("q", body.get("text", ""))),
                top_k=int(body.get("top_k", 10)),
                locale=str(body.get("locale", "en")),
            ),
            resource=ResourceRef(type=RES_CORPUS, id=context.tenant_scope or "-"),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "retrieval.invalid", str(exc), context.correlation_id)
        view = result.value
        return JSONResponse(
            status_code=200,
            content={
                "query": view.query,
                "profile_id": view.profile_id,
                "profile_version": view.profile_version,
                "results": [
                    {
                        "object_id": passage.object_id,
                        "revision_id": passage.revision_id,
                        "block_id": passage.block_id,
                        "ordinal": passage.ordinal,
                        "chunk_id": passage.chunk_id,
                        "text": passage.text,
                        "score": passage.score,
                        "lexical_rank": passage.lexical_rank,
                        "semantic_rank": passage.semantic_rank,
                    }
                    for passage in view.results
                ],
            },
        )

    return router
