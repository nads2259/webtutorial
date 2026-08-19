"""``/codelab`` FastAPI router — a thin adapter over the kernel command/query buses (LAW-04).

The authenticated :class:`RequestContext` (including tenant scope + subject) is resolved by an
injected authenticator (server-side session), never from the request body (rule 50). Running code is
a command (audited); listing runs is a query. No business logic lives here.
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
    CAP_LIST_RUNS,
    CAP_RUN,
    CAP_VERSION,
    ListRunsQuery,
    RunCodeCommand,
)
from ..domain.model import RES_CODELAB

_STATE_KEY = "northstar_codelab_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class CodelabApiDependencies:
    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_codelab_dependencies(app_state: object, deps: CodelabApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> CodelabApiDependencies:
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


def _run_view(view: object) -> dict:
    return {
        "run_id": view.run_id,
        "language": view.language,
        "stdout": view.stdout,
        "stderr": view.stderr,
        "exit_code": view.exit_code,
        "duration_ms": view.duration_ms,
        "timed_out": view.timed_out,
        "truncated": view.truncated,
        "outcome": view.outcome,
        "record_sha256": view.record_sha256,
        "created_at": view.created_at,
        "lesson_id": view.lesson_id,
        "block_id": view.block_id,
    }


def create_codelab_router() -> APIRouter:
    router = APIRouter(prefix="/codelab", tags=["codelab"])

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    @router.post("/runs")
    async def run_code(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_RUN,
            version=CAP_VERSION,
            payload=RunCodeCommand(
                code=str(body.get("code", "")),
                language=str(body.get("language", "python")),
                lesson_id=body.get("lesson_id"),
                block_id=body.get("block_id"),
                stdin=str(body.get("stdin", "")),
            ),
            resource=ResourceRef(type=RES_CODELAB, id=context.tenant_scope or "-"),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "codelab.invalid", str(exc), context.correlation_id)
        return JSONResponse(status_code=201, content=_run_view(result.value))

    @router.get("/runs")
    def list_runs(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        limit = request.query_params.get("limit")
        try:
            limit_value = int(limit) if limit else 50
        except ValueError:
            limit_value = 50
        query = Query(
            capability=CAP_LIST_RUNS,
            version=CAP_VERSION,
            parameters=ListRunsQuery(limit=limit_value),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "codelab.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={"runs": [_run_view(r) for r in result.value.runs]},
        )

    return router
