"""Simulation object model (docs/15, FR-SIM-001..008). Pure and infrastructure-free (rule 10).

Everything here is a frozen value object with its invariants enforced in ``__post_init__``; no
database, network or provider SDK is reachable. The security-critical pieces live here so they are
provable in isolation:

* :class:`SimulationDefinition` is a versioned executable artifact validated against
  ``simulation-definition.schema.json`` and IMMUTABLE once published (FR-SIM-001). ``content_hash``
  gives it a stable version identity that binds leases and evidence to the exact definition.
* :class:`RuntimePolicy` (``runtime-policy.schema.json``) is DENY-BY-DEFAULT: :meth:`permits_egress`
  returns ``True`` ONLY for an explicit allowlist match, so any other destination is refused
  (FR-SIM-003, SSRF). :class:`ResourceQuota` carries the CPU/memory/time/step/output limits.
* :class:`Lease` is a short-lived, scoped grant; :func:`lease_signing_payload` is the canonical
  bytes an adapter signs/verifies, and :meth:`Lease.is_expired` / :meth:`Lease.exceeds` express the
  expiry + over-broad checks (FR-SIM-004).
* :class:`RunEvidence` is a hash-chained, immutable evidence log (FR-SIM-005): :meth:`verify`
  recomputes the chain from the genesis seed so any mutation is detectable.
* :func:`compute_score` is deterministic: identical ``(definition, inputs, seed)`` yield an
  identical :class:`Score` (FR-SIM-006). The hidden scoring key is NEVER an input here.
* :class:`RuntimeTier` + :class:`TrustTier` model the runtime trust tiers the Studio governs and the
  rule that a definition may not run on a tier that DOWNGRADES its isolation (FR-SIM-002/008).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .errors import (
    IsolationDowngrade,
    SimulationInvariantViolation,
)


def _require(condition: bool, message: str, code: str = "simulation.invariant") -> None:
    if not condition:
        raise SimulationInvariantViolation(message, code=code)


def _canonical(value: object) -> str:
    """Deterministic canonical JSON (sorted keys, no whitespace) for hashing/signing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Runtime tiers (FR-SIM-002) + isolation ordering (docs/15 §3)
# ---------------------------------------------------------------------------


class RuntimeTier(StrEnum):
    """Runtime policy tiers (``runtime-policy.schema.json`` ``tier``)."""

    BROWSER = "browser"
    WASI = "wasi"
    CONTAINER = "container"
    HARDENED_CONTAINER = "hardened_container"
    MICROVM = "microvm"
    EXTERNAL = "external"


class RuntimeProfile(StrEnum):
    """Definition runtime profiles (``simulation-definition.schema.json`` ``runtime_profile``)."""

    BROWSER = "browser"
    WASI = "wasi"
    OCI_STANDARD = "oci_standard"
    OCI_HARDENED = "oci_hardened"
    MICROVM = "microvm"
    EXTERNAL = "external"


# Isolation strength ordering: a higher rank is a stronger tenant/host boundary (docs/15 §3, R0-R5).
_TIER_RANK: dict[RuntimeTier, int] = {
    RuntimeTier.BROWSER: 0,
    RuntimeTier.WASI: 1,
    RuntimeTier.CONTAINER: 2,
    RuntimeTier.HARDENED_CONTAINER: 3,
    RuntimeTier.MICROVM: 4,
    RuntimeTier.EXTERNAL: 5,
}

# Every definition profile requires at least the isolation of its mapped tier; a definition can
# never be run on a tier weaker than this (the control plane may only UPGRADE isolation).
_PROFILE_REQUIRED_TIER: dict[RuntimeProfile, RuntimeTier] = {
    RuntimeProfile.BROWSER: RuntimeTier.BROWSER,
    RuntimeProfile.WASI: RuntimeTier.WASI,
    RuntimeProfile.OCI_STANDARD: RuntimeTier.CONTAINER,
    RuntimeProfile.OCI_HARDENED: RuntimeTier.HARDENED_CONTAINER,
    RuntimeProfile.MICROVM: RuntimeTier.MICROVM,
    RuntimeProfile.EXTERNAL: RuntimeTier.EXTERNAL,
}


