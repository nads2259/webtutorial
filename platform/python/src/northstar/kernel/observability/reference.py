"""No-op reference tracer (tracing disabled) — pure, stdlib-only.

Used as the default :class:`~northstar.kernel.observability.ports.TracerPort` when no telemetry
backend is wired in, so instrumentation code can always call a tracer without a ``None`` check
and without pulling OpenTelemetry into the kernel (LAW-02/LAW-12). It records nothing and never
changes control flow: entering yields a span, exiting ends it, exceptions propagate unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType

from .ports import AttributeValue, Span


class NoOpSpan:
    """A span that does nothing (used when tracing is disabled)."""

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        return None

    def record_exception(self, error: BaseException) -> None:
        return None

    def mark_error(self, description: str) -> None:
        return None

    def __enter__(self) -> NoOpSpan:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return None


class NoOpTracer:
    """A :class:`TracerPort` that produces inert :class:`NoOpSpan` context managers."""

    def start_as_current_span(
        self, name: str, *, attributes: Mapping[str, AttributeValue] | None = None
    ) -> Span:
        return NoOpSpan()
