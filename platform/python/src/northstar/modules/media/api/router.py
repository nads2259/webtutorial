"""``/media`` FastAPI router (FR-CNT-009/010, NFR-A11Y-003, NFR-SEC-004).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), **never** from the request body or a client header (rule 50). A refused
upload (the H02 :class:`UploadRejected`) is mapped to the canonical RFC 9457 ``422`` problem via
:func:`northstar.adapters.upload.upload_rejected_problem` — this closes the H02 follow-up note that
a media route surfacing upload errors must reuse the shared mapper. Policy denials surface as
``403``; typed domain errors (incl. the accessibility gate) as ``422``. No business logic here.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.adapters.upload import upload_rejected_problem
from northstar.kernel.context import RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus, Query, QueryBus
from northstar.kernel.security.upload import UploadRejected

from ..application.capabilities import (
    CAP_ATTACH_ALT,
    CAP_ATTACH_CAPTIONS,
    CAP_ATTACH_TRANSCRIPT,
    CAP_GET,
    CAP_PUBLISH,
    CAP_RESOLVE_TIME,
    CAP_UPLOAD,
    CAP_VERSION,
    AttachAltTextCommand,
    AttachCaptionsCommand,
    AttachTranscriptCommand,
    GetMediaQuery,
    PublishMediaCommand,
    ResolveTimeSelectorQuery,
    UploadMediaCommand,
)
from ..domain.model import RES_MEDIA

_STATE_KEY = "northstar_media_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class MediaApiDependencies:
    """Collaborators the ``/media`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_media_dependencies(app_state: object, deps: MediaApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> MediaApiDependencies:
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


def _upload_problem(err: UploadRejected, correlation_id: str) -> JSONResponse:
    """Map a refused upload to the shared RFC 9457 problem (H02 follow-up closed)."""
    problem = upload_rejected_problem(err, correlation_id=correlation_id)
    return JSONResponse(
        status_code=problem.status, media_type=_PROBLEM_CONTENT_TYPE, content=problem.to_body()
    )


def _resource(resource_id: str) -> ResourceRef:
    return ResourceRef(type=RES_MEDIA, id=resource_id or "-")


def create_media_router() -> APIRouter:
    """Build the ``/media`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/media", tags=["media"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    @router.post("")
    async def upload_media(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            data = base64.b64decode(str(body.get("data_base64", "")), validate=True)
        except Exception:
            return _problem(
                422, "validation.failed", "data_base64 is not valid base64", context.correlation_id
            )
        command = Command(
            capability=CAP_UPLOAD,
            version=CAP_VERSION,
            payload=UploadMediaCommand(
                media_type=str(body.get("media_type", "")),
                filename=str(body.get("filename", "asset")),
                declared_content_type=str(body.get("content_type", "")),
                data=data,
                title=body.get("title"),
            ),
            resource=_resource(str(body.get("media_type", ""))),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except UploadRejected as err:
            return _upload_problem(err, context.correlation_id)
        except KernelError as exc:
            return _problem(422, "media.invalid", str(exc), context.correlation_id)
        created = result.value
        return JSONResponse(
            status_code=201,
            content={
                "asset_id": created.asset_id,
                "media_type": created.media_type,
                "content_type": created.content_type,
                "blob_ref": created.blob_ref,
                "byte_size": created.byte_size,
                "state": created.state,
            },
        )

    @router.post("/{asset_id}/transcript")
    async def attach_transcript(asset_id: str, request: Request) -> JSONResponse:
        return await _dispatch_mutation(
            request,
            asset_id,
            CAP_ATTACH_TRANSCRIPT,
            lambda body: AttachTranscriptCommand(
                asset_id=asset_id,
                language=str(body.get("language", "")),
                segments=tuple(body.get("segments") or ()),
            ),
        )

    @router.post("/{asset_id}/captions")
    async def attach_captions(asset_id: str, request: Request) -> JSONResponse:
        return await _dispatch_mutation(
            request,
            asset_id,
            CAP_ATTACH_CAPTIONS,
            lambda body: AttachCaptionsCommand(
                asset_id=asset_id,
                language=str(body.get("language", "")),
                cues=tuple(body.get("cues") or ()),
                label=body.get("label"),
            ),
        )

    @router.post("/{asset_id}/alt-text")
    async def attach_alt(asset_id: str, request: Request) -> JSONResponse:
        return await _dispatch_mutation(
            request,
            asset_id,
            CAP_ATTACH_ALT,
            lambda body: AttachAltTextCommand(
                asset_id=asset_id,
                text=body.get("text"),
                decorative=bool(body.get("decorative", False)),
            ),
        )

    async def _dispatch_mutation(
        request: Request,
        asset_id: str,
        capability: str,
        build: Callable[[dict], object],
    ) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=capability,
            version=CAP_VERSION,
            payload=build(body),
            resource=_resource(asset_id),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "media.invalid", str(exc), context.correlation_id)
        updated = result.value
        return JSONResponse(
            status_code=200,
            content={
                "asset_id": updated.asset_id,
                "state": updated.state,
                "missing_accessibility": list(updated.missing_accessibility),
            },
        )

    @router.post("/{asset_id}/publish")
    async def publish_media(asset_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        command = Command(
            capability=CAP_PUBLISH,
            version=CAP_VERSION,
            payload=PublishMediaCommand(asset_id=asset_id),
            resource=_resource(asset_id),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "media.accessibility.required", str(exc), context.correlation_id)
        published = result.value
        return JSONResponse(
            status_code=200,
            content={"asset_id": published.asset_id, "state": published.state},
        )

    @router.get("/{asset_id}")
    def get_media(asset_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_GET,
            version=CAP_VERSION,
            parameters=GetMediaQuery(asset_id=asset_id),
            resource=_resource(asset_id),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(404, "media.not_found", str(exc), context.correlation_id)
        view = result.value
        return JSONResponse(
            status_code=200,
            content={
                "asset_id": view.asset_id,
                "media_type": view.media_type,
                "content_type": view.content_type,
                "blob_ref": view.blob_ref,
                "byte_size": view.byte_size,
                "state": view.state,
                "title": view.title,
                "accessibility": view.accessibility,
                "time_fragments": list(view.time_fragments),
            },
        )

    @router.get("/{asset_id}/time-selectors")
    def resolve_time(asset_id: str, request: Request, at: float = 0.0) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_RESOLVE_TIME,
            version=CAP_VERSION,
            parameters=ResolveTimeSelectorQuery(asset_id=asset_id, at=at),
            resource=_resource(asset_id),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(404, "media.not_found", str(exc), context.correlation_id)
        resolved = result.value
        return JSONResponse(status_code=200, content=resolved.resolution)

    return router
