"""``/knowledge`` FastAPI router (docs/06, FR-CNT-001..007).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), **never** from the request body or a client header (rule 50, tenant
isolation). Policy denials surface as ``403 application/problem+json``; typed domain errors as
``422``. No business logic lives here.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from northstar.kernel.context import Actor, ActorType, RequestContext, ResourceRef
from northstar.kernel.errors import KernelError, PolicyDenied
from northstar.kernel.messaging import Command, CommandBus, Query, QueryBus

from ..application.capabilities import (
    CAP_ASSIGN_TAXONOMY,
    CAP_BROWSE_DOCUMENTS,
    CAP_CREATE_DOCUMENT,
    CAP_EDIT_DRAFT,
    CAP_GET_DOCUMENT,
    CAP_GET_REVISION,
    CAP_PUBLISH_DOCUMENT,
    CAP_SUBMIT_FOR_REVIEW,
    CAP_TAXONOMY_TERMS,
    CAP_VERSION,
    AssignTaxonomyCommand,
    BrowseDocumentsQuery,
    CreateDocumentCommand,
    EditDraftCommand,
    GetDocumentQuery,
    GetRevisionQuery,
    PublishDocumentCommand,
    SubmitForReviewCommand,
    TaxonomyTermsQuery,
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
    # When set, unauthenticated READ requests are served as an anonymous viewer scoped to this
    # tenant (public, crawlable published content for SEO). Writes always require a session.
    public_tenant: str | None = None


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


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


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

    def _read_ctx(request: Request) -> RequestContext | None:
        """Authenticated context if present, else an anonymous public-tenant viewer (for SEO)."""
        context = _auth(request)
        if context is not None:
            return context
        public = _deps(request).public_tenant
        if public:
            return RequestContext(
                actor=Actor(type=ActorType.ANONYMOUS, id="anonymous"),
                correlation_id=f"pub_{uuid.uuid4().hex}",
                tenant_scope=public,
            )
        return None

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

    @router.get("/catalog")
    def browse_catalog(request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _read_ctx(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        params = request.query_params
        query = Query(
            capability=CAP_BROWSE_DOCUMENTS,
            version=CAP_VERSION,
            parameters=BrowseDocumentsQuery(
                category=params.get("category") or None,
                module=params.get("module") or None,
                kind=params.get("kind") or None,
                subject=params.get("subject") or None,
                phase=params.get("phase") or None,
                phase_title=params.get("phase_title") or None,
                q=params.get("q") or None,
                published_after=params.get("published_after") or None,
                published_before=params.get("published_before") or None,
                sort=params.get("sort") or "order",
                include_total=_flag(params.get("include_total")),
                limit=_int(params.get("limit"), 200),
                offset=_int(params.get("offset"), 0),
            ),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "knowledge.invalid", str(exc), context.correlation_id)
        view = result.value
        payload: dict[str, object] = {
            "entries": [
                {
                    "object_id": e.object_id,
                    "revision_id": e.revision_id,
                    "title": e.title,
                    "summary": e.summary,
                    "document_type": e.document_type,
                    "locale": e.locale,
                    "terms": e.terms,
                    "published_at": e.published_at,
                }
                for e in view.entries
            ]
        }
        if view.total is not None:
            payload["total"] = view.total
        return JSONResponse(status_code=200, content=payload)

    @router.get("/lesson-index")
    def lesson_index(request: Request) -> JSONResponse:
        """A compact ``lesson_id -> {r: revision_id, t: title}`` map for cross-reference hyperlinks.

        Built live from the current published documents (public tenant), so links always resolve to
        the latest revision. Read-only + cacheable by the client.
        """
        deps = _deps(request)
        context = _read_ctx(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        lessons: dict[str, dict[str, str]] = {}
        offset = 0
        page = 1000
        while True:
            query = Query(
                capability=CAP_BROWSE_DOCUMENTS,
                version=CAP_VERSION,
                parameters=BrowseDocumentsQuery(limit=page, offset=offset),
            )
            try:
                result = deps.query_bus.dispatch(query, context)
            except PolicyDenied:
                return _problem(
                    403, "authorization.denied", "Access denied", context.correlation_id
                )
            entries = result.value.entries
            if not entries:
                break
            for e in entries:
                lesson_id = (e.terms.get("lesson") or [None])[0]
                if lesson_id and e.revision_id:
                    lessons[str(lesson_id).upper()] = {"r": e.revision_id, "t": e.title}
            if len(entries) < page:
                break
            offset += page
        return JSONResponse(status_code=200, content={"lessons": lessons})

    @router.get("/taxonomy/{scheme}")
    def taxonomy_terms(scheme: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _read_ctx(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        query = Query(
            capability=CAP_TAXONOMY_TERMS,
            version=CAP_VERSION,
            parameters=TaxonomyTermsQuery(scheme=scheme),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except KernelError as exc:
            return _problem(422, "knowledge.invalid", str(exc), context.correlation_id)
        view = result.value
        return JSONResponse(
            status_code=200,
            content={
                "scheme": view.scheme,
                "terms": [{"term": t.term, "count": t.count} for t in view.terms],
            },
        )

    @router.get("/revisions/{revision_id}")
    def get_revision(revision_id: str, request: Request) -> JSONResponse:
        deps = _deps(request)
        context = _read_ctx(request)
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
        context = _read_ctx(request)
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
