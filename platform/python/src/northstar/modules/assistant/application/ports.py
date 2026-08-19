"""Ports for the assistant application layer (DIP, rule 10/20)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.model import ChatMessage, ChatResult, RetrievedPassage


@runtime_checkable
class ChatModelPort(Protocol):
    """Calls an external OpenAI-compatible chat model and returns its completion text."""

    def complete(
        self,
        *,
        base_url: str,
        model: str,
        messages: Sequence[ChatMessage],
        max_tokens: int = 700,
    ) -> ChatResult: ...


@runtime_checkable
class AssistantSettingsPort(Protocol):
    """Durable, tenant-scoped store for the admin-selected active model."""

    def get_active_model(self, *, organization_id: str) -> str | None: ...

    def set_active_model(self, *, organization_id: str, model_id: str) -> None: ...


@runtime_checkable
class RetrievalGatewayPort(Protocol):
    """Grounds a question in indexed curriculum content via the released ``retrieval.search``."""

    def search(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        text: str,
        top_k: int,
    ) -> Sequence[RetrievedPassage]: ...
