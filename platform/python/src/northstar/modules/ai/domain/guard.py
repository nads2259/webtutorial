"""Pure output guard — the last-line data-loss-prevention defense (docs/10 §10/§11, LLM02/LLM08).

Model output is treated as UNTRUSTED (LLM10). Before an answer is disclosed it is scanned for:

* secret-shaped tokens (API keys, private keys, AWS access keys, ``password=...`` etc.) — even if a
  poisoned document tricked the model into echoing one, the guard redacts it, so
  ``sensitive_disclosure_rate`` stays zero; and
* forbidden echoes — distinctive fragments of the system/developer instruction channel, so an
  injected "reveal the system prompt" attack cannot leak privileged instructions (LLM08).

Pure and infrastructure-free. A finding downgrades the answer to a safe refusal at the pipeline.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

_REDACTION = "[REDACTED]"

# Secret-shaped patterns (never exhaustive, but block the corpus's disclosure attempts).
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(
        r"(?i)(api[_-]?key|secret[_-]?key|client[_-]?secret|password|access[_-]?token)"
        r"\s*[:=]\s*\S{8,}"
    ),
)


@dataclass(frozen=True, slots=True)
class GuardResult:
    """The result of guarding a candidate answer before disclosure."""

    safe: bool
    text: str
    findings: tuple[str, ...] = field(default_factory=tuple)


def _redact_secrets(text: str) -> tuple[str, list[str]]:
    findings: list[str] = []
    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern.search(redacted):
            findings.append(f"secret:{pattern.pattern[:24]}")
            redacted = pattern.sub(_REDACTION, redacted)
    return redacted, findings


def guard_output(text: str, *, forbidden_echoes: Iterable[str] = ()) -> GuardResult:
    """Scan ``text`` for secrets and forbidden instruction echoes; redact and flag any finding.

    ``forbidden_echoes`` are distinctive fragments of the privileged instruction channel that must
    never appear in output. Any finding marks the result unsafe so the pipeline refuses rather than
    discloses; the returned ``text`` has every secret redacted as defense-in-depth.
    """
    redacted, findings = _redact_secrets(text)
    lowered = redacted.lower()
    for phrase in forbidden_echoes:
        needle = phrase.strip().lower()
        if needle and needle in lowered:
            findings.append("instruction_echo")
            redacted = re.sub(re.escape(phrase), _REDACTION, redacted, flags=re.IGNORECASE)
            lowered = redacted.lower()
    return GuardResult(safe=not findings, text=redacted, findings=tuple(findings))
