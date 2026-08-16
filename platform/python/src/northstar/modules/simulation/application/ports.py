"""Ports (abstractions) for the simulation application layer (rule 10/20, DIP).

Every infrastructure/cross-module seam is a Protocol so the capabilities stay infrastructure-free
and the reference executor + AI coach hold no ambient authority (rule 50/60):

* :class:`SimulationRepositoryPort` — the module's own tenant-scoped persistence (LAW-13): the
  published definition, the hidden scoring key, trust tiers and scores.
* :class:`LeaseIssuerPort` / :class:`LeaseValidatorPort` — the control plane signs a short-lived
  lease; the executor side validates signature + expiry WITHOUT broad app credentials (FR-SIM-004).
* :class:`RuntimeExecutorPort` — the sandbox tier seam; the reference in-process adapter enforces
  egress allowlist + quotas + no-credential + scoring-key withholding (EVAL-SEC-010).
* :class:`EvidenceStorePort` — persists the immutable hash-chained run evidence (FR-SIM-005).
* :class:`ScoringPort` — deterministic scoring (FR-SIM-006).
* :class:`AiCoachPort` — the seam onto the AI module's ``ai.answer`` (the single authoritative AI
  path); the coach is a SCOPED actor that never receives the hidden scoring key (FR-SIM-007).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain.model import (
    Lease,
    RunEvidence,
    RuntimePolicy,
    Score,
    SimulationDefinition,
    TrustTier,
)


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """The provider-neutral result of running a simulation in a sandbox tier (EVAL-SEC-010).

    ``status`` is one of ``completed`` / ``terminated_quota`` / ``denied_egress`` /
    ``escape_blocked``. ``actions`` is the ordered transcript the control plane hash-chains into
    evidence; the executor NEVER returns a secret or the scoring key. ``steps_used``/``cpu_used``/
    ``output_bytes`` capture resource use for the quota evidence.
    """

    status: str
    runtime_version: str
    actions: tuple[Mapping[str, object], ...]
    output: str
    steps_used: int
    cpu_used: int
    output_bytes: int
    termination_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SignedLease:
    """A lease plus its opaque signed token (the token is all the executor receives)."""

    lease: Lease
    token: str


@runtime_checkable
class SimulationRepositoryPort(Protocol):
    """Persists the simulation aggregate; every method is tenant-scoped (rule 50, LAW-13)."""

    def add_definition(self, definition: SimulationDefinition, *, scoring_key: str) -> None: ...

    def get_definition(
        self, *, organization_id: str, simulation_id: str, version: str
    ) -> SimulationDefinition | None: ...

    def publish_definition(self, definition: SimulationDefinition) -> None: ...

    def get_scoring_key(
        self, *, organization_id: str, simulation_id: str, version: str
    ) -> str | None: ...

    def set_trust_tier(self, tier: TrustTier) -> None: ...

    def get_trust_tier(self, *, organization_id: str, tier: str) -> TrustTier | None: ...

    def add_score(self, score: Score) -> None: ...

    def get_score(self, *, organization_id: str, run_id: str) -> Score | None: ...


@runtime_checkable
class LeaseIssuerPort(Protocol):
    """Signs a short-lived scoped lease (control plane); returns the lease + opaque token."""

    def issue(self, lease: Lease) -> SignedLease: ...


@runtime_checkable
class LeaseValidatorPort(Protocol):
    """Validates a lease token's signature + integrity WITHOUT app credentials (FR-SIM-004).

    Returns the reconstructed :class:`Lease`; raises :class:`~..domain.errors.LeaseInvalid` for a
    forged/tampered token. Expiry + over-broad scope are checked by the run capability against the
    authoritative clock and policy.
    """

    def validate(self, token: str) -> Lease: ...


@runtime_checkable
class RuntimeExecutorPort(Protocol):
    """Runs a simulation inside a sandbox tier under a validated lease (EVAL-SEC-010).

    The executor receives ONLY the definition, the effective policy, the (validated) lease and the
    run inputs — never an application credential or the scoring key. It enforces the egress
    allowlist and quotas and fails closed on any escape attempt.
    """

    def execute(
        self,
        *,
        definition: SimulationDefinition,
        policy: RuntimePolicy,
        lease: Lease,
        inputs: Mapping[str, object],
    ) -> ExecutionOutcome: ...


@runtime_checkable
class EvidenceStorePort(Protocol):
    """Persists immutable hash-chained run evidence and reads it back (FR-SIM-005)."""

    def record(self, evidence: RunEvidence) -> None: ...

    def get(self, *, organization_id: str, run_id: str) -> RunEvidence | None: ...


@runtime_checkable
class ScoringPort(Protocol):
    """Deterministic scoring of a completed run (FR-SIM-006)."""

    def score(
        self,
        *,
        score_id: str,
        run_id: str,
        organization_id: str,
        definition: SimulationDefinition,
        inputs: Mapping[str, object],
        seed: str,
    ) -> Score: ...


@dataclass(frozen=True, slots=True)
class CoachResult:
    """The result of a scoped AI coaching turn (FR-SIM-007).

    ``hint`` is the coaching text; ``refused`` is ``True`` when a governance defense downgraded it.
    ``disclosed_scoring_key`` is always ``False`` by construction — the coach never receives the
    key — and is surfaced so the AI-isolation eval can assert ``scoring_key_disclosure_rate == 0``.
    """

    hint: str
    refused: bool
    channels: tuple[str, ...] = field(default_factory=tuple)
    trace_id: str = ""
    disclosed_scoring_key: bool = False


@runtime_checkable
class AiCoachPort(Protocol):
    """Seam onto the AI module's ``ai.answer`` for scoped coaching (FR-SIM-007, LAW-09).

    The coach is given ONLY the permitted runtime channel (the question + allowed observations); it
    is NEVER handed the hidden scoring key or a privileged channel.
    """

    def coach(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        question: str,
        runtime_channel: Sequence[str],
        package_id: str,
        version: str,
    ) -> CoachResult: ...


@runtime_checkable
class TrustTierPolicyPort(Protocol):
    """Reads the approved runtime trust tiers the Governance Studio manages (FR-SIM-008)."""

    def get_trust_tier(self, *, organization_id: str, tier: str) -> TrustTier | None: ...
