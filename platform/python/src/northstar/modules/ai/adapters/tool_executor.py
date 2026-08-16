"""Tool executor adapters implementing ``ToolExecutorPort`` (the broker's execution seam).

The Tool Broker calls an executor ONLY after allowlist + grant + arg-schema + approval checks pass;
an executor therefore never sees an unauthorized, undeclared or prohibited call (FR-AI-004). A real
executor dispatches the tool's mapped application capability on the kernel command/query bus; the
reference build ships a callable-backed executor so read tools return a bounded, minimizable result
without wiring every downstream capability.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..application.ports import ToolExecutionContext
from ..domain.model import ToolDefinition

ToolCallable = Callable[[Mapping[str, object], ToolExecutionContext], Mapping[str, object]]


class CallableToolExecutor:
    """Executes tools via a registered callable per ``tool_id`` (default: a bounded ack)."""

    def __init__(self, handlers: Mapping[str, ToolCallable] | None = None) -> None:
        self._handlers: dict[str, ToolCallable] = dict(handlers or {})

    def register(self, tool_id: str, handler: ToolCallable) -> None:
        self._handlers[tool_id] = handler

    def execute(
        self,
        *,
        tool: ToolDefinition,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> Mapping[str, object]:
        handler = self._handlers.get(tool.tool_id)
        if handler is None:
            return {"status": "ok", "tool_id": tool.tool_id}
        return handler(arguments, context)
