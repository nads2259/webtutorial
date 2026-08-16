"""``/ai`` FastAPI router (docs/10, FR-AI-005/006/009).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), NEVER from the request body or a client header (rule 50). Routes dispatch
``ai.answer`` / ``ai.memory.*`` on the buses, which authorize deny-by-default before the capability
runs its governed pipeline. Policy denials surface as ``403 application/problem+json``; typed AI
governance/domain errors as ``422``. No business logic here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.kernel.context import RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus, Query, QueryBus

from ..application.capabilities import (
    CAP_ANSWER,
    CAP_FORGET,
    CAP_LIST_MEMORY,
    CAP_REMEMBER,
    CAP_VERSION,
    RES_AI,
    AnswerCommand,
    ForgetMemoryCommand,
    ListMemoryParameters,
    RememberMemoryCommand,
)
from ..domain.errors import AiGovernanceError

_STATE_KEY = "northstar_ai_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class AiApiDependencies:
    """Collaborators the ``/ai`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_ai_dependencies(app_state: object, deps: AiApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> AiApiDependencies:
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


def _resource(context: RequestContext) -> ResourceRef:
    return ResourceRef(type=RES_AI, id=context.tenant_scope or "-")


def create_ai_router() -> APIRouter:
    """Build the ``/ai`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/ai", tags=["ai"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    @router.post("/answer")
    async def answer(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_ANSWER,
            version=CAP_VERSION,
            payload=AnswerCommand(
                package_id=str(body.get("package_id", "")),
                version=str(body.get("package_version", "1.0.0")),
                question=str(body.get("question", "")),
                top_k=int(body.get("top_k", 5)),
                locale=str(body.get("locale", "en")),
                data_classification=str(body.get("data_classification", "public")),
                approvals=tuple(str(a) for a in body.get("approvals", [])),
            ),
            resource=_resource(context),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (AiGovernanceError, KernelError) as exc:
            return _problem(422, "ai.invalid", str(exc), context.correlation_id)
        view = result.value
        return JSONResponse(
            status_code=200,
            content={
                "answer": view.answer,
                "refused": view.refused,
                "provider": view.provider,
                "model": view.model,
                "prompt_package": view.prompt_package,
                "actor_profile": view.actor_profile,
                "eu_ai_act_tier": view.eu_ai_act_tier,
                "trace_id": view.trace_id,
                "executed_tools": list(view.executed_tools),
                "rejected_tools": [
                    {"tool_id": r.tool_id, "reason_code": r.reason_code}
                    for r in view.rejected_tools
                ],
                "citations": [
                    {
                        "object_id": c.object_id,
                        "revision_id": c.revision_id,
                        "block_id": c.block_id,
                        "chunk_id": c.chunk_id,
                        "claim": c.claim,
                    }
                    for c in view.citations
                ],
                "cost_units": view.cost_units,
            },
        )

    @router.post("/memory")
    async def remember(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_REMEMBER,
            version=CAP_VERSION,
            payload=RememberMemoryCommand(
                memory_class=str(body.get("memory_class", "episodic")),
                purpose=str(body.get("purpose", "")),
                classification=str(body.get("classification", "internal")),
                content=str(body.get("content", "")),
                retention=str(body.get("retention", "session")),
                inferred=bool(body.get("inferred", False)),
            ),
            resource=_resource(context),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (AiGovernanceError, KernelError) as exc:
            return _problem(422, "ai.invalid", str(exc), context.correlation_id)
        return JSONResponse(status_code=201, content={"memory_id": result.value.memory_id})

    @router.delete("/memory/{memory_id}")
    async def forget(request: Request, memory_id: str) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        command = Command(
            capability=CAP_FORGET,
            version=CAP_VERSION,
            payload=ForgetMemoryCommand(memory_id=memory_id),
            resource=_resource(context),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (AiGovernanceError, KernelError) as exc:
            return _problem(422, "ai.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={"memory_id": result.value.memory_id, "deleted": result.value.deleted},
        )

    @router.get("/memory")
    async def list_memory(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_LIST_MEMORY,
            version=CAP_VERSION,
            parameters=ListMemoryParameters(),
            resource=_resource(context),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (AiGovernanceError, KernelError) as exc:
            return _problem(422, "ai.invalid", str(exc), context.correlation_id)
        view = result.value
        return JSONResponse(
            status_code=200,
            content={
                "records": [
                    {
                        "memory_id": r.memory_id,
                        "memory_class": r.memory_class,
                        "purpose": r.purpose,
                        "classification": r.classification,
                        "content": r.content,
                        "inferred": r.inferred,
                    }
                    for r in view.records
                ]
            },
        )

    return router
