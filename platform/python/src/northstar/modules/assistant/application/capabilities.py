"""Assistant capabilities: one authoritative implementation per action (LAW-04).

``assistant.ask`` grounds the user's question in indexed curriculum passages (via the released
``retrieval.search`` gateway) and asks a configured chat model, returning the answer + its sources.
It runs through the kernel command bus, so every question is policy-checked and audited. Tenant scope
and the acting subject come from the authenticated :class:`RequestContext`, never the payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.errors import AssistantError
from ..domain.model import ChatMessage
from .config import AssistantModelStore
from .ports import ChatModelPort, RetrievalGatewayPort

CAP_VERSION = "1.0.0"

CAP_ASK = "assistant.ask"
ASSISTANT_CAPABILITIES: tuple[str, ...] = (CAP_ASK,)

_SYSTEM = (
    "You are the Bestinfopages tutor, an expert Python and AI-systems teacher. Answer the learner's "
    "question clearly and concisely. Prefer the provided context from the course when it is relevant, "
    "and you may add correct, well-known Python knowledge. Use short paragraphs and, when helpful, a "
    "small fenced code example. If the question is unrelated to the course or you are unsure, say so "
    "briefly rather than inventing facts."
)


@dataclass(frozen=True, slots=True)
class AskCommand:
    question: str
    lesson_object_id: str | None = None
    model_id: str | None = None
    top_k: int = 5


@dataclass(frozen=True, slots=True)
class SourceView:
    object_id: str
    revision_id: str
    block_id: str
    snippet: str


@dataclass(frozen=True, slots=True)
class AnswerView:
    answer: str
    model: str
    sources: tuple[SourceView, ...]
    input_tokens: int
    output_tokens: int


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    scope = getattr(getattr(invocation, "context", None), "tenant_scope", None)
    if not scope:
        raise AssistantError("a tenant scope is required")
    return scope


def _subject(invocation: object) -> str:
    actor = getattr(getattr(invocation, "context", None), "actor", None)
    subject = getattr(actor, "id", None)
    if not subject:
        raise AssistantError("an authenticated subject is required")
    return subject


def _correlation(invocation: object) -> str:
    return str(getattr(getattr(invocation, "context", None), "correlation_id", "assistant"))


class Ask:
    """``assistant.ask`` — retrieval-grounded question answering over a configured chat model."""

    def __init__(
        self,
        *,
        chat: ChatModelPort,
        retrieval: RetrievalGatewayPort,
        store: AssistantModelStore,
    ) -> None:
        self._chat = chat
        self._retrieval = retrieval
        self._store = store

    def handle(self, request: object) -> AnswerView:
        cmd = _typed(request, AskCommand)
        question = cmd.question.strip()
        if not question:
            raise AssistantError("question must not be empty")
        organization_id = _tenant(request)
        subject_id = _subject(request)
        correlation_id = _correlation(request)

        passages = list(
            self._retrieval.search(
                organization_id=organization_id,
                subject_id=subject_id,
                correlation_id=correlation_id,
                text=question,
                top_k=max(1, min(cmd.top_k, 10)),
            )
        )
        model = self._store.by_id(cmd.model_id) or self._store.active()

        context_block = "\n\n".join(
            f"[{i + 1}] {p.text[:800]}" for i, p in enumerate(passages) if p.text.strip()
        )
        system = _SYSTEM if not context_block else f"{_SYSTEM}\n\nCourse context:\n{context_block}"
        messages = (
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=question),
        )
        result = self._chat.complete(
            base_url=self._store.base_url, model=model.model, messages=messages
        )
        return AnswerView(
            answer=result.text,
            model=model.id,
            sources=tuple(
                SourceView(
                    object_id=p.object_id,
                    revision_id=p.revision_id,
                    block_id=p.block_id,
                    snippet=p.text[:180],
                )
                for p in passages
            ),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