def tier_rank(tier: RuntimeTier) -> int:
    return _TIER_RANK[tier]


def required_tier_for(profile: RuntimeProfile) -> RuntimeTier:
    return _PROFILE_REQUIRED_TIER[profile]


def ensure_no_downgrade(profile: RuntimeProfile, tier: RuntimeTier) -> None:
    """Raise :class:`IsolationDowngrade` when ``tier`` is weaker than ``profile`` requires."""
    required = required_tier_for(profile)
    if tier_rank(tier) < tier_rank(required):
        raise IsolationDowngrade(required.value, tier.value)


@dataclass(frozen=True, slots=True)
class TrustTier:
    """A runtime trust tier the Governance Studio manages for a tenant (FR-SIM-008).

    ``approved`` gates whether leases may target the tier; ``max_quota`` caps what any lease on the
    tier may grant (the control plane never issues more than the tier allows).
    """

    organization_id: str
    tier: RuntimeTier
    approved: bool
    max_quota: ResourceQuota

    def __post_init__(self) -> None:
        _require(
            bool(self.organization_id), "organization_id required", code="simulation.tier.scope"
        )


# ---------------------------------------------------------------------------
# Resource quota (FR-SIM-003) + runtime policy (runtime-policy.schema.json)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResourceQuota:
    """Hard CPU/memory/time/step/output limits the executor enforces (FR-SIM-003, EVAL-SIM-001)."""

    cpu_millis: int
    memory_mb: int
    wall_time_seconds: int
    max_steps: int
    max_output_bytes: int
    max_processes: int = 1

    def __post_init__(self) -> None:
        for name, value in (
            ("cpu_millis", self.cpu_millis),
            ("memory_mb", self.memory_mb),
            ("wall_time_seconds", self.wall_time_seconds),
            ("max_steps", self.max_steps),
            ("max_output_bytes", self.max_output_bytes),
            ("max_processes", self.max_processes),
        ):
            _require(value >= 1, f"quota {name} must be >= 1", code="simulation.quota.value")

    def covers(self, other: ResourceQuota) -> bool:
        """True when every limit in ``self`` is >= the corresponding limit in ``other``.

        Used to reject an OVER-BROAD lease/tier: a lease may never grant more than the policy (and a
        policy never more than the tier's ``max_quota``).
        """
        return (
            self.cpu_millis >= other.cpu_millis
            and self.memory_mb >= other.memory_mb
            and self.wall_time_seconds >= other.wall_time_seconds
            and self.max_steps >= other.max_steps
            and self.max_output_bytes >= other.max_output_bytes
            and self.max_processes >= other.max_processes
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "cpu_millis": self.cpu_millis,
            "memory_mb": self.memory_mb,
            "wall_time_seconds": self.wall_time_seconds,
            "max_steps": self.max_steps,
            "max_output_bytes": self.max_output_bytes,
            "max_processes": self.max_processes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, int]) -> ResourceQuota:
        return cls(
            cpu_millis=int(data["cpu_millis"]),
            memory_mb=int(data["memory_mb"]),
            wall_time_seconds=int(data["wall_time_seconds"]),
            max_steps=int(data["max_steps"]),
            max_output_bytes=int(data["max_output_bytes"]),
            max_processes=int(data.get("max_processes", 1)),
        )


