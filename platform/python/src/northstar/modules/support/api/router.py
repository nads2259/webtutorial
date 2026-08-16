"""``/support`` FastAPI router (docs/29 §6, FR-SUP-001..003).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope and acting subject — is resolved by an injected
authenticator (server-side session), NEVER from the request body or a client header (rule 50).
Routes dispatch the support capabilities on the bus, which authorize deny-by-default before the
capability runs. Policy/access denials surface as ``403 application/problem+json``; typed domain
rejections (intake validation, invalid transition) as ``422``. No business logic lives here.
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
    CAP_ACCESS_GRANT,
    CAP_ACCESS_REVOKE,
    CAP_ASSIGN,
    CAP_INTAKE,
    CAP_REPLY,
    CAP_TRANSITION,
    CAP_VERSION,
    CAP_VIEW,
    AssignCaseCommand,
    GrantAccessCommand,
    ReplyCommand,
    RevokeAccessCommand,
    SubmitIntakeCommand,
    TransitionCaseCommand,
    ViewCaseQuery,
)
from ..domain.errors import SupportAccessDenied, SupportError
from ..domain.model import RES_SUPPORT_CASE

_STATE_KEY = "northstar_support_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], "RequestContext | None"]


@dataclass(frozen=True, slots=True)
class SupportApiDependencies:
    """Collaborators the ``/support`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_support_dependencies(app_state: object, deps: SupportApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> SupportApiDependencies:
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


def _support_resource(context: RequestContext) -> ResourceRef:
    return ResourceRef(type=RES_SUPPORT_CASE, id=context.tenant_scope or "-")


def create_support_router() -> APIRouter:
    """Build the ``/support`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/support", tags=["support"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _dispatch(request: Request, context: RequestContext, cap: str, payload: object) -> object:
        command = Command(
            capability=cap,
            version=CAP_VERSION,
            payload=payload,
            resource=_support_resource(context),
        )
        return _deps(request).command_bus.dispatch(command, context)

    @router.post("/cases")
    async def submit_intake(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_INTAKE,
                SubmitIntakeCommand(
                    subject=str(body.get("subject", "")),
                    category=str(body.get("category", "")),
                    body=str(body.get("body", "")),
                    priority=str(body.get("priority", "normal")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SupportError, KernelError) as exc:
            return _problem(422, "support.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=201,
            content={
                "case_id": value.case_id,
                "status": value.status,
                "priority": value.priority,
            },
        )

    @router.post("/cases/{case_id}/assign")
    async def assign_case(case_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_ASSIGN,
                AssignCaseCommand(case_id=case_id, assignee_id=str(body.get("assignee_id", ""))),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SupportError, KernelError) as exc:
            return _problem(422, "support.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=200,
            content={
                "case_id": value.case_id,
                "status": value.status,
                "assignee_id": value.assignee_id,
            },
        )

    @router.post("/cases/{case_id}/transition")
    async def transition_case(case_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_TRANSITION,
                TransitionCaseCommand(case_id=case_id, to_status=str(body.get("to_status", ""))),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SupportError, KernelError) as exc:
            return _problem(422, "support.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=200, content={"case_id": value.case_id, "status": value.status}
        )

    @router.post("/cases/{case_id}/reply")
    async def reply_case(case_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_REPLY,
                ReplyCommand(
                    case_id=case_id,
                    body=str(body.get("body", "")),
                    visibility=str(body.get("visibility", "requester")),
                    author_type=str(body.get("author_type", "agent")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SupportError, KernelError) as exc:
            return _problem(422, "support.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=201,
            content={
                "case_id": value.case_id,
                "message_id": value.message_id,
                "visibility": value.visibility,
            },
        )

    @router.get("/cases/{case_id}")
    def view_case(case_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        elevated = request.query_params.get("elevated", "false").lower() in {"1", "true", "yes"}
        query = Query(
            capability=CAP_VIEW,
            version=CAP_VERSION,
            parameters=ViewCaseQuery(case_id=case_id, elevated=elevated),
            resource=_support_resource(context),
        )
        try:
            result = _deps(request).query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except SupportAccessDenied as exc:
            return _problem(403, "support.access.denied", str(exc), context.correlation_id)
        except (SupportError, KernelError) as exc:
            return _problem(422, "support.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=200,
            content={"case_id": value.case_id, "minimized": value.minimized, "view": value.view},
        )

    @router.post("/access/grants")
    async def grant_access(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch(
                request,
                context,
                CAP_ACCESS_GRANT,
                GrantAccessCommand(
                    case_id=str(body.get("case_id", "")),
                    staff_id=str(body.get("staff_id", "")),
                    reason=str(body.get("reason", "")),
                    ttl_seconds=int(body.get("ttl_seconds", 3600)),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SupportError, KernelError) as exc:
            return _problem(422, "support.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=201,
            content={
                "grant_id": value.grant_id,
                "case_id": value.case_id,
                "staff_id": value.staff_id,
                "expires_at": value.expires_at,
            },
        )

    @router.post("/access/grants/{grant_id}/revoke")
    async def revoke_access(grant_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        try:
            result = _dispatch(
                request, context, CAP_ACCESS_REVOKE, RevokeAccessCommand(grant_id=grant_id)
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (SupportError, KernelError) as exc:
            return _problem(422, "support.rejected", str(exc), context.correlation_id)
        value = result.value
        return JSONResponse(
            status_code=200, content={"grant_id": value.grant_id, "revoked": value.revoked}
        )

    return router
