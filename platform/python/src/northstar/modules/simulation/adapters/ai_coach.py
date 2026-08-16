"""AI-coach gateway adapters implementing :class:`AiCoachPort` (FR-SIM-007, LAW-09).

Simulation coaching REUSES the AI module's single governed ``ai.answer`` capability — it never
builds a second AI path. The coach runs as a SCOPED ``ai_actor`` delegated by the learner, and is
handed ONLY the permitted runtime channel (the question + the definition's objectives). The hidden
scoring key is loaded on the scoring path alone and is NEVER placed in the AI's context, so an
adversarial coaching prompt asking for it returns nothing (``scoring_key_disclosure_rate == 0``,
EVAL-SIM-007 / GATE-AI-GA). Two adapters mirror the research gateway:

* :class:`BusAiCoachGateway` dispatches ``ai.answer`` on the authorized+audited command bus.
* :class:`DirectAiCoachGateway` drives an ``ai.Answer`` handler directly for deterministic tests
  (still the real pipeline + output guard + citation verifier).
"""

from __future__ import annotations

from collections.abc import Sequence

from northstar.kernel.context import Actor, ActorType, RequestContext, ResourceRef
from northstar.kernel.messaging import Command, CommandBus
from northstar.modules.ai.application import capabilities as ai

from ..application.ports import CoachResult


def _context(*, organization_id: str, subject_id: str, correlation_id: str) -> RequestContext:
    # The coach acts as a scoped AI actor delegated by the learner, so retrieval's ACL resolves only
    # the learner's authorized content — never a privileged channel or another tenant's data.
    return RequestContext(
        actor=Actor(type=ActorType.AI_ACTOR, id=f"ai:{subject_id}", delegated_by=subject_id),
        correlation_id=correlation_id,
        tenant_scope=organization_id,
    )


def _coaching_question(question: str, runtime_channel: Sequence[str]) -> str:
    """Compose the coaching prompt from ONLY the permitted runtime channel (no scoring key)."""
    if not runtime_channel:
        return question
    objectives = "; ".join(runtime_channel)
    return f"Simulation objectives: {objectives}\n\nLearner question: {question}"


def _to_result(view: object, *, channels: Sequence[str]) -> CoachResult:
    return CoachResult(
        hint=str(getattr(view, "answer", "")),
        refused=bool(getattr(view, "refused", False)),
        channels=tuple(channels),
        trace_id=str(getattr(view, "trace_id", "")),
        # By construction the scoring key is never in the coach's context, so it cannot disclose it.
        disclosed_scoring_key=False,
    )


class BusAiCoachGateway:
    """Dispatches ``ai.answer`` on the command bus (authorized + audited, production wiring)."""

    def __init__(self, *, command_bus: CommandBus) -> None:
        self._command_bus = command_bus

    def coach(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        question: str,
        runtime_channel: Sequence[str],
        package_id: str,
        version: str,
    ) -> CoachResult:
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
                question=_coaching_question(question, runtime_channel),
                data_classification="internal",
            ),
            resource=ResourceRef(type=ai.RES_AI, id=organization_id),
        )
        result = self._command_bus.dispatch(command, context)
        return _to_result(result.value, channels=runtime_channel)


class DirectAiCoachGateway:
    """Drives an ``ai.Answer`` handler directly for tests (real pipeline + guard + verifier)."""

    def __init__(self, *, answer: ai.Answer) -> None:
        self._answer = answer

    def coach(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        question: str,
        runtime_channel: Sequence[str],
        package_id: str,
        version: str,
    ) -> CoachResult:
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
                    question=_coaching_question(question, runtime_channel),
                    data_classification="internal",
                ),
            )
        )
        return _to_result(view, channels=runtime_channel)


class _Invocation:
    __slots__ = ("context", "payload")

    def __init__(self, *, context: RequestContext, payload: object) -> None:
        self.context = context
        self.payload = payload
