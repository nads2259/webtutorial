"""OpenAI-compatible chat model adapter (:class:`ChatModelPort`).

Posts to ``{base_url}/{model}/v1/chat/completions`` with optional Cloudflare Access headers (read from
the environment, never hard-coded), matching the gateway declared in ``models.txt``. Outbound calls
are made only to the admin-configured base URL. A production deployment can route this through the
kernel egress guard / a KMS-managed key without changing the domain or the capability.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import httpx

from ..domain.errors import AssistantError
from ..domain.model import ChatMessage, ChatResult
from ..application.ports import ChatModelPort


class OpenAICompatibleChatModel(ChatModelPort):
    """Reference chat-model adapter calling an OpenAI-compatible ``/v1/chat/completions`` endpoint."""

    def __init__(self, *, timeout_s: float = 45.0) -> None:
        self._timeout = timeout_s

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "Bestinfopages/1.0"}
        cf_id = os.environ.get("NORTHSTAR_ASSISTANT_CF_ID")
        cf_secret = os.environ.get("NORTHSTAR_ASSISTANT_CF_SECRET")
        if cf_id and cf_secret:
            headers["CF-Access-Client-Id"] = cf_id
            headers["CF-Access-Client-Secret"] = cf_secret
        api_key = os.environ.get("NORTHSTAR_ASSISTANT_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def complete(
        self,
        *,
        base_url: str,
        model: str,
        messages: Sequence[ChatMessage],
        max_tokens: int = 700,
    ) -> ChatResult:
        url = f"{base_url.rstrip('/')}/{model}/v1/chat/completions"
        payload = {
            "model": model,
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        try:
            response = httpx.post(url, json=payload, headers=self._headers(), timeout=self._timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise AssistantError(f"model request failed: {exc}") from exc
        except ValueError as exc:
            raise AssistantError("model returned a non-JSON response") from exc

        try:
            text = str(data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise AssistantError("model response was malformed") from exc
        usage = data.get("usage") or {}
        return ChatResult(
            text=text or "(the model returned an empty response)",
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
        )
