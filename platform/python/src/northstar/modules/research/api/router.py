"""``/research`` FastAPI router (docs/37, FR-RSH-001..006).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), NEVER from the request body or a client header (rule 50). Routes dispatch the
research capabilities on the buses, which authorize deny-by-default before the capability runs.
Policy denials surface as ``403 application/problem+json``; typed research-domain errors as ``422``.
No business logic here.
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
    CAP_AI_DRAFT,
    CAP_ASSERT_CLAIM,
    CAP_AUTHOR_DOCUMENT,
    CAP_CREATE_PROJECT,
    CAP_CREATE_WORKSPACE,
    CAP_EXPORT_DOCUMENT,
    CAP_PACKAGE_REPRODUCIBILITY,
    CAP_PUBLISH_DOCUMENT,
    CAP_REGISTER_EVIDENCE,
    CAP_VERSION,
    RES_RESEARCH,
    AiAssistedDraftCommand,
    AssertClaimCommand,
    AuthorDocumentCommand,
    CreateProjectCommand,
    CreateWorkspaceCommand,
    ExportDocumentQuery,
    PackageReproducibilityCommand,
    PublishDocumentCommand,
    RegisterEvidenceCommand,
)
from ..domain.errors import ResearchError

_STATE_KEY = "northstar_research_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], "RequestContext | None"]


@dataclass(frozen=True, slots=True)
class ResearchApiDependencies:
    """Collaborators the ``/research`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_research_dependencies(app_state: object, deps: ResearchApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> ResearchApiDependencies:
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
    return ResourceRef(type=RES_RESEARCH, id=context.tenant_scope or "-")


def create_research_router() -> APIRouter:
    """Build the ``/research`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/research", tags=["research"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _dispatch_command(
        request: Request, context: RequestContext, cap: str, payload: object
    ) -> object:
        command = Command(
            capability=cap, version=CAP_VERSION, payload=payload, resource=_resource(context)
        )
        return _deps(request).command_bus.dispatch(command, context)

    @router.post("/workspaces")
    async def create_workspace(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_CREATE_WORKSPACE,
                CreateWorkspaceCommand(name=str(body.get("name", ""))),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ResearchError, KernelError) as exc:
            return _problem(422, "research.invalid", str(exc), context.correlation_id)
        return JSONResponse(status_code=201, content={"workspace_id": result.value.workspace_id})

    @router.post("/projects")
    async def create_project(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_CREATE_PROJECT,
                CreateProjectCommand(
                    workspace_id=str(body.get("workspace_id", "")),
                    title=str(body.get("title", "")),
                    research_question=body.get("research_question"),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ResearchError, KernelError) as exc:
            return _problem(422, "research.invalid", str(exc), context.correlation_id)
        return JSONResponse(status_code=201, content={"project_id": result.value.project_id})

    @router.post("/documents")
    async def author_document(request: Request) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_AUTHOR_DOCUMENT,
                AuthorDocumentCommand(
                    project_id=str(body.get("project_id", "")),
                    title=str(body.get("title", "")),
                    blocks=tuple(body.get("blocks", []) or ()),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ResearchError, KernelError) as exc:
            return _problem(422, "research.invalid", str(exc), context.correlation_id)
        return JSONResponse(status_code=201, content={"document_id": result.value.document_id})

    @router.post("/documents/{document_id}/publish")
    async def publish_document(request: Request, document_id: str) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_PUBLISH_DOCUMENT,
                PublishDocumentCommand(document_id=document_id, title=str(body.get("title", ""))),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ResearchError, KernelError) as exc:
            return _problem(422, "research.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={
                "revision_id": result.value.revision_id,
                "content_hash": result.value.content_hash,
            },
        )

    @router.post("/documents/{document_id}/evidence")
    async def register_evidence(request: Request, document_id: str) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_REGISTER_EVIDENCE,
                RegisterEvidenceCommand(
                    document_id=document_id,
                    excerpt=str(body.get("excerpt", "")),
                    kind=str(body.get("kind", "external")),
                    object_id=body.get("object_id"),
                    revision_id=body.get("revision_id"),
                    block_id=body.get("block_id"),
                    chunk_id=body.get("chunk_id"),
                    source_uri=body.get("source_uri"),
                    verified=bool(body.get("verified", False)),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ResearchError, KernelError) as exc:
            return _problem(422, "research.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "evidence_id": result.value.evidence_id,
                "version_hash": result.value.version_hash,
            },
        )

    @router.post("/documents/{document_id}/claims")
    async def assert_claim(request: Request, document_id: str) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_ASSERT_CLAIM,
                AssertClaimCommand(
                    document_id=document_id,
                    statement=str(body.get("statement", "")),
                    evidence_ids=tuple(str(e) for e in body.get("evidence_ids", []) or ()),
                    confidence=body.get("confidence"),
                    generated=bool(body.get("generated", False)),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ResearchError, KernelError) as exc:
            return _problem(422, "research.invalid", str(exc), context.correlation_id)
        return JSONResponse(status_code=201, content={"claim_id": result.value.claim_id})

    @router.post("/documents/{document_id}/ai-draft")
    async def ai_draft(request: Request, document_id: str) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_AI_DRAFT,
                AiAssistedDraftCommand(
                    document_id=document_id,
                    question=str(body.get("question", "")),
                    package_id=str(body.get("package_id", "")),
                    version=str(body.get("package_version", "1.0.0")),
                    top_k=int(body.get("top_k", 5)),
                    data_classification=str(body.get("data_classification", "public")),
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ResearchError, KernelError) as exc:
            return _problem(422, "research.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "claim_id": result.value.claim_id,
                "evidence_ids": list(result.value.evidence_ids),
                "refused": result.value.refused,
                "trace_id": result.value.trace_id,
            },
        )

    @router.get("/documents/{document_id}/export")
    async def export_document(request: Request, document_id: str) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        try:
            result = _deps(request).query_bus.dispatch(
                Query(
                    capability=CAP_EXPORT_DOCUMENT,
                    version=CAP_VERSION,
                    parameters=ExportDocumentQuery(document_id=document_id),
                    resource=_resource(context),
                ),
                context,
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ResearchError, KernelError) as exc:
            return _problem(422, "research.invalid", str(exc), context.correlation_id)
        return JSONResponse(status_code=200, content=result.value.document)

    @router.post("/documents/{document_id}/reproducibility-package")
    async def package_reproducibility(request: Request, document_id: str) -> JSONResponse:
        context = _auth(request)
        if context is None:
            return _problem(401, "authentication.required", "Authentication is required", "-")
        body = await _body(request)
        environment = body.get("environment") or {}
        try:
            result = _dispatch_command(
                request,
                context,
                CAP_PACKAGE_REPRODUCIBILITY,
                PackageReproducibilityCommand(
                    document_id=document_id,
                    environment={str(k): str(v) for k, v in dict(environment).items()},
                ),
            )
        except PolicyDenied:
            return _problem(403, "authorization.denied", "Access denied", context.correlation_id)
        except (ResearchError, KernelError) as exc:
            return _problem(422, "research.invalid", str(exc), context.correlation_id)
        return JSONResponse(
            status_code=201,
            content={
                "package": result.value.package.to_dict(),
                "report": result.value.report.to_dict(),
            },
        )

    return router
