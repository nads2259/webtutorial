"""Pure AI-governance value objects (docs/10, rule 60, ARCH-009).

Infrastructure-free (rule 10, LAW-02). These frozen value objects encode the governance
invariants for a scoped AI actor: the model/provider gateway profile (with a no-silent-downgrade
fallback rule, FR-AI-001), immutable prompt-package references (FR-AI-002), actor profiles and
tool grants (FR-AI-003/004), the A0-A4 approval tiers (FR-AI-008), citations bound to a retrieved
passage (FR-AI-005/007) and purpose-limited memory classes (FR-AI-006).

Nothing here reaches a database, a network or a provider SDK; the AI actor holds NO ambient
authority (ARCH-009). Capabilities are reached only through the Tool Broker, and every value here
is the deterministic basis the red-team defenses rest on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .errors import AiInvariantViolation


class ActorProfile(StrEnum):
    """The eight canonical AI actor profiles (mirror prompt-package ``actor_profile``, docs/10)."""

    LEARNER_TUTOR = "learner_tutor"
    AUTHOR_COPILOT = "author_copilot"
    RESEARCH_ASSISTANT = "research_assistant"
    REVIEWER_ASSISTANT = "reviewer_assistant"
    MODERATOR_ASSISTANT = "moderator_assistant"
    ADMIN_ASSISTANT = "admin_assistant"
    SIMULATION_ACTOR = "simulation_actor"
    BACKGROUND_AGENT = "background_agent"


class ApprovalTier(StrEnum):
    """Human-approval tiers A0-A4 (docs/10 §9). Higher is more dangerous; A4 is prohibited."""

    A0_READ_ONLY = "A0"
    A1_DRAFT = "A1"
    A2_REVERSIBLE = "A2"
    A3_HIGH_IMPACT = "A3"
    A4_PROHIBITED = "A4"


class SideEffect(StrEnum):
    """A tool's side-effect class (mirrors ai-tool.schema.json ``side_effect``)."""

    READ = "read"
    DRAFT = "draft"
    REVERSIBLE_WRITE = "reversible_write"
    HIGH_IMPACT = "high_impact"
    PROHIBITED = "prohibited"


class ToolApproval(StrEnum):
    """A tool's approval requirement (mirrors ai-tool.schema.json ``approval``)."""

    NONE = "none"
    USER_CONFIRMATION = "user_confirmation"
    NAMED_APPROVER = "named_approver"
    DUAL_CONTROL = "dual_control"
    NOT_ALLOWED = "not_allowed"


class MemoryClass(StrEnum):
    """Memory classes (docs/10 §7). Each record is purpose-limited and deletable (FR-AI-006)."""

    CONVERSATION = "conversation"
    EPISODIC = "episodic"
    LEARNING_PROFILE = "learning_profile"
    ORGANIZATION_KNOWLEDGE = "organization_knowledge"
    AGENT_WORKFLOW = "agent_workflow"
    SEMANTIC_INDEX = "semantic_index"


class MemoryPolicy(StrEnum):
    """How a prompt package may retain memory (mirrors prompt-package ``memory_policy``)."""

    NONE = "none"
    SESSION = "session"
    USER_OPT_IN = "user_opt_in"
    WORKFLOW = "workflow"


_SIDE_EFFECT_TIER: dict[SideEffect, ApprovalTier] = {
    SideEffect.READ: ApprovalTier.A0_READ_ONLY,
    SideEffect.DRAFT: ApprovalTier.A1_DRAFT,
    SideEffect.REVERSIBLE_WRITE: ApprovalTier.A2_REVERSIBLE,
    SideEffect.HIGH_IMPACT: ApprovalTier.A3_HIGH_IMPACT,
    SideEffect.PROHIBITED: ApprovalTier.A4_PROHIBITED,
}


def tier_for_side_effect(side_effect: SideEffect) -> ApprovalTier:
    """Map a tool's side-effect class to its human-approval tier (docs/10 §8/§9)."""
    return _SIDE_EFFECT_TIER[side_effect]


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """A provider/model gateway profile (docs/10 §3, FR-AI-001).

    Records ``provider``/``model``/``region``, context/token ``limits``, data ``retention`` terms,
    the ``allowed_classifications`` the provider is approved for, cost/rate limits and an ordered
    ``fallback`` of other profile ids. ``approved`` is the governance gate: an unapproved profile
    must never silently serve traffic. Fallback resolution (see :func:`resolve_gateway`) refuses to
    downgrade to a provider not approved for the required classification.
    """

    profile_id: str
    provider: str
    model: str
    region: str
    max_input_tokens: int
    max_output_tokens: int
    retention: str
    allowed_classifications: frozenset[str]
    cost_per_1k_tokens: float
    max_requests_per_minute: int
    approved: bool = True
    fallback: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id or not self.provider or not self.model:
            raise AiInvariantViolation(
                "model profile requires profile_id, provider and model",
                code="ai.gateway.profile_id",
            )
        if self.max_input_tokens <= 0 or self.max_output_tokens <= 0:
            raise AiInvariantViolation(
                "model profile token limits must be positive", code="ai.gateway.limits"
            )
        if not self.allowed_classifications:
            raise AiInvariantViolation(
                "model profile must declare at least one allowed classification",
                code="ai.gateway.classifications",
            )

    def permits(self, classification: str) -> bool:
        """Return ``True`` iff this approved profile may process ``classification``."""
        return self.approved and classification in self.allowed_classifications


