"""Simulation capabilities: one authoritative implementation per action (LAW-04).

Every handler runs through the kernel command/query bus, so each mutation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope + acting subject come from the
authenticated :class:`RequestContext`, NEVER from the payload (rule 50). Handlers depend only on
:mod:`.ports` and the pure :mod:`..domain`.

The security invariants are enforced here by construction and are never weakened:

* ``simulation.definition.publish`` freezes an IMMUTABLE, versioned definition (FR-SIM-001).
* ``simulation.lease.issue`` mints a SHORT-LIVED SIGNED lease scoped to the exact published
  definition, a permitted+approved trust tier (no isolation downgrade), the policy egress allowlist
  and quota (FR-SIM-004/008).
* ``simulation.run.execute`` validates the lease (signature -> tenant scope -> expiry -> exact
  definition -> not over-broad) BEFORE running, executes in the sandbox tier, records immutable
  hash-chained evidence and scores deterministically (FR-SIM-003/004/005/006).
* ``simulation.run.coach`` runs AI coaching as a SCOPED actor given ONLY the permitted runtime
  channel; the hidden scoring key is loaded only on the scoring path and is NEVER passed to the AI
  (FR-SIM-007).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ..domain.errors import (
    ImmutableDefinitionError,
    LeaseInvalid,
    SimulationInvariantViolation,
    SimulationNotFound,
    TenantScopeMissing,
    TrustTierNotApproved,
)
from ..domain.model import (
    DefinitionStatus,
    Lease,
    NetworkMode,
    ResourceQuota,
    RunEvidence,
    RuntimePolicy,
    RuntimeTier,
    SimulationDefinition,
    TrustTier,
    definition_from_document,
    ensure_no_downgrade,
)
from .ports import (
    AiCoachPort,
    EvidenceStorePort,
    LeaseIssuerPort,
    LeaseValidatorPort,
    RuntimeExecutorPort,
    ScoringPort,
    SimulationRepositoryPort,
)

CAP_VERSION = "1.0.0"

CAP_DEFINE = "simulation.definition.define"
CAP_PUBLISH = "simulation.definition.publish"
CAP_ISSUE_LEASE = "simulation.lease.issue"
CAP_RUN = "simulation.run.execute"
CAP_COACH = "simulation.run.coach"
CAP_SET_TIER = "simulation.trust-tier.set"

SIMULATION_CAPABILITIES: tuple[str, ...] = (
    CAP_DEFINE,
    CAP_PUBLISH,
    CAP_ISSUE_LEASE,
    CAP_RUN,
    CAP_COACH,
    CAP_SET_TIER,
)

RES_SIMULATION = "simulation.simulation"

DEFAULT_LEASE_TTL_SECONDS = 300

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DefineSimulationCommand:
    definition: Mapping[str, object]
    scoring_key: str = ""


@dataclass(frozen=True, slots=True)
class DefineSimulationResult:
    simulation_id: str
    version: str
    content_hash: str
    status: str


@dataclass(frozen=True, slots=True)
class PublishSimulationCommand:
    simulation_id: str
    version: str


@dataclass(frozen=True, slots=True)
class PublishSimulationResult:
    simulation_id: str
    version: str
    content_hash: str
    status: str


@dataclass(frozen=True, slots=True)
class SetTrustTierCommand:
    tier: str
    approved: bool
    max_quota: Mapping[str, int] | None = None


@dataclass(frozen=True, slots=True)
class SetTrustTierResult:
    tier: str
    approved: bool


@dataclass(frozen=True, slots=True)
class IssueLeaseCommand:
    simulation_id: str
    version: str
    tier: str
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS


@dataclass(frozen=True, slots=True)
class IssueLeaseResult:
    lease_id: str
    token: str
    tier: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class RunSimulationCommand:
    token: str
    inputs: Mapping[str, object]
    seed: str = "0"


@dataclass(frozen=True, slots=True)
class RunSimulationResult:
    run_id: str
    status: str
    termination_reason: str | None
    steps_used: int
    evidence_head_hash: str
    evidence_verified: bool
    score_value: float


@dataclass(frozen=True, slots=True)
class CoachCommand:
    simulation_id: str
    version: str
    question: str
    package_id: str
    package_version: str = "1.0.0"


@dataclass(frozen=True, slots=True)
class CoachResultView:
    hint: str
    refused: bool
    disclosed_scoring_key: bool
    trace_id: str


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
        raise TenantScopeMissing()
    return scope


def _subject(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    actor = getattr(context, "actor", None)
    subject = getattr(actor, "id", None)
    if not subject:
        raise TenantScopeMissing()
    return subject


def _correlation(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    return str(getattr(context, "correlation_id", "-"))


def _inputs_hash(inputs: Mapping[str, object]) -> str:
    import hashlib

    canonical = json.dumps(dict(inputs), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_runtime_policy(definition: SimulationDefinition, tier: RuntimeTier) -> RuntimePolicy:
    """Derive the deny-by-default runtime policy from a published definition + tier (FR-SIM-003).

    Egress is an explicit allowlist taken from the definition's declared destinations (empty when
    the definition declares ``network: none``). The quota mirrors the declared resources; one step
    maps to one wall-clock second in the reference tier.
    """
    if definition.capabilities.network == "none":
        mode = NetworkMode.NONE
        allowlist: tuple[str, ...] = ()
    else:
        mode = NetworkMode.ALLOWLIST
        allowlist = definition.capabilities.egress_destinations
    quota = ResourceQuota(
        cpu_millis=definition.resources.cpu_millis,
        memory_mb=definition.resources.memory_mb,
        wall_time_seconds=definition.resources.wall_time_seconds,
        max_steps=definition.resources.wall_time_seconds,
        max_output_bytes=definition.resources.max_output_bytes,
        max_processes=definition.resources.max_processes,
    )
    policy_id = "pol-" + definition.content_hash().split(":", 1)[1][:16]
    return RuntimePolicy(
        policy_id=policy_id,
        version=definition.version,
        tier=tier,
        network_mode=mode,
        egress_allowlist=allowlist,
        quota=quota,
    )


def _load_published(
    repo: SimulationRepositoryPort, *, organization_id: str, simulation_id: str, version: str
) -> SimulationDefinition:
    definition = repo.get_definition(
        organization_id=organization_id, simulation_id=simulation_id, version=version
    )
    if definition is None:
        raise SimulationNotFound("definition", f"{simulation_id}@{version}")
    if definition.status is not DefinitionStatus.PUBLISHED:
        raise SimulationInvariantViolation(
            f"simulation '{simulation_id}@{version}' is not published",
            code="simulation.definition.unpublished",
        )
    return definition


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class DefineSimulation:
    """``simulation.definition.define`` — create a draft, schema-shaped definition (FR-SIM-001)."""

    def __init__(self, *, repository: SimulationRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> DefineSimulationResult:
        command = _typed(request, DefineSimulationCommand)
        organization_id = _tenant(request)
        definition = definition_from_document(command.definition, organization_id=organization_id)
        existing = self._repo.get_definition(
            organization_id=organization_id,
            simulation_id=definition.simulation_id,
            version=definition.version,
        )
        if existing is not None and existing.status is DefinitionStatus.PUBLISHED:
            raise ImmutableDefinitionError(definition.simulation_id, definition.version)
        self._repo.add_definition(definition, scoring_key=command.scoring_key)
        return DefineSimulationResult(
            simulation_id=definition.simulation_id,
            version=definition.version,
            content_hash=definition.content_hash(),
            status=definition.status.value,
        )


class PublishSimulation:
    """``simulation.definition.publish`` — freeze an IMMUTABLE versioned definition (FR-SIM-001)."""

    def __init__(self, *, repository: SimulationRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> PublishSimulationResult:
        command = _typed(request, PublishSimulationCommand)
        organization_id = _tenant(request)
        definition = self._repo.get_definition(
            organization_id=organization_id,
            simulation_id=command.simulation_id,
            version=command.version,
        )
        if definition is None:
            raise SimulationNotFound("definition", f"{command.simulation_id}@{command.version}")
        if definition.status is DefinitionStatus.PUBLISHED:
            raise ImmutableDefinitionError(command.simulation_id, command.version)
        published = replace(definition, status=DefinitionStatus.PUBLISHED)
        self._repo.publish_definition(published)
        return PublishSimulationResult(
            simulation_id=published.simulation_id,
            version=published.version,
            content_hash=published.content_hash(),
            status=published.status.value,
        )


class SetTrustTier:
    """``simulation.trust-tier.set`` — Studio manages runtime trust tiers (FR-SIM-008)."""

    def __init__(self, *, repository: SimulationRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> SetTrustTierResult:
        command = _typed(request, SetTrustTierCommand)
        organization_id = _tenant(request)
        tier = RuntimeTier(command.tier)
        quota_data = command.max_quota or {
            "cpu_millis": 60000,
            "memory_mb": 512,
            "wall_time_seconds": 300,
            "max_steps": 300,
            "max_output_bytes": 1048576,
            "max_processes": 4,
        }
        trust_tier = TrustTier(
            organization_id=organization_id,
            tier=tier,
            approved=command.approved,
            max_quota=ResourceQuota.from_dict(quota_data),
        )
        self._repo.set_trust_tier(trust_tier)
        return SetTrustTierResult(tier=tier.value, approved=command.approved)


class IssueLease:
    """``simulation.lease.issue`` — mint a short-lived SIGNED, scoped lease (FR-SIM-004/008)."""

    def __init__(
        self,
        *,
        repository: SimulationRepositoryPort,
        issuer: LeaseIssuerPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._issuer = issuer
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> IssueLeaseResult:
        command = _typed(request, IssueLeaseCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        definition = _load_published(
            self._repo,
            organization_id=organization_id,
            simulation_id=command.simulation_id,
            version=command.version,
        )
        tier = RuntimeTier(command.tier)
        # A definition may only be run on a tier that does NOT downgrade its required isolation.
        ensure_no_downgrade(definition.runtime_profile, tier)
        trust_tier = self._repo.get_trust_tier(organization_id=organization_id, tier=tier.value)
        if trust_tier is None or not trust_tier.approved:
            raise TrustTierNotApproved(tier.value)
        policy = build_runtime_policy(definition, tier)
        if not trust_tier.max_quota.covers(policy.quota):
            raise SimulationInvariantViolation(
                "requested policy quota exceeds the approved trust-tier ceiling",
                code="simulation.tier.quota",
            )
        ttl = max(1, int(command.ttl_seconds))
        now = self._clock()
        lease = Lease(
            lease_id=self._id_factory(),
            simulation_id=definition.simulation_id,
            version=definition.version,
            definition_hash=definition.content_hash(),
            organization_id=organization_id,
            subject_id=subject_id,
            tier=tier,
            egress_allowlist=policy.egress_allowlist,
            quota=policy.quota,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl),
            nonce=self._id_factory(),
        )
        signed = self._issuer.issue(lease)
        return IssueLeaseResult(
            lease_id=lease.lease_id,
            token=signed.token,
            tier=tier.value,
            expires_at=lease.expires_at.isoformat(),
        )


class RunSimulation:
    """``simulation.run.execute`` — validate lease, run in sandbox, record evidence, score.

    The lease is validated in order (signature -> tenant scope -> expiry -> exact definition -> not
    over-broad) BEFORE anything runs; any failure raises :class:`LeaseInvalid` and executes nothing
    (FR-SIM-004). The sandbox enforces egress + quotas + escape defenses and returns a status; the
    control plane records an immutable hash-chained evidence log for EVERY run — completed,
    quota-terminated, egress-denied or escape-blocked (FR-SIM-005) — then scores deterministically
    (FR-SIM-006).
    """

    def __init__(
        self,
        *,
        repository: SimulationRepositoryPort,
        validator: LeaseValidatorPort,
        executor: RuntimeExecutorPort,
        evidence_store: EvidenceStorePort,
        scoring: ScoringPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._validator = validator
        self._executor = executor
        self._evidence = evidence_store
        self._scoring = scoring
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> RunSimulationResult:
        command = _typed(request, RunSimulationCommand)
        organization_id = _tenant(request)

        # 1. Signature/integrity: a forged or tampered token is rejected (nothing runs).
        lease = self._validator.validate(command.token)

        # 2. Tenant scope: the lease must belong to the authenticated tenant (rule 50).
        if lease.organization_id != organization_id:
            raise LeaseInvalid("scope_mismatch", "lease tenant does not match the request tenant")

        # 3. Expiry against the authoritative clock.
        now = self._clock()
        if lease.is_expired(now):
            raise LeaseInvalid("expired", "lease has expired")

        # 4. The lease must bind to the EXACT published definition.
        definition = _load_published(
            self._repo,
            organization_id=organization_id,
            simulation_id=lease.simulation_id,
            version=lease.version,
        )
        if definition.content_hash() != lease.definition_hash:
            raise LeaseInvalid("scope_mismatch", "lease does not match the published definition")

        # 5. Over-broad scope: the lease may not grant more than the policy allows.
        policy = build_runtime_policy(definition, lease.tier)
        if lease.exceeds(policy):
            raise LeaseInvalid("over_broad", "lease grants more than the runtime policy permits")

        # Execute in the sandbox tier (no app credentials, no scoring key ever handed over).
        outcome = self._executor.execute(
            definition=definition, policy=policy, lease=lease, inputs=command.inputs
        )

        run_id = self._id_factory()
        evidence = RunEvidence.start(
            run_id=run_id,
            organization_id=organization_id,
            simulation_id=definition.simulation_id,
            definition_hash=definition.content_hash(),
            runtime_version=outcome.runtime_version,
            inputs_hash=_inputs_hash(command.inputs),
        )
        evidence = evidence.append(
            "run.started",
            {"tier": lease.tier.value, "lease_id": lease.lease_id, "seed": command.seed},
        )
        for action in outcome.actions:
            evidence = evidence.append("run.action", dict(action))
        evidence = evidence.append(
            "run.outcome",
            {
                "status": outcome.status,
                "termination_reason": outcome.termination_reason,
                "steps_used": outcome.steps_used,
                "cpu_used": outcome.cpu_used,
                "output_bytes": outcome.output_bytes,
            },
        )
        evidence = evidence.finalize(outcome.status)
        self._evidence.record(evidence)

        score = self._scoring.score(
            score_id=self._id_factory(),
            run_id=run_id,
            organization_id=organization_id,
            definition=definition,
            inputs=command.inputs,
            seed=command.seed,
        )
        self._repo.add_score(score)

        return RunSimulationResult(
            run_id=run_id,
            status=outcome.status,
            termination_reason=outcome.termination_reason,
            steps_used=outcome.steps_used,
            evidence_head_hash=evidence.head_hash,
            evidence_verified=evidence.verify(),
            score_value=score.value,
        )


class CoachSimulation:
    """``simulation.run.coach`` — scoped AI coaching that cannot see the scoring key (FR-SIM-007).

    Reuses the AI module's ``ai.answer`` via :class:`AiCoachPort`. The coach is given ONLY the
    permitted runtime channel (the definition's objectives); the hidden scoring key is loaded on the
    scoring path alone and is NEVER passed to the AI. An adversarial attempt to read the key returns
    nothing, so ``scoring_key_disclosure_rate == 0`` (EVAL-SIM-007, closes GATE-AI-GA's blocker).
    """

    def __init__(self, *, repository: SimulationRepositoryPort, coach: AiCoachPort) -> None:
        self._repo = repository
        self._coach = coach

    def handle(self, request: object) -> CoachResultView:
        command = _typed(request, CoachCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        definition = _load_published(
            self._repo,
            organization_id=organization_id,
            simulation_id=command.simulation_id,
            version=command.version,
        )
        if definition.ai_assistance in (None, "none"):
            raise SimulationInvariantViolation(
                "AI assistance is disabled for this simulation",
                code="simulation.coach.disabled",
            )
        # The permitted runtime channel — objectives only. The scoring key is NEVER placed here.
        runtime_channel = definition.objectives
        result = self._coach.coach(
            organization_id=organization_id,
            subject_id=subject_id,
            correlation_id=_correlation(request),
            question=command.question,
            runtime_channel=runtime_channel,
            package_id=command.package_id,
            version=command.package_version,
        )
        return CoachResultView(
            hint=result.hint,
            refused=result.refused,
            disclosed_scoring_key=result.disclosed_scoring_key,
            trace_id=result.trace_id,
        )