class NetworkMode(StrEnum):
    """Runtime network mode (``runtime-policy.schema.json`` ``network.mode``)."""

    NONE = "none"
    ALLOWLIST = "allowlist"
    PROVIDER_MANAGED = "provider_managed"


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    """A deny-by-default runtime policy (``runtime-policy.schema.json``, FR-SIM-003).

    Egress is refused unless the destination is an EXACT match on ``egress_allowlist`` and the mode
    permits any egress at all. ``quota`` carries the enforced resource limits.
    """

    policy_id: str
    version: str
    tier: RuntimeTier
    network_mode: NetworkMode
    egress_allowlist: tuple[str, ...]
    quota: ResourceQuota
    root_read_only: bool = True
    workspace_ephemeral: bool = True

    def __post_init__(self) -> None:
        _require(bool(self.policy_id), "policy_id required", code="simulation.policy.id")
        if self.network_mode is NetworkMode.NONE:
            _require(
                len(self.egress_allowlist) == 0,
                "network mode 'none' cannot declare an egress allowlist",
                code="simulation.policy.network",
            )

    def permits_egress(self, destination: str) -> bool:
        """Deny-by-default egress: ``True`` only for an exact allowlist match (FR-SIM-003)."""
        if self.network_mode is NetworkMode.NONE:
            return False
        return destination in self.egress_allowlist


# ---------------------------------------------------------------------------
# Simulation definition (FR-SIM-001) — schema-valid, versioned, immutable
# ---------------------------------------------------------------------------


class DefinitionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


@dataclass(frozen=True, slots=True)
class SimulationCapabilities:
    """The definition's declared capability surface (``simulation-definition`` ``capabilities``)."""

    network: str
    egress_destinations: tuple[str, ...] = ()
    secret_broker_scopes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"network": self.network}
        if self.egress_destinations:
            data["egress_destinations"] = list(self.egress_destinations)
        if self.secret_broker_scopes:
            data["secret_broker_scopes"] = list(self.secret_broker_scopes)
        return data


@dataclass(frozen=True, slots=True)
class DefinitionResources:
    """Declared resource requirements (``simulation-definition`` ``resources``)."""

    cpu_millis: int
    memory_mb: int
    disk_mb: int
    wall_time_seconds: int
    max_processes: int
    max_output_bytes: int = 65536

    def to_dict(self) -> dict[str, int]:
        return {
            "cpu_millis": self.cpu_millis,
            "memory_mb": self.memory_mb,
            "disk_mb": self.disk_mb,
            "wall_time_seconds": self.wall_time_seconds,
            "max_processes": self.max_processes,
            "max_output_bytes": self.max_output_bytes,
        }


@dataclass(frozen=True, slots=True)
class EvaluationProfileRef:
    profile_id: str
    version: str

    def to_dict(self) -> dict[str, str]:
        return {"profile_id": self.profile_id, "version": self.version}


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    retention_class: str
    capture: tuple[str, ...]
    learner_notice_required: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "retention_class": self.retention_class,
            "capture": list(self.capture),
            "learner_notice_required": self.learner_notice_required,
        }


@dataclass(frozen=True, slots=True)
class SimulationDefinition:
    """A versioned executable simulation artifact (FR-SIM-001), schema-valid + immutable.

    The dataclass is frozen and, once :attr:`status` is ``published``, :meth:`mutate` is impossible;
    a correction is a new version. :meth:`content_hash` is the stable version identity computed over
    the canonical schema document (excluding lifecycle status), so a lease/evidence can bind to the
    EXACT definition and any change is detectable.
    """

    simulation_id: str
    version: str
    title: str
    objectives: tuple[str, ...]
    runtime_profile: RuntimeProfile
    capabilities: SimulationCapabilities
    resources: DefinitionResources
    evaluation: EvaluationProfileRef
    evidence_policy: EvidencePolicy
    organization_id: str
    schema_version: str = "1.0"
    ai_assistance: str | None = None
    image_or_module_digest: str | None = None
    status: DefinitionStatus = DefinitionStatus.DRAFT

    def __post_init__(self) -> None:
        _require(
            8 <= len(self.simulation_id) <= 128,
            "simulation_id must be 8..128 chars",
            code="simulation.definition.id",
        )
        _require(bool(self.title), "title required", code="simulation.definition.title")
        _require(len(self.objectives) >= 1, "at least one objective required")
        _require(
            bool(self.organization_id),
            "organization_id required",
            code="simulation.definition.scope",
        )

    def to_document(self) -> dict[str, object]:
        """Project to the canonical ``simulation-definition.schema.json`` document (FR-SIM-001)."""
        document: dict[str, object] = {
            "schema_version": self.schema_version,
            "simulation_id": self.simulation_id,
            "version": self.version,
            "title": self.title,
            "objectives": list(self.objectives),
            "runtime_profile": self.runtime_profile.value,
            "capabilities": self.capabilities.to_dict(),
            "resources": self.resources.to_dict(),
            "evaluation": self.evaluation.to_dict(),
            "evidence_policy": self.evidence_policy.to_dict(),
        }
        if self.image_or_module_digest is not None:
            document["image_or_module_digest"] = self.image_or_module_digest
        if self.ai_assistance is not None:
            document["ai_assistance"] = self.ai_assistance
        return document

    def content_hash(self) -> str:
        """Stable version identity over the canonical document (excludes lifecycle status)."""
        return _sha256(_canonical(self.to_document()))

    def mutate(self, *_args: object, **_kwargs: object) -> None:
        from .errors import ImmutableDefinitionError

        raise ImmutableDefinitionError(self.simulation_id, self.version)


