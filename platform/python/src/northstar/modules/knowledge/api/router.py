"""``/knowledge`` FastAPI router (docs/06, FR-CNT-001..007).

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
    CAP_ASSIGN_TAXONOMY,
    CAP_CREATE_DOCUMENT,
    CAP_EDIT_DRAFT,
    CAP_GET_DOCUMENT,
    CAP_GET_REVISION,
    CAP_PUBLISH_DOCUMENT,
    CAP_SUBMIT_FOR_REVIEW,
    CAP_VERSION,
    AssignTaxonomyCommand,
    CreateDocumentCommand,
    EditDraftCommand,
    GetDocumentQuery,
    GetRevisionQuery,
    PublishDocumentCommand,
    SubmitForReviewCommand,
)
from ..domain.model import RES_DOCUMENT

_STATE_KEY = "northstar_knowledge_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], RequestContext | None]


@dataclass(frozen=True, slots=True)
class KnowledgeApiDependencies:
    """Collaborators the ``/knowledge`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_knowledge_dependencies(app_state: object, deps: KnowledgeApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> KnowledgeApiDependencies:
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


def _doc_resource(object_id: str) -> ResourceRef:
    return ResourceRef(type=RES_DOCUMENT, id=object_id)


def create_knowledge_router() -> APIRouter:
    """Build the ``/knowledge`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/knowledge", tags=["knowledge"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    @router.post("")
    async def create_document(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_CREATE_DOCUMENT,
            version=CAP_VERSION,
            payload=CreateDocumentCommand(
                document_type=str(body.get("document_type", "")),
                locale=str(body.get("locale", "")),
                title=str(body.get("title", "")),
                blocks=tuple(body.get("blocks") or ()),
                summary=body.get("summary"),
            ),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "knowledge.invalid", str(exc), context.correlation_id)
        created = result.value
        return JSONResponse(
            status_code=201,
            content={
                "object_id": created.object_id,
                "draft_id": created.draft_id,
                "organization_id": created.organization_id,
            },
        )

    @router.post("/{object_id}/draft")
    async def edit_draft(object_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_EDIT_DRAFT,
            version=CAP_VERSION,
            payload=EditDraftCommand(object_id=object_id, blocks=tuple(body.get("blocks") or ())),
            resource=_doc_resource(object_id),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "knowledge.invalid", str(exc), context.correlation_id)
        edited = result.value
        return JSONResponse(
            status_code=200,
            content={"object_id": edited.object_id, "version": edited.version},
        )

    @router.post("/{object_id}/submit")
    async def submit_for_review(object_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        command = Command(
            capability=CAP_SUBMIT_FOR_REVIEW,
            version=CAP_VERSION,
            payload=SubmitForReviewCommand(object_id=object_id),
            resource=_doc_resource(object_id),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "knowledge.invalid", str(exc), context.correlation_id)
        submitted = result.value
        return JSONResponse(
            status_code=200,
            content={"object_id": submitted.object_id, "lifecycle": submitted.lifecycle},
        )

    @router.post("/{object_id}/publish")
    async def publish_document(object_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_PUBLISH_DOCUMENT,
            version=CAP_VERSION,
            payload=PublishDocumentCommand(
                object_id=object_id,
                title=str(body.get("title", "")),
                channel=str(body.get("channel", "default")),
                visibility=str(body.get("visibility", "organization")),
                summary=body.get("summary"),
            ),
            resource=_doc_resource(object_id),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "knowledge.invalid", str(exc), context.correlation_id)
        published = result.value
        return JSONResponse(
            status_code=201,
            content={
                "object_id": published.object_id,
                "revision_id": published.revision_id,
                "parent_revision_id": published.parent_revision_id,
                "content_hash": published.content_hash,
            },
        )

    @router.post("/{object_id}/taxonomy")
    async def assign_taxonomy(object_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        command = Command(
            capability=CAP_ASSIGN_TAXONOMY,
            version=CAP_VERSION,
            payload=AssignTaxonomyCommand(
                object_id=object_id,
                scheme=str(body.get("scheme", "")),
                term=str(body.get("term", "")),
            ),
            resource=_doc_resource(object_id),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "knowledge.invalid", str(exc), context.correlation_id)
        assigned = result.value
        return JSONResponse(
            status_code=201,
            content={
                "object_id": assigned.object_id,
                "scheme": assigned.scheme,
                "term": assigned.term,
            },
        )

    @router.get("/revisions/{revision_id}")
    def get_revision(revision_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_GET_REVISION,
            version=CAP_VERSION,
            parameters=GetRevisionQuery(revision_id=revision_id),
            resource=_doc_resource(revision_id),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(404, "knowledge.not_found", str(exc), context.correlation_id)
        view = result.value
        return JSONResponse(
            status_code=200,
            content={
                "revision_id": view.revision_id,
                "object_id": view.object_id,
                "parent_revision_id": view.parent_revision_id,
                "title": view.title,
                "content_hash": view.content_hash,
                "blocks": list(view.blocks),
            },
        )

    @router.get("/{object_id}")
    def get_document(object_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_GET_DOCUMENT,
            version=CAP_VERSION,
            parameters=GetDocumentQuery(object_id=object_id),
            resource=_doc_resource(object_id),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(404, "knowledge.not_found", str(exc), context.correlation_id)
        view = result.value
        return JSONResponse(
            status_code=200,
            content={
                "object_id": view.object_id,
                "document_type": view.document_type,
                "locale": view.locale,
                "lifecycle": view.lifecycle,
                "latest_revision_id": view.latest_revision_id,
            },
        )

    return router
