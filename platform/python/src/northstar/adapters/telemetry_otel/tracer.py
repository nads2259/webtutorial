"""OpenTelemetry adapter implementing the kernel :class:`TracerPort` (ADR-015, NFR-OPS-001).

Infrastructure is allowed here (rule 10): this is the *only* place OpenTelemetry is imported.
It adapts an OTel ``Tracer`` to the kernel-side :class:`~northstar.kernel.observability.ports`
Protocols and provides helpers to build a :class:`TracerProvider`, install an **in-memory** span
exporter for tests, and instrument a FastAPI app. The exporter/collector stays replaceable — no
process forces a global provider, so callers inject the provider explicitly (no ambient state).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from types import TracebackType
from typing import TYPE_CHECKING

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span as TraceSpan
from opentelemetry.trace import Status, StatusCode, Tracer

from northstar.kernel.observability.ports import AttributeValue

if TYPE_CHECKING:
    from fastapi import FastAPI

DEFAULT_SERVICE_NAME = "northstar-api"
DEFAULT_TRACER_NAME = "northstar"


class OtelSpan:
    """Adapts an OTel ``start_as_current_span`` context manager to the kernel :class:`Span`."""

    def __init__(self, cm: AbstractContextManager[TraceSpan]) -> None:
        self._cm = cm
        self._span: TraceSpan | None = None

    def __enter__(self) -> OtelSpan:
        self._span = self._cm.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return bool(self._cm.__exit__(exc_type, exc, traceback))

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        if self._span is not None:
            self._span.set_attribute(key, value)

    def record_exception(self, error: BaseException) -> None:
        if self._span is not None:
            self._span.record_exception(error)

    def mark_error(self, description: str) -> None:
        if self._span is not None:
            self._span.set_status(Status(StatusCode.ERROR, description))


class OtelTracer:
    """A :class:`~northstar.kernel.observability.ports.TracerPort` backed by an OTel ``Tracer``."""

    def __init__(self, tracer: Tracer) -> None:
        self._tracer = tracer

    def start_as_current_span(
        self, name: str, *, attributes: Mapping[str, AttributeValue] | None = None
    ) -> OtelSpan:
        return OtelSpan(self._tracer.start_as_current_span(name, attributes=dict(attributes or {})))


def build_tracer_provider(
    *, service_name: str = DEFAULT_SERVICE_NAME, exporter: SpanExporter | None = None
) -> TracerProvider:
    """Build a :class:`TracerProvider` tagged with ``service.name``.

    When ``exporter`` is supplied a :class:`SimpleSpanProcessor` is attached so spans are exported
    synchronously (deterministic for tests). With no exporter the provider still creates spans but
    exports nowhere — the exporter/collector is a replaceable deployment concern (ADR-015).
    """
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def install_in_memory_exporter(provider: TracerProvider) -> InMemorySpanExporter:
    """Attach and return an in-memory span exporter (tests assert on ``get_finished_spans``)."""
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def build_tracer(provider: TracerProvider, *, name: str = DEFAULT_TRACER_NAME) -> OtelTracer:
    """Return an :class:`OtelTracer` bound to ``provider`` (no global provider is set)."""
    return OtelTracer(provider.get_tracer(name))


def instrument_fastapi_app(app: FastAPI, *, tracer_provider: TracerProvider) -> None:
    """Instrument ``app`` so each HTTP request produces a server span under ``tracer_provider``."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider)