def definition_from_document(
    document: Mapping[str, object], *, organization_id: str
) -> SimulationDefinition:
    """Build a :class:`SimulationDefinition` from a schema-shaped document (draft).

    Raises :class:`SimulationInvariantViolation` for a structurally invalid document. Full JSON
    Schema conformance is asserted by the contract test; this keeps the domain infrastructure-free.
    """
    try:
        capabilities_raw = dict(document["capabilities"])  # type: ignore[arg-type]
        resources_raw = dict(document["resources"])  # type: ignore[arg-type]
        evaluation_raw = dict(document["evaluation"])  # type: ignore[arg-type]
        evidence_raw = dict(document["evidence_policy"])  # type: ignore[arg-type]
        return SimulationDefinition(
            simulation_id=str(document["simulation_id"]),
            version=str(document["version"]),
            title=str(document["title"]),
            objectives=tuple(str(o) for o in document["objectives"]),  # type: ignore[union-attr]
            runtime_profile=RuntimeProfile(str(document["runtime_profile"])),
            capabilities=SimulationCapabilities(
                network=str(capabilities_raw["network"]),
                egress_destinations=tuple(
                    str(d) for d in capabilities_raw.get("egress_destinations", ())
                ),
                secret_broker_scopes=tuple(
                    str(s) for s in capabilities_raw.get("secret_broker_scopes", ())
                ),
            ),
            resources=DefinitionResources(
                cpu_millis=int(resources_raw["cpu_millis"]),
                memory_mb=int(resources_raw["memory_mb"]),
                disk_mb=int(resources_raw["disk_mb"]),
                wall_time_seconds=int(resources_raw["wall_time_seconds"]),
                max_processes=int(resources_raw["max_processes"]),
                max_output_bytes=int(resources_raw.get("max_output_bytes", 65536)),
            ),
            evaluation=EvaluationProfileRef(
                profile_id=str(evaluation_raw["profile_id"]),
                version=str(evaluation_raw["version"]),
            ),
            evidence_policy=EvidencePolicy(
                retention_class=str(evidence_raw["retention_class"]),
                capture=tuple(str(c) for c in evidence_raw.get("capture", ())),
                learner_notice_required=bool(evidence_raw.get("learner_notice_required", True)),
            ),
            organization_id=organization_id,
            schema_version=str(document.get("schema_version", "1.0")),
            ai_assistance=(
                str(document["ai_assistance"])
                if document.get("ai_assistance") is not None
                else None
            ),
            image_or_module_digest=(
                str(document["image_or_module_digest"])
                if document.get("image_or_module_digest") is not None
                else None
            ),
        )
    except KeyError as exc:
        raise SimulationInvariantViolation(
            f"simulation definition is missing required field {exc}",
            code="simulation.definition.malformed",
        ) from exc


