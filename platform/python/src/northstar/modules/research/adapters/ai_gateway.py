"""AI-draft gateway adapters implementing :class:`AiDraftPort` (FR-RSH-005, ARCH-009).

Research REUSES the AI module's single governed ``ai.answer`` capability — it never builds a second
AI path. Two adapters, both normalizing the AI module's :class:`AnswerView` (whose citations are
ALREADY verified against retrieved evidence by the AI citation verifier) into an
:class:`AiDraftResult`:

* :class:`BusAiDraftGateway` dispatches ``ai.answer`` on the kernel command bus, so the call is
  authorized deny-by-default and audited (production wiring).
* :class:`DirectAiDraftGateway` drives an ``ai.Answer`` handler directly for fast, deterministic
  unit/security tests (still the real pipeline + citation verifier).

An uncited/fabricated citation never survives the AI verifier, so it never reaches research — which
is exactly what lets the research domain reject an unevidenced AI claim (EVAL-RSH-005).
"""

from __future__ import annotations

from northstar.kernel.context import Actor, ActorType, RequestContext, ResourceRef
from northstar.kernel.messaging import Command, CommandBus
from northstar.modules.ai.application import capabilities as ai

from ..application.ports import AiDraftResult, DraftedCitation


def _to_result(view: object) -> AiDraftResult:
    citations = tuple(
        DraftedCitation(
            object_id=c.object_id,
            revision_id=c.revision_id,
            block_id=c.block_id,
            chunk_id=c.chunk_id,
            claim=c.claim,
        )
        for c in getattr(view, "citations", ())
    )
    return AiDraftResult(
        answer=getattr(view, "answer", ""),
        refused=bool(getattr(view, "refused", False)),
        citations=citations,
        provider=getattr(view, "provider", ""),
        model=getattr(view, "model", ""),
        prompt_package=getattr(view, "prompt_package", ""),
        trace_id=getattr(view, "trace_id", ""),
    )


def _context(*, organization_id: str, subject_id: str, correlation_id: str) -> RequestContext:
    # The AI actor operates with the delegated researcher's scope so retrieval's ACL resolves only
    # the caller's authorized passages — never another tenant's evidence.
    return RequestContext(
        actor=Actor(type=ActorType.AI_ACTOR, id=subject_id, delegated_by=subject_id),
        correlation_id=correlation_id,
        tenant_scope=organization_id,
    )


class BusAiDraftGateway:
    """Dispatches ``ai.answer`` on the command bus (authorized + audited, production wiring)."""

    def __init__(self, *, command_bus: CommandBus) -> None:
        self._command_bus = command_bus

    def draft(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        question: str,
        package_id: str,
        version: str,
        top_k: int,
        data_classification: str,
    ) -> AiDraftResult:
        context = _context(
            organization_id=organization_id,
            subject_id=subject_id,
            correlation_id=correlation_id,
        )
        command = Command(
            capability=ai.CAP_ANSWER,
            version=ai.CAP_VERSION,
            payload=ai.AnswerCommand(
                package_id=package_id,
                version=version,
                question=question,
                top_k=top_k,
                data_classification=data_classification,
            ),
            resource=ResourceRef(type=ai.RES_AI, id=organization_id),
        )
        result = self._command_bus.dispatch(command, context)
        return _to_result(result.value)


class DirectAiDraftGateway:
    """Drives an ``ai.Answer`` handler directly for tests (real pipeline + citation verifier)."""

    def __init__(self, *, answer: ai.Answer) -> None:
        self._answer = answer

    def draft(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        question: str,
        package_id: str,
        version: str,
        top_k: int,
        data_classification: str,
    ) -> AiDraftResult:
        context = _context(
            organization_id=organization_id,
            subject_id=subject_id,
            correlation_id=correlation_id,
        )
        view = self._answer.handle(
            _Invocation(
                context=context,
                payload=ai.AnswerCommand(
                    package_id=package_id,
                    version=version,
                    question=question,
                    top_k=top_k,
                    data_classification=data_classification,
                ),
            )
        )
        return _to_result(view)


class _Invocation:
    __slots__ = ("context", "payload")

    def __init__(self, *, context: RequestContext, payload: object) -> None:
        self.context = context
        self.payload = payload
