"""Ports (abstractions) for the AI application layer (rule 10/20, DIP, ARCH-009).

Every infrastructure seam is a Protocol so the domain and capabilities stay infrastructure-free
and the AI actor holds no ambient authority:

* :class:`ModelGatewayPort` — the provider/model seam. The reference adapter is a DETERMINISTIC
  mock (no external API); a real provider is a straight adapter swap (FR-AI-001).
* :class:`ToolExecutorPort` — the seam the Tool Broker uses to run the underlying application
  capability AFTER authorization; the broker is the ONLY path model -> capability (FR-AI-004).
* :class:`PromptRegistryPort` — immutable versioned prompt packages (FR-AI-002).
* :class:`RetrievalPort` — retrieve via the retrieval module's ACL-in-query capability (FR-AI-005).
* :class:`MemoryRepositoryPort` / :class:`TraceRepositoryPort` — purpose-limited memory (FR-AI-006)
  and interaction provenance (FR-AI-009).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain.budgets import BudgetLimit, CostEntry
from ..domain.model import (
    Citation,
    InteractionTrace,
    MemoryRecord,
    ModelProfile,
    PassageRef,
    PromptPackage,
    PromptPackageRef,
    TokenUsage,
    ToolDefinition,
    UntrustedPassage,
)


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """A tool invocation the model asked for (routed through the broker, never run directly)."""

    tool_id: str
    version: str
    arguments: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """The provider-neutral request handed to the model gateway.

    Channel separation is explicit: ``system_instruction`` and ``developer_instructions`` are the
    privileged channel resolved from the immutable prompt package; ``untrusted`` and
    ``user_message`` are UNTRUSTED data. ``declared_tools`` is the package allowlist; ``retrieved``
    are the ACL-cleared passages available for grounding/citation.
    """

    profile: ModelProfile
    system_instruction: str
    developer_instructions: tuple[str, ...]
    untrusted: tuple[UntrustedPassage, ...]
    user_message: str
    retrieved: tuple[PassageRef, ...]
    declared_tools: tuple[ToolDefinition, ...]


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """The provider-neutral result the gateway returns (untrusted until guarded/verified)."""

    text: str
    tool_calls: tuple[ToolCallRequest, ...] = field(default_factory=tuple)
    citations: tuple[Citation, ...] = field(default_factory=tuple)
    usage: TokenUsage = field(
        default_factory=lambda: TokenUsage(input_tokens=0, output_tokens=0, cost_units=0.0)
    )
    finish_reason: str = "stop"


@runtime_checkable
class ModelGatewayPort(Protocol):
    """Provider-neutral generation seam. Implementations never leak provider names to the domain."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate a response for ``request`` (deterministic in the reference mock adapter)."""
        ...


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """The authenticated scope a tool executes within (tenant + acting AI actor + delegation)."""

    organization_id: str
    actor_id: str
    delegated_by: str | None
    correlation_id: str


@runtime_checkable
class ToolExecutorPort(Protocol):
    """Runs the underlying application capability for an ALREADY-authorized tool call.

    The Tool Broker calls this only after allowlist + grant + arg-schema + approval checks pass;
    an executor never sees an unauthorized, undeclared or prohibited call (FR-AI-004).
    """

    def execute(
        self,
        *,
        tool: ToolDefinition,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> Mapping[str, object]:
        """Execute the tool's capability and return its (to-be-minimized) output."""
        ...


@runtime_checkable
class PromptRegistryPort(Protocol):
    """Stores and serves IMMUTABLE versioned prompt packages (FR-AI-002)."""

    def register(self, package: PromptPackage) -> None:
        """Register a package version; re-registering an existing version is rejected."""
        ...

    def get(self, ref: PromptPackageRef) -> PromptPackage:
        """Return the package for ``ref`` or raise ``PromptPackageNotFound``."""
        ...


@runtime_checkable
class RetrievalPort(Protocol):
    """Retrieves ACL-cleared passages for the authenticated actor (FR-AI-005).

    Implementations authorize BEFORE retrieval and re-check ACLs before returning any passage by
    delegating to the retrieval module's ``retrieval.search`` capability (ACL inside the query).
    """

    def search(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        text: str,
        top_k: int,
        locale: str,
    ) -> tuple[PassageRef, ...]:
        """Return the ACL-cleared passages for ``text`` in the caller's scope."""
        ...


@runtime_checkable
class MemoryRepositoryPort(Protocol):
    """Persists purpose-limited, correctable, deletable AI memory records (tenant + owner scoped).

    ``list_for_owner`` returns only the ACTIVE (non-superseded) heads for the user-visible control;
    ``export_for_owner`` returns the full history (active + superseded revisions) for a portable
    bundle; ``supersede`` atomically records an audited correction (a new head that supersedes the
    prior record); ``erase_for_owner`` removes ALL of the owner's memory for a reset/DSAR erase so
    the deletion residue is zero (FR-AI-006).
    """

    def add(self, record: MemoryRecord) -> None: ...

    def get(
        self, *, organization_id: str, owner_id: str, memory_id: str
    ) -> MemoryRecord | None: ...

    def list_for_owner(self, *, organization_id: str, owner_id: str) -> Sequence[MemoryRecord]: ...

    def export_for_owner(
        self, *, organization_id: str, owner_id: str
    ) -> Sequence[MemoryRecord]: ...

    def supersede(self, *, previous: MemoryRecord, correction: MemoryRecord) -> None: ...

    def delete(self, *, organization_id: str, owner_id: str, memory_id: str) -> bool: ...

    def erase_for_owner(self, *, organization_id: str, owner_id: str) -> int: ...

    def count_for_owner(self, *, organization_id: str, owner_id: str) -> int: ...


@runtime_checkable
class TraceRepositoryPort(Protocol):
    """Persists per-interaction provenance traces (model/provider/prompt/tools/cost, FR-AI-009)."""

    def record(self, trace: InteractionTrace) -> None: ...


@runtime_checkable
class BudgetLedgerPort(Protocol):
    """Persists AI budgets + recorded provider costs, tenant-scoped (FR-AI-008, LLM10 defense).

    ``limits_for`` returns the applicable multi-scope budgets (per-actor/tenant/workflow),
    most-specific-first; ``spent`` returns the already-recorded spend for one scope; ``record``
    appends a per-interaction cost entry; ``total_recorded`` returns the ledger total used by the
    provider-cost reconciliation.
    """

    def limits_for(
        self, *, organization_id: str, actor_id: str, workflow_id: str | None
    ) -> Sequence[BudgetLimit]: ...

    def spent(self, *, organization_id: str, scope: str, scope_id: str) -> float: ...

    def record(self, entry: CostEntry) -> None: ...

    def total_recorded(self, *, organization_id: str, scope: str, scope_id: str) -> float: ...
