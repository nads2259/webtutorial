"""Kernel observability ports (tracing) and a no-op reference tracer (NFR-OPS-001).

Pure abstractions only — the kernel never imports OpenTelemetry (LAW-02/LAW-12). Concrete
tracers live in ``northstar.adapters.telemetry_otel``.
"""

from __future__ import annotations

from .ports import AttributeValue, Span, TracerPort
from .reference import NoOpSpan, NoOpTracer

__all__ = [
    "AttributeValue",
    "NoOpSpan",
    "NoOpTracer",
    "Span",
    "TracerPort",
]
