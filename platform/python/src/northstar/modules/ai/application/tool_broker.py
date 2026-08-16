"""The Tool Broker — the ONE path from a model to a framework capability (docs/10 §8, FR-AI-004).

No model output ever reaches a capability except through :meth:`ToolBroker.invoke`, which enforces,
in order and deny-by-default (ARCH-009, LLM03/ASI01/ASI02):

1. **Allowlist** — the tool must be declared by the active immutable prompt package;
2. **Prohibition** — an A4 tool (credential change, secret disclosure, self-grant, financial
   transfer, moderation bypass) is blocked unconditionally;
3. **Grant** — the AI actor must hold an explicit grant for the tool;
4. **Argument schema** — arguments must validate against the tool's JSON Schema;
5. **Budget** — the per-run call budget must not be exceeded (unbounded-consumption defense);
6. **Approval** — an A3 high-impact tool needs a named approver;
7. **Execute + minimize + trace** — only then run the capability, minimize its output to the
   declared fields, and record cost/outcome.

Retrieved/injected content can never add a tool, a grant or an approval: those come only from the
prompt package and the actor's grants, so an injected "call tool X" instruction is rejected.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import jsonschema

from ..domain.errors import (
    ApprovalRequired,
    ProhibitedActionError,
    ToolArgumentInvalid,
    ToolBudgetExceeded,
    UnauthorizedToolError,
    UndeclaredToolError,
)
from ..domain.model import (
    ApprovalTier,
    PromptPackage,
    ToolCallRecord,
    ToolDefinition,
    ToolGrant,
)
from .ports import ToolCallRequest, ToolExecutionContext, ToolExecutorPort


@dataclass(frozen=True, slots=True)
class ToolInvocationResult:
    """The outcome of an authorized, executed tool call: its trace record + minimized output."""

    record: ToolCallRecord
    output: Mapping[str, object]


def _minimize(output: Mapping[str, object], tool: ToolDefinition) -> Mapping[str, object]:
    """Output minimization: keep only the fields the tool's output schema declares (docs/10 §8)."""
    properties = tool.output_schema.get("properties")
    if isinstance(properties, Mapping):
        return {key: value for key, value in output.items() if key in properties}
    return dict(output)


class ToolBroker:
    """The authoritative broker; the only collaborator that may execute an AI tool (FR-AI-004)."""

    def __init__(
        self,
        *,
        tools: Mapping[str, ToolDefinition],
        grants: Sequence[ToolGrant],
        executor: ToolExecutorPort,
    ) -> None:
        self._tools = dict(tools)
        self._grants: dict[tuple[str, str], ToolGrant] = {
            (grant.granted_to, grant.tool_id): grant for grant in grants
        }
        self._executor = executor

    def invoke(
        self,
        *,
        package: PromptPackage,
        actor_id: str,
        call: ToolCallRequest,
        context: ToolExecutionContext,
        approvals: frozenset[str] = frozenset(),
        call_counts: dict[str, int] | None = None,
    ) -> ToolInvocationResult:
        """Authorize and execute one tool call, or raise a typed governance error."""
        counts = call_counts if call_counts is not None else {}

        # 1. Allowlist: the tool must be declared by the active prompt package AND be a known tool.
        tool = self._tools.get(call.tool_id)
        if tool is None or not package.declares(call.tool_id):
            raise UndeclaredToolError(call.tool_id)

        # 2. Prohibition: an A4 tool can never be invoked by an AI actor.
        tier = tool.approval_tier
        if tier is ApprovalTier.A4_PROHIBITED:
            raise ProhibitedActionError(call.tool_id)

        # 3. Grant: the AI actor must hold an explicit grant (no ambient authority).
        grant = self._grants.get((actor_id, call.tool_id))
        if grant is None:
            raise UnauthorizedToolError(call.tool_id)

        # 4. Argument schema validation.
        try:
            jsonschema.validate(dict(call.arguments), dict(tool.input_schema))
        except jsonschema.ValidationError as exc:
            raise ToolArgumentInvalid(call.tool_id, exc.message) from exc

        # 5. Per-run budget.
        limit = min(tool.max_calls_per_run, grant.max_calls_per_run)
        used = counts.get(call.tool_id, 0)
        if used >= limit:
            raise ToolBudgetExceeded(call.tool_id, limit)

        # 6. Approval obligation for A3 high-impact tools (named approver / dual control).
        if tier is ApprovalTier.A3_HIGH_IMPACT:
            approved = grant.approved_by is not None or call.tool_id in approvals
            if not approved:
                raise ApprovalRequired(call.tool_id, tier.value)

        # 7. Execute, minimize output, record trace/cost.
        counts[call.tool_id] = used + 1
        raw = self._executor.execute(tool=tool, arguments=dict(call.arguments), context=context)
        minimized = _minimize(raw, tool)
        record = ToolCallRecord(
            tool_id=call.tool_id,
            outcome="executed",
            reason_code=None,
            cost_units=tool.cost_units,
        )
        return ToolInvocationResult(record=record, output=minimized)
