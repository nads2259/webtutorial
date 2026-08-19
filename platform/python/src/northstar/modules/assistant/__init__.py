"""Assistant: a retrieval-grounded Q&A helper wired to an external OpenAI-compatible chat model.

A pragmatic companion to the fully-governed :mod:`northstar.modules.ai` pipeline: the single
authoritative action ``assistant.ask`` retrieves relevant curriculum passages (reusing the released
``retrieval.search`` capability for grounding) and asks a configured chat model, returning the answer
plus its sources. The model endpoint is a swappable adapter behind :class:`ChatModelPort`; the active
model is admin-selectable from a preset registry (configured from ``models.txt``-style settings).
"""

from __future__ import annotations

from .application.capabilities import (
    CAP_ASK,
    CAP_VERSION,
    ASSISTANT_CAPABILITIES,
    Ask,
    AskCommand,
)

__all__ = ["ASSISTANT_CAPABILITIES", "CAP_ASK", "CAP_VERSION", "Ask", "AskCommand"]
