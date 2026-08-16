"""AI capabilities: the governed RAG pipeline and purpose-limited memory (LAW-04/09, docs/10).

One authoritative implementation per action, run through the kernel buses (deny-by-default
authorized + audited, rule 50/60/LAW-14):

* ``ai.answer`` (command) is the RAG pipeline (FR-AI-005/007/009): it resolves an immutable prompt
  package, resolves the model gateway with a no-silent-downgrade rule, retrieves via the retrieval
  module (ACL inside the query, authorize-before-retrieval + re-check before disclosure), assembles
  retrieved/user content as UNTRUSTED data (channel separation), generates via the gateway, routes
  every model tool call through the Tool Broker (the only path to a capability), verifies citations
  against the actually-retrieved passages, guards output for secret/instruction leakage, and records
  a provenance trace.
* ``ai.memory.remember`` / ``ai.memory.forget`` (commands) and ``ai.memory.list`` (query) implement
  purpose-limited, deletable memory (FR-AI-006).

Tenant scope and acting subject come from the authenticated :class:`RequestContext`, never the
payload (rule 50). Handlers depend only on :mod:`.ports`, :mod:`.tool_broker` and the pure domain.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from northstar.kernel.audit.ports import AuditOutcome, AuditRecorderPort
from northstar.kernel.context import Actor, ActorType, ResourceRef

from ..domain.citations import verify_citations
from ..domain.errors import (
    AiGovernanceError,
    DataClassificationNotAllowed,
    MemoryCorrectionInvalid,
    MemoryNotFound,
)
from ..domain.guard import guard_output
from ..domain.model import (
    ActorProfile,
    AIActorProfile,
    InteractionTrace,
    MemoryClass,
    MemoryRecord,
    ModelProfile,
    PromptPackage,
    PromptPackageRef,
    ToolCallRecord,
    ToolDefinition,
    UntrustedPassage,
    resolve_gateway,
)
from .budget_guard import BudgetGuard
from .ports import (
    GenerationRequest,
    MemoryRepositoryPort,
    ModelGatewayPort,
    PromptRegistryPort,
    RetrievalPort,
    ToolExecutionContext,
    TraceRepositoryPort,
)
from .tool_broker import ToolBroker

CAP_VERSION = "1.0.0"

CAP_ANSWER = "ai.answer"
CAP_REMEMBER = "ai.memory.remember"
CAP_FORGET = "ai.memory.forget"
CAP_LIST_MEMORY = "ai.memory.list"
CAP_CORRECT_MEMORY = "ai.memory.correct"
CAP_EXPORT_MEMORY = "ai.memory.export"
CAP_RESET_MEMORY = "ai.memory.reset"

AI_CAPABILITIES: tuple[str, ...] = (
    CAP_ANSWER,
    CAP_REMEMBER,
    CAP_FORGET,
    CAP_LIST_MEMORY,
    CAP_CORRECT_MEMORY,
    CAP_EXPORT_MEMORY,
    CAP_RESET_MEMORY,
)

RES_AI = "ai.assistant"
RES_AI_MEMORY = "ai.memory"

IdFactory = Callable[[], str]
Clock = Callable[[], datetime]

_EVENT_MEMORY = "northstar.ai.memory.rights"

_REFUSAL = (
    "I can't help with that request because it would disclose protected information or bypass a "
    "safety control."
)
_UNSUPPORTED = "I don't have a grounded, cited answer for that from the sources I'm allowed to use."


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnswerCommand:
    """Ask the governed AI to answer ``question`` using a named prompt package (FR-AI-005)."""

    package_id: str
    version: str
    question: str
    top_k: int = 5
    locale: str = "en"
    data_classification: str = "public"
    approvals: tuple[str, ...] = ()
    workflow_id: str | None = None


@dataclass(frozen=True, slots=True)
class CitationView:
    object_id: str
    revision_id: str
    block_id: str
    chunk_id: str
    claim: str


@dataclass(frozen=True, slots=True)
class RejectedToolView:
    tool_id: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class RejectedCitationView:
    chunk_id: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class AnswerView:
    """The disclosed answer plus full provenance (docs/10 §11, FR-AI-009).

    ``refused`` is ``True`` when a defense downgraded the answer to a safe refusal.
    ``executed_tools`` lists tools that actually ran through the broker; ``rejected_tools`` records
    blocked calls with a reason; ``rejected_citations`` records dropped fabricated/unsupported ones.
    """

    answer: str
    refused: bool
    citations: tuple[CitationView, ...]
    provider: str
    model: str
    prompt_package: str
    actor_profile: str
    eu_ai_act_tier: str
    trace_id: str
    executed_tools: tuple[str, ...]
    rejected_tools: tuple[RejectedToolView, ...]
    rejected_citations: tuple[RejectedCitationView, ...]
    disclosure_findings: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    cost_units: float


@dataclass(frozen=True, slots=True)
class RememberMemoryCommand:
    memory_class: str
    purpose: str
    classification: str
    content: str
    retention: str = "session"
    inferred: bool = False


@dataclass(frozen=True, slots=True)
class MemoryView:
    memory_id: str
    memory_class: str
    purpose: str
    classification: str
    content: str
    inferred: bool


@dataclass(frozen=True, slots=True)
class RememberMemoryResult:
    memory_id: str


@dataclass(frozen=True, slots=True)
class ForgetMemoryCommand:
    memory_id: str


@dataclass(frozen=True, slots=True)
class ForgetMemoryResult:
    memory_id: str
    deleted: bool


@dataclass(frozen=True, slots=True)
class ListMemoryParameters:
    pass


@dataclass(frozen=True, slots=True)
class MemoryListView:
    records: tuple[MemoryView, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CorrectMemoryCommand:
    memory_id: str
    content: str
    purpose: str | None = None


@dataclass(frozen=True, slots=True)
class CorrectMemoryResult:
    memory_id: str
    superseded_memory_id: str


@dataclass(frozen=True, slots=True)
class ExportMemoryParameters:
    pass


@dataclass(frozen=True, slots=True)
class MemoryExportRecordView:
    memory_id: str
    memory_class: str
    purpose: str
    classification: str
    content: str
    inferred: bool
    active: bool
    supersedes: str | None
    superseded_by: str | None


@dataclass(frozen=True, slots=True)
class MemoryExportView:
    subject_id: str
    generated_at: str
    record_count: int
    records: tuple[MemoryExportRecordView, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "store_id": RES_AI_MEMORY,
            "subject_id": self.subject_id,
            "generated_at": self.generated_at,
            "count": self.record_count,
            "items": [
                {
                    "memory_id": r.memory_id,
                    "memory_class": r.memory_class,
                    "purpose": r.purpose,
                    "classification": r.classification,
                    "content": r.content,
                    "inferred": r.inferred,
                    "active": r.active,
                    "supersedes": r.supersedes,
                    "superseded_by": r.superseded_by,
                }
                for r in self.records
            ],
        }


@dataclass(frozen=True, slots=True)
class ResetMemoryCommand:
    pass


@dataclass(frozen=True, slots=True)
class ResetMemoryResult:
    erased_count: int


# ---------------------------------------------------------------------------
# Invocation helpers (context is authoritative, never the payload — rule 50)
# ---------------------------------------------------------------------------


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    scope = getattr(context, "tenant_scope", None)
    if not scope:
        raise DataClassificationNotAllowed("<none>", "tenant scope missing")
    return scope


def _subject(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    actor = getattr(context, "actor", None)
    subject = getattr(actor, "id", None)
    if not subject:
        raise DataClassificationNotAllowed("<none>", "actor subject missing")
    return subject


def _actor(invocation: object) -> Actor:
    """The authenticated caller recorded in the audit trail (falls back to a subject-only actor)."""
    context = getattr(invocation, "context", None)
    actor = getattr(context, "actor", None)
    if isinstance(actor, Actor):
        return actor
    return Actor(type=ActorType.USER, id=_subject(invocation))


def _correlation(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    return str(getattr(context, "correlation_id", "-"))


def _forbidden_echoes(package: PromptPackage) -> tuple[str, ...]:
    """Distinctive instruction sentences that must never appear in output (LLM08 defense)."""
    phrases: list[str] = []
    for text in (package.system_instruction, *package.developer_instructions):
        for sentence in re.split(r"[.\n]", text):
            stripped = sentence.strip()
            if len(stripped.split()) >= 6:
                phrases.append(stripped)
    return tuple(phrases)


# ---------------------------------------------------------------------------
# RAG pipeline capability
# ---------------------------------------------------------------------------


class Answer:
    """``ai.answer`` — the governed RAG pipeline (FR-AI-005/007/009, GATE-AI-GA).

    Every step is a deny-by-default defense: the prompt package (immutable) is the only instruction
    source, the gateway refuses a silent downgrade, retrieval applies the ACL inside the query, the
    broker is the only route to a capability, citations are verified against retrieved passages and
    output is guarded before disclosure. The model is untrusted; the defenses — not the model — keep
    the zero-leak metrics at zero.
    """

    def __init__(
        self,
        *,
        gateway: ModelGatewayPort,
        broker: ToolBroker,
        registry: PromptRegistryPort,
        retrieval: RetrievalPort,
        traces: TraceRepositoryPort,
        actors: dict[ActorProfile, AIActorProfile],
        model_catalog: dict[str, ModelProfile],
        primary_profile_id: str,
        tools_by_id: dict[str, ToolDefinition],
        id_factory: IdFactory,
        budget_guard: BudgetGuard | None = None,
    ) -> None:
        self._gateway = gateway
        self._broker = broker
        self._registry = registry
        self._retrieval = retrieval
        self._traces = traces
        self._actors = actors
        self._catalog = model_catalog
        self._primary_profile_id = primary_profile_id
        self._tools_by_id = tools_by_id
        self._id_factory = id_factory
        self._budget_guard = budget_guard

    def handle(self, request: object) -> AnswerView:
        cmd = _typed(request, AnswerCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        correlation_id = _correlation(request)

        package = self._registry.get(PromptPackageRef(cmd.package_id, cmd.version))
        actor = self._actors[package.actor_profile]

        # Data-classification scoping (deny-by-default) + no silent provider downgrade (FR-AI-001).
        if cmd.data_classification not in actor.allowed_data_classifications:
            raise DataClassificationNotAllowed(actor.actor_id, cmd.data_classification)
        primary = self._catalog[self._primary_profile_id]
        profile = resolve_gateway(
            primary=primary, classification=cmd.data_classification, catalog=self._catalog
        )

        # Authorize-before-retrieval + ACL-in-query + re-check-before-disclosure (in retrieval).
        passages = self._retrieval.search(
            organization_id=organization_id,
            subject_id=subject_id,
            correlation_id=correlation_id,
            text=cmd.question,
            top_k=cmd.top_k,
            locale=cmd.locale,
        )

        untrusted = tuple(
            UntrustedPassage(label=f"passage:{p.chunk_id}", chunk_id=p.chunk_id, text=p.text)
            for p in passages
        )
        declared_defs = tuple(
            self._tools_by_id[tool_id]
            for tool_id in package.declared_tools
            if tool_id in self._tools_by_id
        )
        gen_request = GenerationRequest(
            profile=profile,
            system_instruction=package.system_instruction,
            developer_instructions=package.developer_instructions,
            untrusted=untrusted,
            user_message=cmd.question,
            retrieved=passages,
            declared_tools=declared_defs,
        )
        generation = self._gateway.generate(gen_request)

        # Multi-scope cost/budget enforcement (per-actor/tenant/workflow); the per-run tool-call
        # budget is enforced separately by the Tool Broker. A request whose projected spend exceeds
        # ANY applicable budget is rejected here with a typed error + audit, and fails safe (a
        # limiter outage denies) before any tool call, disclosure or trace is recorded (FR-AI-008).
        if self._budget_guard is not None:
            self._budget_guard.authorize(
                organization_id=organization_id,
                actor=_actor(request),
                actor_id=actor.actor_id,
                workflow_id=cmd.workflow_id,
                requested_cost=generation.usage.cost_units,
                correlation_id=correlation_id,
            )

        # Route EVERY model tool call through the broker; unauthorized calls are rejected, not run.
        exec_context = ToolExecutionContext(
            organization_id=organization_id,
            actor_id=actor.actor_id,
            delegated_by=subject_id,
            correlation_id=correlation_id,
        )
        executed: list[ToolCallRecord] = []
        rejected_tools: list[RejectedToolView] = []
        call_counts: dict[str, int] = {}
        approvals = frozenset(cmd.approvals)
        for call in generation.tool_calls:
            try:
                result = self._broker.invoke(
                    package=package,
                    actor_id=actor.actor_id,
                    call=call,
                    context=exec_context,
                    approvals=approvals,
                    call_counts=call_counts,
                )
                executed.append(result.record)
            except AiGovernanceError as exc:
                rejected_tools.append(RejectedToolView(tool_id=call.tool_id, reason_code=exc.code))

        # Verify citations against the ACTUALLY-retrieved passages (reject fabricated/unsupported).
        report = verify_citations(generation.citations, passages)
        valid_citations = report.valid
        rejected_citations = tuple(
            RejectedCitationView(chunk_id=v.citation.chunk_id, reason_code=v.reason_code)
            for v in report.rejected
        )

        # Output guard (secret + instruction-echo DLP) before disclosure.
        guard = guard_output(generation.text, forbidden_echoes=_forbidden_echoes(package))
        refused = False
        answer_text = guard.text
        if not guard.safe:
            refused = True
            answer_text = _REFUSAL
        elif generation.citations and not valid_citations:
            # The model attempted to cite but every citation was fabricated/unsupported: refuse
            # rather than present an ungrounded claim (misinformation defense, LLM07).
            refused = True
            answer_text = _UNSUPPORTED

        trace_id = self._id_factory()
        all_records = tuple(executed) + tuple(
            ToolCallRecord(
                tool_id=r.tool_id, outcome="rejected", reason_code=r.reason_code, cost_units=0.0
            )
            for r in rejected_tools
        )
        self._traces.record(
            InteractionTrace(
                trace_id=trace_id,
                organization_id=organization_id,
                actor_id=actor.actor_id,
                actor_profile=package.actor_profile,
                provider=profile.provider,
                model=profile.model,
                prompt_package=package.ref.key,
                usage=generation.usage,
                tool_calls=all_records,
                citations_valid=len(valid_citations),
                citations_rejected=report.rejected_count,
                refused=refused,
            )
        )

        # Record the provider cost for this interaction against the budget ledger (provenance +
        # reconciliation source, FR-AI-008/009). The deterministic mock's provider cost equals the
        # internal accounting figure; a real provider adapter supplies the reported charge.
        if self._budget_guard is not None:
            self._budget_guard.record(
                organization_id=organization_id,
                actor_id=actor.actor_id,
                workflow_id=cmd.workflow_id,
                cost_units=generation.usage.cost_units,
                provider_cost=generation.usage.cost_units,
                provider=profile.provider,
                correlation_id=correlation_id,
            )

        return AnswerView(
            answer=answer_text,
            refused=refused,
            citations=tuple(
                CitationView(
                    object_id=c.object_id,
                    revision_id=c.revision_id,
                    block_id=c.block_id,
                    chunk_id=c.chunk_id,
                    claim=c.claim,
                )
                for c in valid_citations
            ),
            provider=profile.provider,
            model=profile.model,
            prompt_package=package.ref.key,
            actor_profile=package.actor_profile.value,
            eu_ai_act_tier=actor.eu_ai_act_tier,
            trace_id=trace_id,
            executed_tools=tuple(r.tool_id for r in executed),
            rejected_tools=tuple(rejected_tools),
            rejected_citations=rejected_citations,
            disclosure_findings=guard.findings,
            input_tokens=generation.usage.input_tokens,
            output_tokens=generation.usage.output_tokens,
            cost_units=generation.usage.cost_units,
        )


# ---------------------------------------------------------------------------
# Memory capabilities (purpose-limited + deletable, FR-AI-006)
# ---------------------------------------------------------------------------


class RememberMemory:
    """``ai.memory.remember`` — store a purpose-limited, deletable memory record (FR-AI-006)."""

    def __init__(self, *, repository: MemoryRepositoryPort, id_factory: IdFactory) -> None:
        self._repo = repository
        self._id_factory = id_factory

    def handle(self, request: object) -> RememberMemoryResult:
        cmd = _typed(request, RememberMemoryCommand)
        organization_id = _tenant(request)
        owner_id = _subject(request)
        record = MemoryRecord(
            memory_id=self._id_factory(),
            organization_id=organization_id,
            owner_id=owner_id,
            memory_class=MemoryClass(cmd.memory_class),
            purpose=cmd.purpose,
            classification=cmd.classification,
            content=cmd.content,
            retention=cmd.retention,
            inferred=cmd.inferred,
        )
        self._repo.add(record)
        return RememberMemoryResult(memory_id=record.memory_id)


class ForgetMemory:
    """``ai.memory.forget`` — delete a memory record the owner controls (FR-AI-006)."""

    def __init__(self, *, repository: MemoryRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ForgetMemoryResult:
        cmd = _typed(request, ForgetMemoryCommand)
        organization_id = _tenant(request)
        owner_id = _subject(request)
        deleted = self._repo.delete(
            organization_id=organization_id, owner_id=owner_id, memory_id=cmd.memory_id
        )
        if not deleted:
            raise MemoryNotFound(cmd.memory_id)
        return ForgetMemoryResult(memory_id=cmd.memory_id, deleted=True)


class ListMemory:
    """``ai.memory.list`` — list the owner's memory records (user-visible control, FR-AI-006)."""

    def __init__(self, *, repository: MemoryRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> MemoryListView:
        _typed(request, ListMemoryParameters)
        organization_id = _tenant(request)
        owner_id = _subject(request)
        records = self._repo.list_for_owner(organization_id=organization_id, owner_id=owner_id)
        return MemoryListView(
            records=tuple(
                MemoryView(
                    memory_id=r.memory_id,
                    memory_class=r.memory_class.value,
                    purpose=r.purpose,
                    classification=r.classification,
                    content=r.content,
                    inferred=r.inferred,
                )
                for r in records
            )
        )


class CorrectMemory:
    """``ai.memory.correct`` — amend a stored memory via an AUDITED supersede (FR-AI-006).

    A correction never mutates the prior record in place: it inserts a NEW head record carrying the
    corrected content that ``supersedes`` the prior one, marks the prior record superseded (so the
    revision history is preserved for the portable export) and writes a tamper-evident audit entry
    binding the prior id to the new id (LAW-14). Only the authenticated owner can correct their own
    memory (the subject is taken from the context, never the payload — rule 50).
    """

    def __init__(
        self,
        *,
        repository: MemoryRepositoryPort,
        audit: AuditRecorderPort,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._audit = audit
        self._id_factory = id_factory

    def handle(self, request: object) -> CorrectMemoryResult:
        cmd = _typed(request, CorrectMemoryCommand)
        organization_id = _tenant(request)
        owner_id = _subject(request)
        if not cmd.content.strip():
            raise MemoryCorrectionInvalid(cmd.memory_id)
        previous = self._repo.get(
            organization_id=organization_id, owner_id=owner_id, memory_id=cmd.memory_id
        )
        if previous is None or not previous.active:
            raise MemoryNotFound(cmd.memory_id)
        correction = MemoryRecord(
            memory_id=self._id_factory(),
            organization_id=organization_id,
            owner_id=owner_id,
            memory_class=previous.memory_class,
            purpose=cmd.purpose if cmd.purpose is not None else previous.purpose,
            classification=previous.classification,
            content=cmd.content,
            retention=previous.retention,
            inferred=False,
            supersedes=previous.memory_id,
        )
        self._repo.supersede(previous=previous, correction=correction)
        self._audit.record(
            event_type=_EVENT_MEMORY,
            actor=_actor(request),
            action=CAP_CORRECT_MEMORY,
            outcome=AuditOutcome.SUCCESS,
            correlation_id=_correlation(request),
            resource=ResourceRef(type=RES_AI_MEMORY, id=correction.memory_id),
            reason_codes=("ai.memory.corrected", f"supersedes:{previous.memory_id}"),
        )
        return CorrectMemoryResult(
            memory_id=correction.memory_id, superseded_memory_id=previous.memory_id
        )


class ExportMemory:
    """``ai.memory.export`` — a portable bundle of the owner's AI memory history (FR-AI-006).

    Returns EVERY record the owner controls (active heads + superseded revisions) so the export is a
    complete, intelligible, portable record; the subject is the authenticated caller, never the
    payload (rule 50).
    """

    def __init__(self, *, repository: MemoryRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> MemoryExportView:
        _typed(request, ExportMemoryParameters)
        organization_id = _tenant(request)
        owner_id = _subject(request)
        records = self._repo.export_for_owner(organization_id=organization_id, owner_id=owner_id)
        return MemoryExportView(
            subject_id=owner_id,
            generated_at=self._clock().isoformat(),
            record_count=len(records),
            records=tuple(
                MemoryExportRecordView(
                    memory_id=r.memory_id,
                    memory_class=r.memory_class.value,
                    purpose=r.purpose,
                    classification=r.classification,
                    content=r.content,
                    inferred=r.inferred,
                    active=r.active,
                    supersedes=r.supersedes,
                    superseded_by=r.superseded_by,
                )
                for r in records
            ),
        )


class ResetMemory:
    """``ai.memory.reset`` — erase ALL of the owner's AI memory (audited, FR-AI-006).

    A reset removes every record (active + superseded) for the authenticated owner so no residue
    remains, and writes a tamper-evident audit entry recording how many records were erased. The
    same erasure path backs a privacy DSAR erase via the AI-memory rights handler.
    """

    def __init__(self, *, repository: MemoryRepositoryPort, audit: AuditRecorderPort) -> None:
        self._repo = repository
        self._audit = audit

    def handle(self, request: object) -> ResetMemoryResult:
        _typed(request, ResetMemoryCommand)
        organization_id = _tenant(request)
        owner_id = _subject(request)
        erased = self._repo.erase_for_owner(organization_id=organization_id, owner_id=owner_id)
        self._audit.record(
            event_type=_EVENT_MEMORY,
            actor=_actor(request),
            action=CAP_RESET_MEMORY,
            outcome=AuditOutcome.SUCCESS,
            correlation_id=_correlation(request),
            resource=ResourceRef(type=RES_AI_MEMORY, id=owner_id),
            reason_codes=("ai.memory.reset", f"erased:{erased}"),
        )
        return ResetMemoryResult(erased_count=erased)
