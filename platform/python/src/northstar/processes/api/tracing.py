"""Tracing decorator for the command bus (NFR-OPS-001).

Wraps :meth:`CommandBus.dispatch` in a span via the kernel :class:`TracerPort` so every command
routed through the authoritative write path produces one span carrying the capability coordinate,
correlation id and audit outcome. This lives in the process/composition layer (not the kernel):
it depends only on kernel abstractions (the command bus + the tracer port), never on OpenTelemetry
directly, so the kernel stays infrastructure-free (rule 10, LAW-12). Tracing is observation only —
it never changes authorization, dispatch or the returned result, and it re-raises handler/policy
errors unchanged after recording them on the span.
"""

from __future__ import annotations

from northstar.kernel.capabilities import CapabilityDispatcher
from northstar.kernel.context import RequestContext
from northstar.kernel.messaging import Command, CommandBus, CommandResult
from northstar.kernel.observability.ports import TracerPort


class TracingCommandBus(CommandBus):
    """A :class:`CommandBus` that emits one span per dispatched command (Liskov-substitutable)."""

    def __init__(
        self,
        dispatcher: CapabilityDispatcher,
        policy: object,
        audit: object,
        *,
        tracer: TracerPort,
    ) -> None:
        super().__init__(dispatcher, policy, audit)  # type: ignore[arg-type]
        self._tracer = tracer

    def dispatch(self, command: Command, context: RequestContext) -> CommandResult:
        with self._tracer.start_as_current_span(
            f"command {command.capability}",
            attributes={
                "northstar.capability": command.capability,
                "northstar.capability.version": command.version,
                "northstar.policy_action": command.policy_action,
                "northstar.correlation_id": context.correlation_id,
            },
        ) as span:
            try:
                result = super().dispatch(command, context)
            except Exception as exc:
                span.record_exception(exc)
                span.mark_error(exc.__class__.__name__)
                raise
            span.set_attribute("northstar.audit.outcome", result.audit.outcome.value)
            span.set_attribute("northstar.replayed", result.replayed)
            return result
