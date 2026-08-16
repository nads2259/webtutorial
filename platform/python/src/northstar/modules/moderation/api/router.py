"""``/moderation`` FastAPI router (FR-ANN-007, EVAL-MOD-001).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), **never** from the request body or a client header (rule 50, tenant
isolation). Authorization denials (only a moderator may decide, only the affected author may
appeal) surface as ``403 application/problem+json``; typed domain errors as ``422``. No business
logic lives here.
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
    CAP_APPLY_ACTION,
    CAP_ASSIGN,
    CAP_DECIDE,
    CAP_GET_CASE,
    CAP_RESOLVE_APPEAL,
    CAP_SUBMIT_APPEAL,
    CAP_SUBMIT_REPORT,
    CAP_TRIAGE,
    CAP_VERSION,
    ApplyActionCommand,
    AssignCommand,
    DecideCommand,
    GetCaseQuery,
    ResolveAppealCommand,
    SubmitAppealCommand,
    SubmitReportCommand,
    TriageCommand,
)
from ..domain.model import RES_MODERATION_CASE

_STATE_KEY = "northstar_moderation_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class ModerationApiDependencies:
    """Collaborators the ``/moderation`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_moderation_dependencies(app_state: object, deps: ModerationApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> ModerationApiDependencies:
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


def _resource(resource_id: str) -> ResourceRef:
    return ResourceRef(type=RES_MODERATION_CASE, id=resource_id or "-")


def create_moderation_router() -> APIRouter:
    """Build the ``/moderation`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/moderation", tags=["moderation"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _dispatch_command(request: Request, command: Command) -> JSONResponse | object:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        try:
            return _deps(request).command_bus.dispatch(command, context).value
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "moderation.invalid", str(exc), context.correlation_id)

    @router.post("/reports")
    async def submit_report(request: Request) -> JSONResponse:
        body = await _body(request)
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_SUBMIT_REPORT,
                version=CAP_VERSION,
                payload=SubmitReportCommand(
                    content_type=str(body.get("content_type", "")),
                    content_id=str(body.get("content_id", "")),
                    reason=str(body.get("reason", "")),
                ),
                resource=_resource(str(body.get("content_id", ""))),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(
            status_code=201,
            content={
                "case_id": outcome.case_id,
                "state": outcome.state,
                "coalesced": outcome.coalesced,
                "report_count": outcome.report_count,
            },
        )

    @router.post("/cases/{case_id}/triage")
    async def triage(case_id: str, request: Request) -> JSONResponse:
        body = await _body(request)
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_TRIAGE,
                version=CAP_VERSION,
                payload=TriageCommand(case_id=case_id, note=body.get("note")),
                resource=_resource(case_id),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(
            status_code=200, content={"case_id": outcome.case_id, "state": outcome.state}
        )

    @router.post("/cases/{case_id}/assign")
    async def assign(case_id: str, request: Request) -> JSONResponse:
        body = await _body(request)
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_ASSIGN,
                version=CAP_VERSION,
                payload=AssignCommand(
                    case_id=case_id, assignee_id=str(body.get("assignee_id", ""))
                ),
                resource=_resource(case_id),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(
            status_code=200, content={"case_id": outcome.case_id, "state": outcome.state}
        )

    @router.post("/cases/{case_id}/decision")
    async def decide(case_id: str, request: Request) -> JSONResponse:
        body = await _body(request)
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_DECIDE,
                version=CAP_VERSION,
                payload=DecideCommand(
                    case_id=case_id,
                    disposition=str(body.get("disposition", "")),
                    rationale=str(body.get("rationale", "")),
                ),
                resource=_resource(case_id),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(
            status_code=200,
            content={
                "case_id": outcome.case_id,
                "state": outcome.state,
                "disposition": outcome.disposition,
                "enforcement_kind": outcome.enforcement_kind,
            },
        )

    @router.post("/cases/{case_id}/action")
    async def apply_action(case_id: str, request: Request) -> JSONResponse:
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_APPLY_ACTION,
                version=CAP_VERSION,
                payload=ApplyActionCommand(case_id=case_id),
                resource=_resource(case_id),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(
            status_code=200,
            content={
                "case_id": outcome.case_id,
                "state": outcome.state,
                "enforcement_kind": outcome.enforcement_kind,
                "applied": outcome.applied,
            },
        )

    @router.post("/cases/{case_id}/appeal")
    async def submit_appeal(case_id: str, request: Request) -> JSONResponse:
        body = await _body(request)
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_SUBMIT_APPEAL,
                version=CAP_VERSION,
                payload=SubmitAppealCommand(
                    case_id=case_id, rationale=str(body.get("rationale", ""))
                ),
                resource=_resource(case_id),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(
            status_code=200, content={"case_id": outcome.case_id, "state": outcome.state}
        )

    @router.post("/cases/{case_id}/appeal/resolve")
    async def resolve_appeal(case_id: str, request: Request) -> JSONResponse:
        body = await _body(request)
        outcome = _dispatch_command(
            request,
            Command(
                capability=CAP_RESOLVE_APPEAL,
                version=CAP_VERSION,
                payload=ResolveAppealCommand(
                    case_id=case_id,
                    resolution=str(body.get("resolution", "")),
                    rationale=body.get("rationale"),
                ),
                resource=_resource(case_id),
            ),
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return JSONResponse(
            status_code=200,
            content={
                "case_id": outcome.case_id,
                "state": outcome.state,
                "resolution": outcome.resolution,
                "enforcement_restored": outcome.enforcement_restored,
            },
        )

    @router.get("/cases/{case_id}")
    def get_case(case_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_GET_CASE,
            version=CAP_VERSION,
            parameters=GetCaseQuery(case_id=case_id),
            resource=_resource(case_id),
        )
        try:
            view = _deps(request).query_bus.dispatch(query, context).value
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(404, "moderation.not_found", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "case_id": view.case_id,
                "state": view.state,
                "content_type": view.content_type,
                "content_id": view.content_id,
                "author_id": view.author_id,
                "assignee_id": view.assignee_id,
                "report_count": view.report_count,
                "disposition": view.disposition,
                "enforcement_kind": view.enforcement_kind,
                "enforcement_reversed": view.enforcement_reversed,
                "appeal_resolution": view.appeal_resolution,
            },
        )

    return router
