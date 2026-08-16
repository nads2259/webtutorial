"""``/annotations`` FastAPI router (FR-ANN-001..006).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), **never** from the request body or a client header (rule 50, tenant
isolation). Policy denials surface as ``403 application/problem+json``; typed domain errors as
``422``. No business logic lives here.
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
    CAP_CREATE_ANNOTATION,
    CAP_LIST_FOR_TARGET,
    CAP_MODERATE,
    CAP_REMAP,
    CAP_REPLY,
    CAP_SET_VISIBILITY,
    CAP_VERSION,
    CreateAnnotationCommand,
    ListForTargetQuery,
    ModerateCommand,
    RemapOnNewRevisionCommand,
    ReplyCommand,
    SetVisibilityCommand,
)
from ..domain.model import RES_ANNOTATION

_STATE_KEY = "northstar_annotation_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class AnnotationApiDependencies:
    """Collaborators the ``/annotations`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_annotation_dependencies(app_state: object, deps: AnnotationApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> AnnotationApiDependencies:
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
    return ResourceRef(type=RES_ANNOTATION, id=resource_id)


def create_annotation_router() -> APIRouter:
    """Build the ``/annotations`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/annotations", tags=["annotation"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    @router.post("")
    async def create_annotation(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_CREATE_ANNOTATION,
            version=CAP_VERSION,
            payload=CreateAnnotationCommand(
                object_id=str(body.get("object_id", "")),
                revision_id=str(body.get("revision_id", "")),
                selectors=tuple(body.get("selectors") or ()),
                motivation=str(body.get("motivation", "")),
                visibility=str(body.get("visibility", "")),
                body_type=str(body.get("body_type", "text")),
                body_content=body.get("body_content"),
                body_locale=body.get("body_locale"),
                audience_ids=tuple(body.get("audience_ids") or ()),
            ),
            resource=_resource(str(body.get("object_id", ""))),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "annotation.invalid", str(exc), context.correlation_id)
        created = result.value
        return JSONResponse(
            status_code=201,
            content={
                "annotation_id": created.annotation_id,
                "thread_id": created.thread_id,
                "state": created.state,
            },
        )

    @router.post("/{annotation_id}/replies")
    async def reply(annotation_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_REPLY,
            version=CAP_VERSION,
            payload=ReplyCommand(
                parent_annotation_id=annotation_id,
                motivation=str(body.get("motivation", "commenting")),
                visibility=str(body.get("visibility", "")),
                body_type=str(body.get("body_type", "text")),
                body_content=body.get("body_content"),
                body_locale=body.get("body_locale"),
                audience_ids=tuple(body.get("audience_ids") or ()),
            ),
            resource=_resource(annotation_id),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "annotation.invalid", str(exc), context.correlation_id)
        created = result.value
        return JSONResponse(
            status_code=201,
            content={
                "annotation_id": created.annotation_id,
                "thread_id": created.thread_id,
                "parent_annotation_id": created.parent_annotation_id,
            },
        )

    @router.post("/{annotation_id}/visibility")
    async def set_visibility(annotation_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        audience = body.get("audience_ids")
        command = Command(
            capability=CAP_SET_VISIBILITY,
            version=CAP_VERSION,
            payload=SetVisibilityCommand(
                annotation_id=annotation_id,
                visibility=str(body.get("visibility", "")),
                audience_ids=tuple(audience) if audience is not None else None,
            ),
            resource=_resource(annotation_id),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "annotation.invalid", str(exc), context.correlation_id)
        updated = result.value
        return JSONResponse(
            status_code=200,
            content={"annotation_id": updated.annotation_id, "visibility": updated.visibility},
        )

    @router.post("/{annotation_id}/moderation")
    async def moderate(annotation_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_MODERATE,
            version=CAP_VERSION,
            payload=ModerateCommand(
                annotation_id=annotation_id,
                kind=str(body.get("kind", "")),
                reason=body.get("reason"),
            ),
            resource=_resource(annotation_id),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "annotation.invalid", str(exc), context.correlation_id)
        moderated = result.value
        return JSONResponse(
            status_code=200,
            content={
                "annotation_id": moderated.annotation_id,
                "moderation_id": moderated.moderation_id,
                "state": moderated.state,
            },
        )

    @router.post("/{annotation_id}/remap")
    async def remap(annotation_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_REMAP,
            version=CAP_VERSION,
            payload=RemapOnNewRevisionCommand(
                annotation_id=annotation_id,
                new_revision_id=str(body.get("new_revision_id", "")),
            ),
            resource=_resource(annotation_id),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "annotation.invalid", str(exc), context.correlation_id)
        remapped = result.value
        return JSONResponse(
            status_code=200,
            content={
                "annotation_id": remapped.annotation_id,
                "strategy": remapped.strategy,
                "confidence": remapped.confidence,
                "mapped": remapped.mapped,
                "state": remapped.state,
                "current_revision_id": remapped.current_revision_id,
                "review_reason": remapped.review_reason,
            },
        )

    @router.get("/target/{object_id}")
    def list_for_target(object_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_LIST_FOR_TARGET,
            version=CAP_VERSION,
            parameters=ListForTargetQuery(object_id=object_id),
            resource=_resource(object_id),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(404, "annotation.not_found", str(exc), context.correlation_id)
        listed = result.value
        return JSONResponse(
            status_code=200,
            content={
                "annotations": [
                    {
                        "annotation_id": view.annotation_id,
                        "motivation": view.motivation,
                        "visibility": view.visibility,
                        "state": view.state,
                        "object_id": view.object_id,
                        "source_revision_id": view.source_revision_id,
                        "current_revision_id": view.current_revision_id,
                        "thread_id": view.thread_id,
                        "parent_annotation_id": view.parent_annotation_id,
                    }
                    for view in listed.annotations
                ],
                "threads": [
                    {
                        "thread_id": thread.thread_id,
                        "root_annotation_id": thread.root_annotation_id,
                        "annotation_ids": list(thread.annotation_ids),
                    }
                    for thread in listed.threads
                ],
            },
        )

    return router
