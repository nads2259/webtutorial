"""Typed domain errors for the assistant module."""

from __future__ import annotations

from northstar.kernel.errors import KernelError


class AssistantError(KernelError):
    """The assistant request is invalid or the model call failed."""
