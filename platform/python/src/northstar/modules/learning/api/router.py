"""``/learning`` FastAPI router (FR-LRN-001..007).

A thin inbound adapter over the kernel command/query buses (LAW-04). The authenticated
:class:`RequestContext` — including the tenant scope — is resolved by an injected authenticator
(server-side session), NEVER from the request body or a client header (rule 50). Routes dispatch the
learning capabilities on the bus, which authorize deny-by-default before the capability runs. Policy
denials surface as ``403 application/problem+json``; typed domain rejections as ``422``. No business
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
    CAP_ATTEMPT_SUBMIT,
    CAP_COURSE_COMPOSE,
    CAP_COURSE_PUBLISH,
    CAP_CREDENTIAL_EVALUATE,
    CAP_ITEM_PUBLISH,
    CAP_OVERLAY_ADD,
    CAP_PROFILE_CORRECT,
    CAP_PROFILE_INSPECT,
    CAP_PROFILE_RESET,
    CAP_PROGRESS_RECORD,
    CAP_PROGRESS_RESUME,
    CAP_RECOMMEND_NEXT,
    CAP_TUTOR_ASK,
    CAP_VERSION,
    AddOverlayCommand,
    CompletionRuleSpec,
    ComposeCourseCommand,
    CorrectProfileCommand,
    EvaluateCredentialCommand,
    InspectProfileQuery,
    PublishCourseCommand,
    PublishItemCommand,
    RecommendNextQuery,
    RecordProgressCommand,
    ResetProfileCommand,
    ResumeQuery,
    SectionSpec,
    SubmitAttemptCommand,
    TutorAskCommand,
)
from ..domain.errors import LearningError
from ..domain.model import RES_LEARNING

_STATE_KEY = "northstar_learning_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"

Authenticator = Callable[[Request], "RequestContext | None"]


@dataclass(frozen=True, slots=True)
class LearningApiDependencies:
    """Collaborators the ``/learning`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    authenticate: Authenticator


def bind_learning_dependencies(app_state: object, deps: LearningApiDependencies) -> None:
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> LearningApiDependencies:
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
    return ResourceRef(type=RES_LEARNING, id=context.tenant_scope or "-")


def create_learning_router() -> APIRouter:
    """Build the ``/learning`` router (dependencies read from ``app.state``)."""
    router = APIRouter(prefix="/learning", tags=["learning"])

    async def _body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    def _auth(request: Request) -> RequestContext | None:
        return _deps(request).authenticate(request)

    def _command(request: Request, context: RequestContext, cap: str, payload: object) -> object:
        command = Command(
            capability=cap, version=CAP_VERSION, payload=payload, resource=_resource(context)
        )
        return _deps(request).command_bus.dispatch(command, context)

    def _query(request: Request, context: RequestContext, cap: str, params: object) -> object:
        query = Query(
            capability=cap, version=CAP_VERSION, parameters=params, resource=_resource(context)
        )
        return _deps(request).query_bus.dispatch(query, context)

    def _run(
        context: RequestContext | None, fn: Callable[[], object]
    ) -> tuple[object, JSONResponse | None]:
        if context is None:
            return None, _problem(401, "authentication.required", "Authentication is required", "-")
        try:
            return fn().value, None  # type: ignore[union-attr]
        except PolicyDenied:
            return None, _problem(
                403, "authorization.denied", "Access denied", context.correlation_id
            )
        except (LearningError, KernelError) as exc:
            return None, _problem(422, "learning.rejected", str(exc), context.correlation_id)

    @router.post("/courses")
    async def compose_course(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        rule = body.get("completion_rule")
        payload = ComposeCourseCommand(
            course_id=str(body.get("course_id", "")),
            domain_id=str(body.get("domain_id", "")),
            title=str(body.get("title", "")),
            sections=tuple(
                SectionSpec(
                    section_id=str(s.get("section_id", "")),
                    title=str(s.get("title", "")),
                    object_id=str(s.get("object_id", "")),
                    revision_id=str(s.get("revision_id", "")),
                    block_ids=tuple(str(b) for b in s.get("block_ids", []) or []),
                    ordinal=int(s.get("ordinal", 0)),
                )
                for s in body.get("sections", []) or []
            ),
            path_id=body.get("path_id"),
            domain_title=body.get("domain_title"),
            domain_slug=body.get("domain_slug"),
            completion_rule=(
                CompletionRuleSpec(
                    rule_id=str(rule.get("rule_id", "")),
                    required_section_ids=tuple(rule.get("required_section_ids", []) or ()),
                    required_item_ids=tuple(rule.get("required_item_ids", []) or ()),
                )
                if isinstance(rule, dict)
                else None
            ),
        )
        value, problem = _run(
            context, lambda: _command(request, context, CAP_COURSE_COMPOSE, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=201,
            content={"course_id": value.course_id, "section_count": value.section_count},
        )

    @router.post("/courses/{course_id}/publish")
    async def publish_course(course_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        value, problem = _run(
            context,
            lambda: _command(
                request, context, CAP_COURSE_PUBLISH, PublishCourseCommand(course_id=course_id)
            ),
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200, content={"course_id": value.course_id, "published": value.published}
        )

    @router.post("/progress")
    async def record_progress(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = RecordProgressCommand(
            course_id=str(body.get("course_id", "")),
            section_id=str(body.get("section_id", "")),
            block_id=str(body.get("block_id", "")),
            modality=str(body.get("modality", "guided")),
            complete_section=bool(body.get("complete_section", False)),
        )
        value, problem = _run(
            context, lambda: _command(request, context, CAP_PROGRESS_RECORD, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(status_code=200, content=_progress_body(value))

    @router.get("/courses/{course_id}/resume")
    async def resume(course_id: str, request: Request) -> JSONResponse:
        context = _auth(request)
        value, problem = _run(
            context,
            lambda: _query(request, context, CAP_PROGRESS_RESUME, ResumeQuery(course_id=course_id)),
        )
        if problem is not None:
            return problem
        return JSONResponse(status_code=200, content=_progress_body(value))

    @router.post("/overlays")
    async def add_overlay(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = AddOverlayCommand(
            course_id=str(body.get("course_id", "")),
            section_id=str(body.get("section_id", "")),
            block_id=str(body.get("block_id", "")),
            kind=str(body.get("kind", "")),
            body=str(body.get("body", "")),
            quote=body.get("quote"),
        )
        value, problem = _run(context, lambda: _command(request, context, CAP_OVERLAY_ADD, payload))
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=201,
            content={
                "overlay_id": value.overlay_id,
                "kind": value.kind,
                "position": value.position,
            },
        )

    @router.post("/assessment/items")
    async def publish_item(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = PublishItemCommand(
            item_id=str(body.get("item_id", "")),
            version=str(body.get("version", "")),
            kind=str(body.get("kind", "")),
            prompt=str(body.get("prompt", "")),
            answer_key=tuple(str(a) for a in body.get("answer_key", []) or ()),
            choices=tuple(str(c) for c in body.get("choices", []) or ()),
            points=int(body.get("points", 1)),
            pass_ratio=float(body.get("pass_ratio", 1.0)),
            max_attempts=int(body.get("max_attempts", 3)),
            accommodations=tuple(str(a) for a in body.get("accommodations", []) or ()),
        )
        value, problem = _run(
            context, lambda: _command(request, context, CAP_ITEM_PUBLISH, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=201,
            content={
                "item_id": value.item_id,
                "version": value.version,
                "content_hash": value.content_hash,
                "sealed": value.sealed,
                "item": value.item,
            },
        )

    @router.post("/assessment/attempts")
    async def submit_attempt(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = SubmitAttemptCommand(
            item_id=str(body.get("item_id", "")),
            version=str(body.get("version", "")),
            responses=tuple(str(r) for r in body.get("responses", []) or ()),
            accommodations=tuple(str(a) for a in body.get("accommodations", []) or ()),
        )
        value, problem = _run(
            context, lambda: _command(request, context, CAP_ATTEMPT_SUBMIT, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=201,
            content={
                "attempt_id": value.attempt_id,
                "raw": value.raw,
                "max": value.max,
                "passed": value.passed,
                "feedback": value.feedback,
                "attempt_number": value.attempt_number,
            },
        )

    @router.post("/credentials/evaluate")
    async def evaluate_credential(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = EvaluateCredentialCommand(
            course_id=str(body.get("course_id", "")), rule_id=str(body.get("rule_id", ""))
        )
        value, problem = _run(
            context, lambda: _command(request, context, CAP_CREDENTIAL_EVALUATE, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={
                "satisfied": value.satisfied,
                "credential_id": value.credential_id,
                "rule_id": value.rule_id,
                "evidence": list(value.evidence),
                "missing": list(value.missing),
                "verification_hash": value.verification_hash,
                "verified": value.verified,
                "already_issued": value.already_issued,
            },
        )

    @router.get("/recommendations")
    async def recommend(request: Request) -> JSONResponse:
        context = _auth(request)
        value, problem = _run(
            context, lambda: _query(request, context, CAP_RECOMMEND_NEXT, RecommendNextQuery())
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={
                "consented": value.consented,
                "recommendations": [
                    {
                        "course_id": r.course_id,
                        "reason": r.reason,
                        "factors": list(r.factors),
                        "inferred_difficulty": r.inferred_difficulty,
                    }
                    for r in value.recommendations
                ],
            },
        )

    @router.get("/profile")
    async def inspect_profile(request: Request) -> JSONResponse:
        context = _auth(request)
        value, problem = _run(
            context, lambda: _query(request, context, CAP_PROFILE_INSPECT, InspectProfileQuery())
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={"subject_id": value.subject_id, "features": list(value.features)},
        )

    @router.post("/profile/correct")
    async def correct_profile(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = CorrectProfileCommand(
            feature=str(body.get("feature", "")), value=str(body.get("value", ""))
        )
        value, problem = _run(
            context, lambda: _command(request, context, CAP_PROFILE_CORRECT, payload)
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={"subject_id": value.subject_id, "features": list(value.features)},
        )

    @router.post("/profile/reset")
    async def reset_profile(request: Request) -> JSONResponse:
        context = _auth(request)
        value, problem = _run(
            context, lambda: _command(request, context, CAP_PROFILE_RESET, ResetProfileCommand())
        )
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={"subject_id": value.subject_id, "features": list(value.features)},
        )

    @router.post("/tutor/ask")
    async def tutor_ask(request: Request) -> JSONResponse:
        context = _auth(request)
        body = await _body(request)
        payload = TutorAskCommand(
            question=str(body.get("question", "")), locale=str(body.get("locale", "en"))
        )
        value, problem = _run(context, lambda: _command(request, context, CAP_TUTOR_ASK, payload))
        if problem is not None:
            return problem
        return JSONResponse(
            status_code=200,
            content={
                "answer": value.answer,
                "refused": value.refused,
                "locale": value.locale,
                "citations": [
                    {
                        "object_id": c.object_id,
                        "revision_id": c.revision_id,
                        "block_id": c.block_id,
                        "chunk_id": c.chunk_id,
                        "claim": c.claim,
                    }
                    for c in value.citations
                ],
                "rubric": value.rubric,
                "disclosed_answer_key": value.disclosed_answer_key,
                "human_review_required": value.human_review_required,
            },
        )

    return router


def _progress_body(value: object) -> dict[str, object]:
    return {
        "subject_id": value.subject_id,
        "course_id": value.course_id,
        "resume": value.resume,
        "modality": value.modality,
        "completed_sections": list(value.completed_sections),
        "next_section_id": value.next_section_id,
    }