def resolve_gateway(
    *,
    primary: ModelProfile,
    classification: str,
    catalog: Mapping[str, ModelProfile],
) -> ModelProfile:
    """Resolve the model profile to use, honoring the no-silent-downgrade rule (FR-AI-001).

    If ``primary`` is approved for ``classification`` it is used. Otherwise the ordered
    ``primary.fallback`` is consulted and the FIRST fallback that is itself approved for
    ``classification`` is returned; a fallback approved for fewer/other classifications is skipped
    (never a silent downgrade). If nothing qualifies, :class:`NoApprovedProviderError` is raised —
    the gateway fails closed rather than routing restricted data to a less-approved provider.
    """
    from .errors import NoApprovedProviderError

    if primary.permits(classification):
        return primary
    for fallback_id in primary.fallback:
        candidate = catalog.get(fallback_id)
        if candidate is not None and candidate.permits(classification):
            return candidate
    raise NoApprovedProviderError(primary.profile_id, classification)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """An AI tool contract (mirrors ai-tool.schema.json) the Tool Broker validates against.

    A tool is a governed seam onto ONE application capability. ``side_effect`` and ``approval``
    determine the approval tier; ``input_schema`` validates arguments; ``limits`` bound cost and
    per-run call count. The AI actor may only reach the underlying capability through the broker.
    """

    tool_id: str
    version: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    side_effect: SideEffect
    approval: ToolApproval
    capability: str
    capability_version: str
    required_permissions: tuple[str, ...] = ()
    max_calls_per_run: int = 1
    cost_units: float = 0.0
    timeout_ms: int = 5000

    def __post_init__(self) -> None:
        if not self.tool_id or not self.version:
            raise AiInvariantViolation(
                "tool definition requires tool_id and version", code="ai.tool.id"
            )
        if self.max_calls_per_run < 1:
            raise AiInvariantViolation("tool max_calls_per_run must be >= 1", code="ai.tool.limits")

    @property
    def approval_tier(self) -> ApprovalTier:
        """The effective approval tier: ``not_allowed`` or a ``prohibited`` side-effect is A4."""
        if self.approval is ToolApproval.NOT_ALLOWED:
            return ApprovalTier.A4_PROHIBITED
        return tier_for_side_effect(self.side_effect)


@dataclass(frozen=True, slots=True)
class ToolGrant:
    """A grant that a specific AI actor may use a specific tool up to a per-run budget (FR-AI-004).

    Grants are explicit and earned; the broker rejects any tool call without a matching grant
    (deny-by-default, ARCH-009). ``approved_by`` records the named human approver for an A3 tool.
    """

    tool_id: str
    granted_to: str
    max_calls_per_run: int = 1
    approved_by: str | None = None


@dataclass(frozen=True, slots=True)
class AIActorProfile:
    """A scoped AI actor: its profile, default authority tier and the tools it may be granted.

    An actor is NEVER a superuser (ARCH-009, LAW-09). ``default_authority`` caps the tier the actor
    may operate at without escalation; ``allowed_data_classifications`` bounds what data it may see;
    ``memory_policy`` bounds retention. ``eu_ai_act_tier`` records the regulatory tier
    (``security/ai-eu-ai-act-tiering.csv``).
    """

    actor_id: str
    profile: ActorProfile
    default_authority: ApprovalTier
    eu_ai_act_tier: str
    allowed_data_classifications: frozenset[str]
    memory_policy: MemoryPolicy = MemoryPolicy.NONE

    def __post_init__(self) -> None:
        if not self.actor_id:
            raise AiInvariantViolation("actor_id must be non-empty", code="ai.actor.id")


