"""OpenTelemetry telemetry adapter (infra allowed here; ADR-015, NFR-OPS-001).

Adapts OpenTelemetry to the kernel :mod:`northstar.kernel.observability.ports` Protocols. The
kernel imports none of this; only processes/adapters do (rule 10).
"""

from __future__ import annotations

from .tracer import (
    DEFAULT_SERVICE_NAME,
    DEFAULT_TRACER_NAME,
    OtelSpan,
    OtelTracer,
    build_tracer,
    build_tracer_provider,
    install_in_memory_exporter,
    instrument_fastapi_app,
)

__all__ = [
    "DEFAULT_SERVICE_NAME",
    "DEFAULT_TRACER_NAME",
    "OtelSpan",
    "OtelTracer",
    "build_tracer",
    "build_tracer_provider",
    "install_in_memory_exporter",
    "instrument_fastapi_app",
]
