"""Tracing ports for kernel-side observability (NFR-OPS-001, LAW-12).

Pure abstractions only (LAW-02, rule 10): **no** OpenTelemetry, SQLAlchemy or other
infrastructure import lives here. The kernel/application layers depend on the small
:class:`TracerPort`/:class:`Span` Protocols so a command dispatch can be traced without knowing
which telemetry backend (if any) is wired in. Concrete tracers are adapters behind this port —
see :mod:`northstar.adapters.telemetry_otel` (OpenTelemetry) and
:mod:`northstar.kernel.observability.reference` (a no-op reference used when tracing is disabled).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Protocol, runtime_checkable

AttributeValue = str | int | float | bool
"""The value types a span attribute may take (mirrors the OTel attribute value contract)."""


@runtime_checkable
class Span(Protocol):
    """A single unit of work in a trace, used as a context manager.

    Entering starts (or activates) the span; exiting ends it. Implementations MUST end the span
    on ``__exit__`` even when the block raised, so an error never leaks an unfinished span.
    """

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        """Attach a single typed attribute to the span."""
        ...

    def record_exception(self, error: BaseException) -> None:
        """Record ``error`` as a span event (does not itself end the span)."""
        ...

    def mark_error(self, description: str) -> None:
        """Mark the span's status as an error with an explainable ``description``."""
        ...

    def __enter__(self) -> Span: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class TracerPort(Protocol):
    """Starts spans for traced operations (deny-nothing: tracing never changes behavior).

    ``start_as_current_span`` returns a :class:`Span` context manager that is also installed as
    the *current* span for its duration, so nested spans (e.g. an HTTP request span wrapping a
    command-dispatch span) form a parent/child relationship when the adapter supports context
    propagation.
    """

    def start_as_current_span(
        self, name: str, *, attributes: Mapping[str, AttributeValue] | None = None
    ) -> Span: ...
