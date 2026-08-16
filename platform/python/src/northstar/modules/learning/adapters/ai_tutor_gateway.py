"""AI-tutor gateway adapters implementing :class:`AiTutorPort` (EVAL-AI-009/011, LAW-09).

The learning tutor REUSES the AI module's single governed ``ai.answer`` capability — it never builds
a second AI path. The tutor runs as a SCOPED ``ai_actor`` delegated by the learner and is handed
ONLY the learner's question in the learner's locale (EVAL-AI-011: retrieval/safety/citation are
preserved per-locale by the one pipeline). An assessment answer key is NEVER placed in the tutor's
context, so an adversarial "give me the answer key" prompt returns nothing — the same property the
simulation coach relies on for the hidden scoring key. Two adapters mirror the simulation coach:

* :class:`BusAiTutorGateway` dispatches ``ai.answer`` on the authorized+audited command bus.
* :class:`DirectAiTutorGateway` drives an ``ai.Answer`` handler directly for deterministic tests
  (still the real pipeline + output guard + citation verifier).
"""

from __future__ import annotations

from northstar.kernel.context import Actor, ActorType, RequestContext, ResourceRef
from northstar.kernel.messaging import Command, CommandBus
from northstar.modules.ai.application import capabilities as ai

from ..application.ports import TutorAnswer, TutorCitation


def _context(*, organization_id: str, subject_id: str, correlation_id: str) -> RequestContext:
    # The tutor acts as a scoped AI actor delegated by the learner, so retrieval's ACL resolves only
    # the learner's authorized content — never a privileged channel or another tenant's data.
    return RequestContext(
        actor=Actor(type=ActorType.AI_ACTOR, id=f"ai:{subject_id}", delegated_by=subject_id),
        correlation_id=correlation_id,
        tenant_scope=organization_id,
    )


def _to_answer(view: object, *, locale: str) -> TutorAnswer:
    citations = tuple(
        TutorCitation(
            object_id=str(getattr(c, "object_id", "")),
            revision_id=str(getattr(c, "revision_id", "")),
            block_id=str(getattr(c, "block_id", "")),
            chunk_id=str(getattr(c, "chunk_id", "")),
            claim=str(getattr(c, "claim", "")),
        )
        for c in getattr(view, "citations", ())
    )
    return TutorAnswer(
        answer=str(getattr(view, "answer", "")),
        refused=bool(getattr(view, "refused", False)),
        locale=locale,
        citations=citations,
        trace_id=str(getattr(view, "trace_id", "")),
        provider=str(getattr(view, "provider", "")),
        model=str(getattr(view, "model", "")),
    )


class BusAiTutorGateway:
    """Dispatches ``ai.answer`` on the command bus (authorized + audited, production wiring)."""

    def __init__(self, *, command_bus: CommandBus) -> None:
        self._command_bus = command_bus

    def ask(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        question: str,
        locale: str,
        package_id: str,
        version: str,
    ) -> TutorAnswer:
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
                locale=locale,
                data_classification="internal",
            ),
            resource=ResourceRef(type=ai.RES_AI, id=organization_id),
        )
        result = self._command_bus.dispatch(command, context)
        return _to_answer(result.value, locale=locale)


class DirectAiTutorGateway:
    """Drives an ``ai.Answer`` handler directly for tests (real pipeline + guard + verifier)."""

    def __init__(self, *, answer: ai.Answer) -> None:
        self._answer = answer

    def ask(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        question: str,
        locale: str,
        package_id: str,
        version: str,
    ) -> TutorAnswer:
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
                    locale=locale,
                    data_classification="internal",
                ),
            )
        )
        return _to_answer(view, locale=locale)


class _Invocation:
    __slots__ = ("context", "payload")

    def __init__(self, *, context: RequestContext, payload: object) -> None:
        self.context = context
        self.payload = payload


__all__ = ["BusAiTutorGateway", "DirectAiTutorGateway"]