@dataclass(frozen=True, slots=True)
class PromptPackageRef:
    """An immutable reference to a registered prompt package version (FR-AI-002).

    The concrete instruction/schema text lives in the registry keyed by ``(package_id, version)``;
    a reference never carries mutable instruction strings, so provenance stays stable.
    """

    package_id: str
    version: str

    def __post_init__(self) -> None:
        if not self.package_id or not self.version:
            raise AiInvariantViolation(
                "prompt package ref requires package_id and version", code="ai.prompt.ref"
            )

    @property
    def key(self) -> str:
        return f"{self.package_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class PromptPackage:
    """A resolved, IMMUTABLE prompt package (docs/10 §4, FR-AI-002).

    Holds the actor profile, the system/developer instructions (already resolved from the package,
    never concatenated at runtime), the declared tools (the allowlist the broker enforces), the
    retrieval and memory policies and the evaluation suite id. Instances are frozen; the registry
    refuses to re-register or mutate an existing ``(package_id, version)``.
    """

    package_id: str
    version: str
    actor_profile: ActorProfile
    purpose: str
    system_instruction: str
    developer_instructions: tuple[str, ...]
    declared_tools: tuple[str, ...]
    retrieval_profile: str | None
    memory_policy: MemoryPolicy
    evaluation_suite: str
    status: str = "approved"

    def __post_init__(self) -> None:
        if not self.package_id or not self.version:
            raise AiInvariantViolation(
                "prompt package requires package_id and version", code="ai.prompt.id"
            )
        if not self.system_instruction.strip():
            raise AiInvariantViolation(
                "prompt package requires a non-empty system instruction", code="ai.prompt.system"
            )

    @property
    def ref(self) -> PromptPackageRef:
        return PromptPackageRef(package_id=self.package_id, version=self.version)

    def declares(self, tool_id: str) -> bool:
        """Return ``True`` iff ``tool_id`` is in this package's declared tool allowlist."""
        return tool_id in self.declared_tools


@dataclass(frozen=True, slots=True)
class PassageRef:
    """A retrieved, ACL-cleared passage the RAG pipeline may quote and cite (FR-AI-005/007).

    Carries the stable citation identity (``object_id``/``revision_id``/``block_id``/``chunk_id``)
    and the passage ``text``. It only ever holds passages the retrieval module returned for the
    authenticated actor, so it can never carry another tenant's or another subject's private text.
    """

    object_id: str
    revision_id: str
    block_id: str
    chunk_id: str
    text: str
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class Citation:
    """A citation binding an answer claim to exactly one retrieved passage (docs/10 §5).

    A citation is ACCEPTED only if the cited ``chunk_id`` was actually retrieved AND the passage
    supports the ``claim`` (see :mod:`.citations`). A model emitting an id is not sufficient; a
    fabricated or unsupported citation is rejected (FR-AI-007, citation_fabrication_rate <= 0.01).
    """

    object_id: str
    revision_id: str
    block_id: str
    chunk_id: str
    claim: str


@dataclass(frozen=True, slots=True)
class UntrustedPassage:
    """Retrieved/user/extension content carried as UNTRUSTED DATA into context (docs/10 §10).

    Channel separation: this text is never merged into the system/developer instruction channel and
    any instructions inside it MUST NOT alter tool grants or data access (indirect-injection
    defense, LLM01). ``label`` records provenance for the assembled, delimited context.
    """

    label: str
    chunk_id: str
    text: str


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """A purpose-limited, deletable memory record (docs/10 §7, FR-AI-006).

    Every record has an ``owner_id`` + tenant scope, a ``memory_class``, an explicit ``purpose`` and
    ``classification``, a ``retention`` marker and an ``inferred`` flag (inferred facts are labeled,
    not treated as user-declared truth). Records are always deletable by the owner.
    """

    memory_id: str
    organization_id: str
    owner_id: str
    memory_class: MemoryClass
    purpose: str
    classification: str
    content: str
    retention: str = "session"
    inferred: bool = False
    supersedes: str | None = None
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        if not self.memory_id or not self.owner_id or not self.organization_id:
            raise AiInvariantViolation(
                "memory record requires memory_id, owner_id and organization_id",
                code="ai.memory.identity",
            )
        if not self.purpose.strip():
            raise AiInvariantViolation(
                "memory record requires an explicit purpose (purpose limitation)",
                code="ai.memory.purpose",
            )

    @property
    def active(self) -> bool:
        """A memory record is the current head iff it has not been superseded by a correction."""
        return self.superseded_by is None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token/cost accounting for one gateway interaction (FR-AI-009, cost provenance)."""

    input_tokens: int
    output_tokens: int
    cost_units: float


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """The recorded outcome of one attempted tool call via the broker (trace/cost, FR-AI-009)."""

    tool_id: str
    outcome: str
    reason_code: str | None
    cost_units: float


@dataclass(frozen=True, slots=True)
class InteractionTrace:
    """Provenance for one AI interaction (FR-AI-009, ASI05 untraceability defense).

    Records the actor, model/provider, prompt package version, tools attempted/executed, token
    cost and the policy outcome so every interaction is fully traceable and auditable.
    """

    trace_id: str
    organization_id: str
    actor_id: str
    actor_profile: ActorProfile
    provider: str
    model: str
    prompt_package: str
    usage: TokenUsage
    tool_calls: tuple[ToolCallRecord, ...] = field(default_factory=tuple)
    citations_valid: int = 0
    citations_rejected: int = 0
    refused: bool = False
