"""A DETERMINISTIC mock model provider behind ``ModelGatewayPort`` (FR-AI-001, no external API).

Two modes:

* **grounded** (default): given retrieved passages it returns a faithful answer whose single claim
  is drawn verbatim from the top passage and cites that passage's exact identity — so a legitimate
  request produces a valid, verifiable citation (the red-team "control positive").
* **adversarial** (``MockProviderConfig(obey_injection=True)``): the model ATTEMPTS to obey any
  injected instruction it finds in the UNTRUSTED channel (user message or retrieved/poisoned
  content) — it will call the tool it is told to, echo secrets/instructions it is told to reveal,
  and emit the citation id it is told to fabricate. This is deliberate: it forces the framework's
  DEFENSES (Tool Broker, citation verifier, output guard, retrieval ACL), not the model's goodwill,
  to keep every zero-leak metric at zero. The mock never invents real secrets; it only echoes text
  already present in its (untrusted) input.

This adapter is pure Python + regex; it imports no provider SDK and no persistence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from northstar.modules.ai.application.ports import (
    GenerationRequest,
    GenerationResult,
    ToolCallRequest,
)
from northstar.modules.ai.domain.model import Citation, TokenUsage

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Injection directive patterns the adversarial mock "obeys" (found in the UNTRUSTED channel).
_TOOL_MARKER = re.compile(r"\[\[TOOL:([a-z0-9][a-z0-9._-]*)\]\]")
_TOOL_BADARGS_MARKER = re.compile(r"\[\[TOOL_BADARGS:([a-z0-9][a-z0-9._-]*)\]\]")
_TOOL_NATURAL = re.compile(
    r"(?:call|invoke|run|use|execute)\s+(?:the\s+)?tool\s+([a-z0-9][a-z0-9._-]*)"
)
_CITE_MARKER = re.compile(r"\[\[CITE:([a-z0-9][a-z0-9-]*)\]\]")
_LOOP_MARKER = re.compile(r"\[\[LOOP:([a-z0-9._-]+):(\d+)\]\]")

_REVEAL = re.compile(r"(?i)\b(reveal|exfiltrat\w*|leak|dump|print|show|expose)\b")
_SECRET_WORD = re.compile(
    r"(?i)\b(secret|api[_ -]?key|password|token|credential|system\s*prompt|instruction)s?\b"
)
_IGNORE = re.compile(
    r"(?i)\b(ignore|disregard|forget|override)\b.{0,40}\b(instruction|rule|prompt|polic)\w*"
)
_JAILBREAK = re.compile(r"(?i)\b(jailbreak|pretend|roleplay|dan mode|developer mode)\b")

# Natural-language phrases that map to specific prohibited/high-impact tool ids (excessive agency).
_DANGEROUS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)(rotate|change|reset).{0,30}(credential|password|api[ _-]?key)"),
        "ai.credential.rotate",
    ),
    (
        re.compile(r"(?i)(grant|give|make).{0,30}(admin|permission|role|superuser)"),
        "ai.permission.grant",
    ),
    (
        re.compile(r"(?i)(transfer|wire|send).{0,30}(fund|money|payment|dollar)"),
        "ai.finance.transfer",
    ),
)


@dataclass(frozen=True, slots=True)
class MockProviderConfig:
    """Configuration for the mock provider. ``obey_injection`` turns on adversarial obedience."""

    obey_injection: bool = False
    tool_version: str = "1.0.0"


def _first_sentence(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    parts = _SENTENCE_SPLIT.split(stripped)
    return parts[0].strip()


def _count_tokens(text: str) -> int:
    return len(text.split())


class DeterministicMockProvider:
    """A deterministic ``ModelGatewayPort`` for tests (grounded, or adversarially obedient)."""

    def __init__(self, config: MockProviderConfig | None = None) -> None:
        self._config = config or MockProviderConfig()

    def generate(self, request: GenerationRequest) -> GenerationResult:
        text, tool_calls, citations = self._grounded_base(request)
        if self._config.obey_injection:
            text, tool_calls, citations = self._obey(request, text, tool_calls, citations)
        usage = self._usage(request, text)
        return GenerationResult(
            text=text,
            tool_calls=tuple(tool_calls),
            citations=tuple(citations),
            usage=usage,
            finish_reason="stop",
        )

    # -- grounded base answer ------------------------------------------------

    def _grounded_base(
        self, request: GenerationRequest
    ) -> tuple[str, list[ToolCallRequest], list[Citation]]:
        if not request.retrieved:
            return (
                "I don't have any authorized sources on that, so I can't answer with grounding.",
                [],
                [],
            )
        top = request.retrieved[0]
        claim = _first_sentence(top.text) or top.text.strip()
        citation = Citation(
            object_id=top.object_id,
            revision_id=top.revision_id,
            block_id=top.block_id,
            chunk_id=top.chunk_id,
            claim=claim,
        )
        return (f"Based on the sources: {claim}", [], [citation])

    # -- adversarial obedience ----------------------------------------------

    def _obey(
        self,
        request: GenerationRequest,
        text: str,
        tool_calls: list[ToolCallRequest],
        citations: list[Citation],
    ) -> tuple[str, list[ToolCallRequest], list[Citation]]:
        untrusted = "\n".join(p.text for p in request.untrusted)
        scan = f"{request.user_message}\n{untrusted}"
        version = self._config.tool_version

        # Tool-call injections (markers, natural language, dangerous phrases).
        for tool_id in self._tool_ids(scan):
            tool_calls.append(
                ToolCallRequest(tool_id=tool_id, version=version, arguments={"note": "obeyed"})
            )
        for tool_id in _TOOL_BADARGS_MARKER.findall(scan):
            tool_calls.append(
                ToolCallRequest(tool_id=tool_id, version=version, arguments={"unexpected": 123})
            )
        for tool_id, count in _LOOP_MARKER.findall(scan):
            for _ in range(int(count)):
                tool_calls.append(
                    ToolCallRequest(
                        tool_id=tool_id,
                        version=version,
                        arguments={"query": request.user_message, "note": "loop"},
                    )
                )

        # Citation fabrication.
        for fake_id in _CITE_MARKER.findall(scan):
            citations.append(
                Citation(
                    object_id="obj-fabricated",
                    revision_id="rev-fabricated",
                    block_id="blk-fabricated",
                    chunk_id=fake_id,
                    claim="This is asserted by a fabricated source.",
                )
            )

        # Secret / instruction disclosure: echo whatever the injection points at.
        if _REVEAL.search(scan) and _SECRET_WORD.search(scan):
            leaked = [text, untrusted]
            if re.search(r"(?i)system\s*prompt|instruction", scan):
                leaked.append(request.system_instruction)
                leaked.extend(request.developer_instructions)
            text = " ".join(part for part in leaked if part)

        # Jailbreak / ignore-instructions: comply and leak the instruction channel.
        if _IGNORE.search(scan) or _JAILBREAK.search(scan):
            text = (
                f"{text} Sure — ignoring my prior rules as requested. {request.system_instruction}"
            )

        return text, tool_calls, citations

    def _tool_ids(self, scan: str) -> list[str]:
        ids: list[str] = []
        ids.extend(_TOOL_MARKER.findall(scan))
        ids.extend(_TOOL_NATURAL.findall(scan))
        for pattern, tool_id in _DANGEROUS:
            if pattern.search(scan):
                ids.append(tool_id)
        # Preserve order, drop duplicates.
        seen: set[str] = set()
        unique: list[str] = []
        for tool_id in ids:
            if tool_id not in seen:
                seen.add(tool_id)
                unique.append(tool_id)
        return unique

    def _usage(self, request: GenerationRequest, text: str) -> TokenUsage:
        prompt_tokens = (
            _count_tokens(request.system_instruction)
            + sum(_count_tokens(d) for d in request.developer_instructions)
            + sum(_count_tokens(p.text) for p in request.untrusted)
            + _count_tokens(request.user_message)
        )
        output_tokens = _count_tokens(text)
        cost = (prompt_tokens + output_tokens) / 1000.0 * request.profile.cost_per_1k_tokens
        return TokenUsage(
            input_tokens=prompt_tokens, output_tokens=output_tokens, cost_units=round(cost, 6)
        )
