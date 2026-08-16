"""Typed, pure AI-governance domain errors (LAW-02/09, rule 60).

The AI module raises explainable, machine-comparable errors rather than bare strings so the
Tool Broker, prompt registry, RAG pipeline and gateway refuse unsafe actions deterministically.
Adapters map these to RFC 9457 problem details at the API edge (rule 30/40); the domain stays
infrastructure-free. Every refusal here is a DEFENSE the red-team corpus must not be able to
bypass (non-waivable GATE-AI-GA metrics).
"""

from __future__ import annotations


class AiGovernanceError(Exception):
    """Base class for AI-governance domain errors (deny-by-default, explainable)."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else self.__class__.__name__


class PromptPackageImmutable(AiGovernanceError):  # noqa: N818 canonical error name
    """A registered prompt package version cannot be mutated or re-registered (FR-AI-002)."""

    def __init__(self, package_id: str, version: str) -> None:
        self.package_id = package_id
        self.version = version
        super().__init__(
            f"prompt package '{package_id}@{version}' is immutable and already registered",
            code="ai.prompt.immutable",
        )


class PromptPackageNotFound(AiGovernanceError):  # noqa: N818 canonical error name
    """A prompt package version was requested but is not registered."""

    def __init__(self, package_id: str, version: str) -> None:
        self.package_id = package_id
        self.version = version
        super().__init__(
            f"prompt package '{package_id}@{version}' is not registered",
            code="ai.prompt.not_found",
        )


class RuntimePromptConcatenation(AiGovernanceError):  # noqa: N818 canonical error name
    """Instruction text was supplied at runtime instead of from the registry (FR-AI-002)."""

    def __init__(self) -> None:
        super().__init__(
            "runtime instruction concatenation is prohibited; instructions must come from an "
            "immutable prompt package in the registry",
            code="ai.prompt.runtime_concat",
        )


class UndeclaredToolError(AiGovernanceError):
    """The model requested a tool the active prompt package never declared (FR-AI-004)."""

    def __init__(self, tool_id: str) -> None:
        self.tool_id = tool_id
        super().__init__(
            f"tool '{tool_id}' is not declared by the active prompt package",
            code="ai.tool.undeclared",
        )


class UnauthorizedToolError(AiGovernanceError):
    """The AI actor holds no grant for the requested tool (FR-AI-004, ARCH-009)."""

    def __init__(self, tool_id: str) -> None:
        self.tool_id = tool_id
        super().__init__(
            f"the AI actor is not granted tool '{tool_id}'",
            code="ai.tool.unauthorized",
        )


class ToolArgumentInvalid(AiGovernanceError):  # noqa: N818 canonical error name
    """Tool call arguments failed the tool's declared input schema (FR-AI-004)."""

    def __init__(self, tool_id: str, detail: str) -> None:
        self.tool_id = tool_id
        self.detail = detail
        super().__init__(
            f"arguments for tool '{tool_id}' are invalid: {detail}",
            code="ai.tool.args_invalid",
        )


class ApprovalRequired(AiGovernanceError):  # noqa: N818 canonical error name
    """A high-impact (A3) tool/action needs a named approver that was not supplied (FR-AI-008)."""

    def __init__(self, tool_id: str, tier: str) -> None:
        self.tool_id = tool_id
        self.tier = tier
        super().__init__(
            f"tool '{tool_id}' is {tier} and requires human approval before invocation",
            code="ai.approval.required",
        )


class ProhibitedActionError(AiGovernanceError):
    """An A4 prohibited action (credential change, secret disclosure, self-grant, financial
    transfer, moderation bypass) was requested and is blocked unconditionally (FR-AI-008)."""

    def __init__(self, tool_id: str) -> None:
        self.tool_id = tool_id
        super().__init__(
            f"tool '{tool_id}' is a prohibited (A4) action and can never be invoked by an AI actor",
            code="ai.action.prohibited",
        )


class ToolBudgetExceeded(AiGovernanceError):  # noqa: N818 canonical error name
    """The per-run tool-call budget was exhausted (unbounded-consumption defense, LLM06)."""

    def __init__(self, tool_id: str, limit: int) -> None:
        self.tool_id = tool_id
        self.limit = limit
        super().__init__(
            f"tool '{tool_id}' exceeded the per-run call budget of {limit}",
            code="ai.tool.budget_exceeded",
        )


class BudgetScopeExceeded(AiGovernanceError):  # noqa: N818 canonical error name
    """A cost/budget limit was exceeded at an applicable scope (FR-AI-008, LLM10 defense).

    Multi-scope enforcement: a request whose projected spend exceeds ANY applicable budget
    (per-actor, per-tenant, per-workflow/campaign) is rejected with this typed error naming the
    exceeded ``scope``/``scope_id`` and the ``limit`` it would breach. The per-run tool-call budget
    is enforced separately by :class:`ToolBudgetExceeded`.
    """

    def __init__(self, scope: str, scope_id: str, limit: float, projected: float) -> None:
        self.scope = scope
        self.scope_id = scope_id
        self.limit = limit
        self.projected = projected
        super().__init__(
            f"projected AI spend {projected} for {scope} '{scope_id}' exceeds the budget "
            f"limit of {limit}",
            code="ai.budget.exceeded",
        )


class BudgetLimiterUnavailable(AiGovernanceError):  # noqa: N818 canonical error name
    """The budget ledger/limiter could not be consulted; the cost-sensitive path fails SAFE.

    A cost-sensitive AI request must never proceed while the budget limiter is unavailable (that
    would be an unbounded-consumption bypass), so the request is denied deterministically rather
    than fail-open (FR-AI-008, rule 50 deny-by-default).
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(
            f"AI budget limiter is unavailable; denying the cost-sensitive request: {detail}",
            code="ai.budget.limiter_unavailable",
        )


class MemoryCorrectionInvalid(AiGovernanceError):  # noqa: N818 canonical error name
    """A memory correction/amend was requested with empty replacement content (FR-AI-006)."""

    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id
        super().__init__(
            f"correction for memory '{memory_id}' requires non-empty replacement content",
            code="ai.memory.correction_invalid",
        )


class NoApprovedProviderError(AiGovernanceError):
    """No approved model provider can serve the required data classification without a silent
    downgrade to a less-approved provider (FR-AI-001)."""

    def __init__(self, profile_id: str, classification: str) -> None:
        self.profile_id = profile_id
        self.classification = classification
        super().__init__(
            f"no approved fallback for profile '{profile_id}' permits classification "
            f"'{classification}'; refusing to silently downgrade",
            code="ai.gateway.no_approved_provider",
        )


class DataClassificationNotAllowed(AiGovernanceError):  # noqa: N818 canonical error name
    """The selected model profile is not approved for the request's data classification."""

    def __init__(self, profile_id: str, classification: str) -> None:
        self.profile_id = profile_id
        self.classification = classification
        super().__init__(
            f"model profile '{profile_id}' is not approved for classification '{classification}'",
            code="ai.gateway.classification_denied",
        )


class MemoryNotFound(AiGovernanceError):  # noqa: N818 canonical error name
    """A memory record was requested for deletion/read but does not exist for this owner."""

    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id
        super().__init__(
            f"memory record '{memory_id}' does not exist for this owner/tenant",
            code="ai.memory.not_found",
        )


class AiInvariantViolation(AiGovernanceError):  # noqa: N818 canonical error name
    """A pure AI value-object invariant was violated."""

    def __init__(self, message: str, *, code: str = "ai.invariant") -> None:
        super().__init__(message, code=code)