# ---------------------------------------------------------------------------
# Lease (FR-SIM-004) — short-lived, scoped, signed
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Lease:
    """A short-lived, scoped runtime lease the executor validates (FR-SIM-004).

    The lease binds to the EXACT published definition (``definition_hash``), a tenant + subject, a
    runtime tier, an egress allowlist and a :class:`ResourceQuota`. It carries NO application
    credential — the executor runs on the lease alone. ``nonce`` makes each lease unique.
    """

    lease_id: str
    simulation_id: str
    version: str
    definition_hash: str
    organization_id: str
    subject_id: str
    tier: RuntimeTier
    egress_allowlist: tuple[str, ...]
    quota: ResourceQuota
    issued_at: datetime
    expires_at: datetime
    nonce: str

    def __post_init__(self) -> None:
        _require(bool(self.lease_id), "lease_id required", code="simulation.lease.id")
        _require(
            self.issued_at.tzinfo is not None and self.expires_at.tzinfo is not None,
            "lease timestamps must be timezone-aware (UTC)",
            code="simulation.lease.time",
        )
        _require(
            self.expires_at > self.issued_at,
            "lease must expire after it is issued",
            code="simulation.lease.window",
        )

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def exceeds(self, policy: RuntimePolicy) -> bool:
        """True when the lease grants MORE than the policy allows (over-broad, FR-SIM-004).

        A lease is over-broad if it targets a different tier, allows an egress destination the
        policy does not, or grants a quota the policy does not fully cover.
        """
        if self.tier is not policy.tier:
            return True
        if any(dest not in policy.egress_allowlist for dest in self.egress_allowlist):
            return True
        return not policy.quota.covers(self.quota)

    def signing_material(self) -> dict[str, object]:
        """The canonical claim set an adapter signs/verifies (order-independent)."""
        return {
            "lease_id": self.lease_id,
            "simulation_id": self.simulation_id,
            "version": self.version,
            "definition_hash": self.definition_hash,
            "organization_id": self.organization_id,
            "subject_id": self.subject_id,
            "tier": self.tier.value,
            "egress_allowlist": sorted(self.egress_allowlist),
            "quota": self.quota.to_dict(),
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "nonce": self.nonce,
        }


def lease_signing_payload(lease: Lease) -> bytes:
    """The canonical bytes to HMAC/sign for ``lease`` (deterministic, order-independent)."""
    return _canonical(lease.signing_material()).encode("utf-8")


# ---------------------------------------------------------------------------
# Run evidence (FR-SIM-005) — immutable, hash-chained, tamper-evident
# ---------------------------------------------------------------------------


def evidence_genesis(*, definition_hash: str, runtime_version: str, inputs_hash: str) -> str:
    """The genesis hash the evidence chain extends (binds definition + runtime + inputs)."""
    return _sha256(
        _canonical(
            {
                "definition_hash": definition_hash,
                "runtime_version": runtime_version,
                "inputs_hash": inputs_hash,
            }
        )
    )


def _entry_hash(*, prev_hash: str, seq: int, kind: str, payload: Mapping[str, object]) -> str:
    return _sha256(
        _canonical({"prev": prev_hash, "seq": seq, "kind": kind, "payload": dict(payload)})
    )


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    """One immutable, hash-chained evidence record (an action/outcome in a run, FR-SIM-005)."""

    seq: int
    kind: str
    payload: Mapping[str, object]
    prev_hash: str
    entry_hash: str


