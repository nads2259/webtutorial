"""``/assistant`` FastAPI router — a thin adapter over the kernel command bus (LAW-04).

``POST /assistant/ask`` asks the configured model (grounded on curriculum content); ``GET
/assistant/models`` lists selectable models; ``GET/POST /assistant/config`` reads/sets the active
model (admin). Auth (session) is required for all routes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import uuid

from northstar.kernel.context import Actor, ActorType, RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus

from ..application.capabilities import CAP_ASK, CAP_VERSION, AskCommand
from ..application.config import AssistantModelStore
from ..domain.model import RES_ASSISTANT

_STATE_KEY = "northstar_assistant_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class AssistantApiDependencies:
    command_bus: CommandBus
    authenticate: Authenticator
    store: AssistantModelStore
    admin_lookup: Callable[[str], bool] = lambda _subject_id: False
    # When set, anonymous visitors may ask the tutor (scoped to this public tenant); None disables it.
    public_tenant: str | None = None


def bind_assistant_dependencies(app_state: object, deps: AssistantApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> AssistantApiDependencies:
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


def create_assistant_router() -> APIRouter:
    router = APIRouter(prefix="/assistant", tags=["assistant"])

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    @router.get("/models")
    def models(request: Request) -> JSONResponse:
        deps = _deps(request)
        active = deps.store.active().id
        return JSONResponse(
            status_code=200,
            content={
                "active": active,
                "models": [
                    {"id": m.id, "label": m.label, "kind": m.kind, "active": m.id == active}
                    for m in deps.store.models()
                ],
            },
        )

    @router.get("/config")
    def get_config(request: Request) -> JSONResponse:
        deps = _deps(request)
        return JSONResponse(status_code=200, content={"active": deps.store.active().id})

    @router.post("/config")
    async def set_config(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        if not deps.admin_lookup(context.actor.id):
            return _problem(403, "authorization.denied", "Admin access required", context.correlation_id)
        body = await _body(request)
        model_id = str(body.get("model_id", ""))
        if not deps.store.set_active(model_id):
            return _problem(422, "assistant.unknown_model", "Unknown model", context.correlation_id)
        return JSONResponse(status_code=200, content={"active": deps.store.active().id})

    @router.post("/ask")
    async def ask(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            # Anonymous visitors may ask when a public tenant is configured (grounded on public
            # content); the acting subject is the shared anonymous principal.
            if deps.public_tenant is None:
                return _problem(401, "authentication.required", "Authentication is required", "-")
            context = RequestContext(
                actor=Actor(type=ActorType.ANONYMOUS, id="anonymous"),
                correlation_id=f"cor_{uuid.uuid4().hex}",
                tenant_scope=deps.public_tenant,
            )
        body = await _body(request)
        command = Command(
            capability=CAP_ASK,
            version=CAP_VERSION,
            payload=AskCommand(
                question=str(body.get("question", "")),
                lesson_object_id=body.get("lesson_object_id"),
                model_id=body.get("model_id"),
                top_k=int(body.get("top_k", 5)),
            ),
            resource=ResourceRef(type=RES_ASSISTANT, id=context.tenant_scope or "-"),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "assistant.failed", str(exc), context.correlation_id)
        view = result.value
        return JSONResponse(
            status_code=200,
            content={
                "answer": view.answer,
                "model": view.model,
                "input_tokens": view.input_tokens,
                "output_tokens": view.output_tokens,
                "sources": [
                    {
                        "object_id": s.object_id,
                        "revision_id": s.revision_id,
                        "block_id": s.block_id,
                        "snippet": s.snippet,
                    }
                    for s in view.sources
                ],
            },
        )

    return router
