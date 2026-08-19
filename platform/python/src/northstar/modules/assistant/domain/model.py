"""Pure value objects for the assistant module."""

from __future__ import annotations

from dataclasses import dataclass

RES_ASSISTANT = "assistant.session"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AssistantModel:
    """A selectable chat model. The request URL is ``{base_url}/{model}/v1/chat/completions``."""

    id: str
    label: str
    model: str
    kind: str  # coder | reasoning | planning | general


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    object_id: str
    revision_id: str
    block_id: str
    text: str
    score: float