@dataclass(frozen=True, slots=True)
class RunEvidence:
    """An immutable, tamper-evident evidence log for a run (FR-SIM-005, EVAL-SIM-003).

    Built by :meth:`start` then :meth:`append`; :meth:`verify` recomputes the whole chain from the
    genesis seed and returns ``False`` if any entry (payload or ordering) was mutated.
    """

    run_id: str
    organization_id: str
    simulation_id: str
    definition_hash: str
    runtime_version: str
    inputs_hash: str
    entries: tuple[EvidenceEntry, ...] = ()
    outcome: str = "pending"

    @classmethod
    def start(
        cls,
        *,
        run_id: str,
        organization_id: str,
        simulation_id: str,
        definition_hash: str,
        runtime_version: str,
        inputs_hash: str,
    ) -> RunEvidence:
        return cls(
            run_id=run_id,
            organization_id=organization_id,
            simulation_id=simulation_id,
            definition_hash=definition_hash,
            runtime_version=runtime_version,
            inputs_hash=inputs_hash,
        )

    @property
    def genesis(self) -> str:
        return evidence_genesis(
            definition_hash=self.definition_hash,
            runtime_version=self.runtime_version,
            inputs_hash=self.inputs_hash,
        )

    @property
    def head_hash(self) -> str:
        return self.entries[-1].entry_hash if self.entries else self.genesis

    def append(self, kind: str, payload: Mapping[str, object]) -> RunEvidence:
        """Return a NEW evidence log with one more hash-chained entry (append-only)."""
        seq = len(self.entries)
        prev = self.head_hash
        entry = EvidenceEntry(
            seq=seq,
            kind=kind,
            payload=dict(payload),
            prev_hash=prev,
            entry_hash=_entry_hash(prev_hash=prev, seq=seq, kind=kind, payload=payload),
        )
        from dataclasses import replace

        return replace(self, entries=(*self.entries, entry))

    def finalize(self, outcome: str) -> RunEvidence:
        from dataclasses import replace

        return replace(self, outcome=outcome)

    def verify(self) -> bool:
        """Recompute the chain from the genesis seed; ``False`` if any entry was tampered."""
        prev = self.genesis
        for expected_seq, entry in enumerate(self.entries):
            if entry.seq != expected_seq or entry.prev_hash != prev:
                return False
            recomputed = _entry_hash(
                prev_hash=entry.prev_hash,
                seq=entry.seq,
                kind=entry.kind,
                payload=entry.payload,
            )
            if recomputed != entry.entry_hash:
                return False
            prev = entry.entry_hash
        return True


# ---------------------------------------------------------------------------
# Deterministic scoring (FR-SIM-006)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Score:
    """A deterministic score for a run (FR-SIM-006, EVAL-SIM-002/006).

    ``value`` is derived purely from ``(definition_hash, inputs, seed)`` via :func:`compute_score`,
    so the same triple always yields the same score. The hidden scoring key is NOT an input, so
    scoring is reproducible AND an AI coach that never sees the key cannot influence or read it.
    """

    score_id: str
    run_id: str
    organization_id: str
    profile_id: str
    profile_version: str
    seed: str
    value: float
    breakdown: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        _require(0.0 <= self.value <= 1.0, "score value must be within [0, 1]")


def compute_score(
    *,
    definition_hash: str,
    inputs: Mapping[str, object],
    seed: str,
    profile_id: str,
    profile_version: str,
) -> float:
    """Deterministically compute a score in [0, 1] from ``(definition, inputs, seed)`` (FR-SIM-006).

    A pure function: the same arguments always return the same value. This is what makes a run
    replayable to an identical score.
    """
    material = _canonical(
        {
            "definition_hash": definition_hash,
            "inputs": dict(inputs),
            "seed": seed,
            "profile_id": profile_id,
            "profile_version": profile_version,
        }
    )
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    # Map the first 8 bytes to a stable fraction in [0, 1].
    numerator = int.from_bytes(digest[:8], "big")
    return numerator / float(0xFFFFFFFFFFFFFFFF)


__all__ = [
    "DefinitionResources",
    "DefinitionStatus",
    "EvaluationProfileRef",
    "EvidenceEntry",
    "EvidencePolicy",
    "IsolationDowngrade",
    "Lease",
    "NetworkMode",
    "ResourceQuota",
    "RunEvidence",
    "RuntimePolicy",
    "RuntimeProfile",
    "RuntimeTier",
    "Score",
    "SimulationCapabilities",
    "SimulationDefinition",
    "TrustTier",
    "compute_score",
    "definition_from_document",
    "ensure_no_downgrade",
    "evidence_genesis",
    "lease_signing_payload",
    "required_tier_for",
    "tier_rank",
]
