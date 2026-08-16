"""Pure reproducibility-package model + verify/rebuild (docs/37 §3-4/§8, EVAL-RES-001).

Assembles a deterministic, self-contained *reproducibility package* for a published research
document so an independent reviewer can trace every claim to its evidence and either reproduce the
declared output or see an EXPLICIT limitation (never a silent gap). The package carries the
published document projection, ALL claims, each claim's linked evidence (provenance + version_hash),
dataset/experiment references (ownership/version/integrity), the declared environment/tool versions,
simulation/notebook references (experiment refs) and the version identities — plus a
content-addressed :class:`PackageManifest` (a stable per-item hash + a package hash).

Determinism (LAW-06/07): identical inputs yield an identical ``package_hash`` because every item is
projected to a canonical dict and hashed with canonical JSON (sorted keys), and the manifest entries
are ordered canonically. The wall-clock ``generated_at`` is deliberately NOT part of the hash.

:func:`verify_package` re-derives the manifest purely from the package's own item data (an
independent rebuild) and returns a :class:`ReviewReport`: manifest integrity (any mutation of an
item or the manifest is detected), the traceable-claims count, the explicit limitations list and a
pass/fail matching the "trace every claim / reproduce declared output or explicit limitation"
threshold. Infrastructure-free (rule 10): only stdlib + the pure research model are used.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .errors import ClaimWithoutEvidence, ResearchInvariantViolation
from .model import Claim, DatasetRef, EvidenceRecord, ExperimentRef

_UNVERIFIED_EVIDENCE = "unverified_evidence"


def _canonical_json(value: object) -> str:
    """Canonical JSON encoding (sorted keys, tight separators) — byte-stable across runs."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: object) -> str:
    """Content-address ``value`` as ``sha256:<hex>`` over its canonical JSON encoding."""
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _claim_dict(claim: Claim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "statement": claim.statement,
        "evidence_ids": list(claim.evidence_ids),
        "confidence": claim.confidence,
        "generated": claim.generated,
    }


def _evidence_dict(evidence: EvidenceRecord) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "document_id": evidence.document_id,
        "kind": evidence.kind.value,
        "excerpt": evidence.excerpt,
        "version_hash": evidence.version_hash,
        "provenance": evidence.provenance,
        "verified": evidence.verified,
    }


def _dataset_dict(dataset: DatasetRef) -> dict[str, Any]:
    return {
        "dataset_ref_id": dataset.dataset_ref_id,
        "project_id": dataset.project_id,
        "name": dataset.name,
        "owner_id": dataset.owner_id,
        "version": dataset.version,
        "integrity_hash": dataset.integrity_hash,
        "license": dataset.license,
        "classification": dataset.classification,
        "retention": dataset.retention,
    }


def _experiment_dict(experiment: ExperimentRef) -> dict[str, Any]:
    return {
        "experiment_ref_id": experiment.experiment_ref_id,
        "project_id": experiment.project_id,
        "name": experiment.name,
        "owner_id": experiment.owner_id,
        "version": experiment.version,
        "reproducibility": experiment.reproducibility.value,
        "dataset_ref_ids": list(experiment.dataset_ref_ids),
        "environment_digest": experiment.environment_digest,
        "seed": experiment.seed,
    }


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One content-addressed manifest line: the stable hash of a single package item."""

    item_type: str
    item_id: str
    item_hash: str

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.item_type, self.item_id, self.item_hash)


@dataclass(frozen=True, slots=True)
class PackageManifest:
    """The content-addressed manifest: per-item hashes (canonically ordered) + a package hash."""

    entries: tuple[ManifestEntry, ...]
    package_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [
                {"item_type": e.item_type, "item_id": e.item_id, "item_hash": e.item_hash}
                for e in self.entries
            ],
            "package_hash": self.package_hash,
        }


@dataclass(frozen=True, slots=True)
class LimitationEntry:
    """An EXPLICIT limitation: a claim whose declared output cannot be independently reproduced."""

    claim_id: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReproducibilityPackage:
    """A deterministic, self-contained reproducibility package for a published research document."""

    package_id: str
    organization_id: str
    document_id: str
    revision_id: str
    document: dict[str, Any]
    claims: tuple[Claim, ...]
    evidence: tuple[EvidenceRecord, ...]
    datasets: tuple[DatasetRef, ...]
    experiments: tuple[ExperimentRef, ...]
    environment: dict[str, str]
    version_identities: dict[str, Any]
    limitations: tuple[LimitationEntry, ...]
    manifest: PackageManifest
    generated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """Serialize the package to a canonical, self-contained mapping (JSON-ready)."""
        return {
            "package_id": self.package_id,
            "organization_id": self.organization_id,
            "document_id": self.document_id,
            "revision_id": self.revision_id,
            "document": self.document,
            "claims": [_claim_dict(c) for c in self.claims],
            "evidence": [_evidence_dict(e) for e in self.evidence],
            "datasets": [_dataset_dict(d) for d in self.datasets],
            "experiments": [_experiment_dict(x) for x in self.experiments],
            "environment": dict(self.environment),
            "version_identities": self.version_identities,
            "limitations": [
                {"claim_id": lim.claim_id, "reason": lim.reason, "detail": lim.detail}
                for lim in self.limitations
            ],
            "manifest": self.manifest.to_dict(),
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ReviewReport:
    """An independent reviewer's report: integrity + traceability + explicit limitations + verdict.

    ``passed`` is ``True`` only when the manifest integrity holds AND every claim is traceable to at
    least one evidence record in the package. Non-reproducible declared outputs do NOT fail the
    report — they are surfaced as EXPLICIT limitations, exactly as the threshold allows ("reproduce
    declared outputs OR see explicit limitation").
    """

    package_id: str
    integrity_ok: bool
    total_claims: int
    traceable_claims: int
    limitations: tuple[LimitationEntry, ...]
    passed: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "integrity_ok": self.integrity_ok,
            "total_claims": self.total_claims,
            "traceable_claims": self.traceable_claims,
            "limitations": [
                {"claim_id": lim.claim_id, "reason": lim.reason, "detail": lim.detail}
                for lim in self.limitations
            ],
            "passed": self.passed,
            "reasons": list(self.reasons),
        }


def _version_identities(
    *,
    document_id: str,
    revision_id: str,
    content_hash: str,
    evidence: tuple[EvidenceRecord, ...],
    datasets: tuple[DatasetRef, ...],
    experiments: tuple[ExperimentRef, ...],
) -> dict[str, Any]:
    """Collect the stable version identities every referenced artifact declares (deterministic)."""
    return {
        "document_id": document_id,
        "revision_id": revision_id,
        "content_hash": content_hash,
        "evidence": {e.evidence_id: e.version_hash for e in evidence},
        "datasets": {d.dataset_ref_id: d.version for d in datasets},
        "experiments": {x.experiment_ref_id: x.version for x in experiments},
    }


def _resolve_limitations(
    claims: tuple[Claim, ...], evidence_by_id: Mapping[str, EvidenceRecord]
) -> tuple[LimitationEntry, ...]:
    """Compute the EXPLICIT limitations: claims whose declared output is not reproducible.

    A claim's declared output is reproducible only when every one of its resolved evidence records
    is independently ``verified`` against its source. A claim resting on any unverified evidence is
    recorded as an explicit limitation (never silently dropped).
    """
    limitations: list[LimitationEntry] = []
    for claim in claims:
        resolved = [evidence_by_id[eid] for eid in claim.evidence_ids if eid in evidence_by_id]
        unverified = [e.evidence_id for e in resolved if not e.verified]
        if unverified:
            limitations.append(
                LimitationEntry(
                    claim_id=claim.claim_id,
                    reason=_UNVERIFIED_EVIDENCE,
                    detail=(
                        "declared output cannot be independently reproduced: unverified evidence "
                        + ", ".join(sorted(unverified))
                    ),
                )
            )
    return tuple(sorted(limitations, key=lambda lim: lim.claim_id))


def _build_manifest(
    *,
    package_id: str,
    organization_id: str,
    document: dict[str, Any],
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRecord, ...],
    datasets: tuple[DatasetRef, ...],
    experiments: tuple[ExperimentRef, ...],
    environment: Mapping[str, str],
    version_identities: Mapping[str, Any],
) -> PackageManifest:
    """Derive the content-addressed manifest from the package items (canonical ordering)."""
    entries: list[ManifestEntry] = [
        ManifestEntry("document", str(document.get("document_id", "")), _hash(document)),
        ManifestEntry("environment", "declared", _hash(dict(environment))),
        ManifestEntry("versions", "identities", _hash(dict(version_identities))),
    ]
    entries.extend(ManifestEntry("claim", c.claim_id, _hash(_claim_dict(c))) for c in claims)
    entries.extend(
        ManifestEntry("evidence", e.evidence_id, _hash(_evidence_dict(e))) for e in evidence
    )
    entries.extend(
        ManifestEntry("dataset", d.dataset_ref_id, _hash(_dataset_dict(d))) for d in datasets
    )
    entries.extend(
        ManifestEntry("experiment", x.experiment_ref_id, _hash(_experiment_dict(x)))
        for x in experiments
    )
    ordered = tuple(sorted(entries, key=ManifestEntry.as_tuple))
    package_hash = _hash(
        {
            "package_id": package_id,
            "organization_id": organization_id,
            "entries": [e.as_tuple() for e in ordered],
        }
    )
    return PackageManifest(entries=ordered, package_hash=package_hash)


def build_package(
    *,
    package_id: str,
    organization_id: str,
    document: dict[str, Any],
    document_id: str,
    revision_id: str,
    content_hash: str,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRecord, ...],
    datasets: tuple[DatasetRef, ...],
    experiments: tuple[ExperimentRef, ...],
    environment: Mapping[str, str],
    generated_at: datetime,
) -> ReproducibilityPackage:
    """Assemble a deterministic reproducibility package + its content-addressed manifest.

    Every claim MUST resolve to >=1 evidence record present in the package (reuses the
    claim->evidence invariant): a claim whose declared evidence is absent from the package raises
    :class:`ClaimWithoutEvidence` rather than being silently dropped. A claim that resolves but
    rests on unverified evidence is retained with an EXPLICIT limitation entry (see
    :func:`verify_package`).
    """
    if not organization_id:
        raise ResearchInvariantViolation(
            "organization_id required", code="research.reproducibility.scope"
        )
    evidence_by_id = {e.evidence_id: e for e in evidence}
    for claim in claims:
        if not any(eid in evidence_by_id for eid in claim.evidence_ids):
            raise ClaimWithoutEvidence()
    version_identities = _version_identities(
        document_id=document_id,
        revision_id=revision_id,
        content_hash=content_hash,
        evidence=evidence,
        datasets=datasets,
        experiments=experiments,
    )
    manifest = _build_manifest(
        package_id=package_id,
        organization_id=organization_id,
        document=document,
        claims=claims,
        evidence=evidence,
        datasets=datasets,
        experiments=experiments,
        environment=environment,
        version_identities=version_identities,
    )
    limitations = _resolve_limitations(claims, evidence_by_id)
    return ReproducibilityPackage(
        package_id=package_id,
        organization_id=organization_id,
        document_id=document_id,
        revision_id=revision_id,
        document=document,
        claims=claims,
        evidence=evidence,
        datasets=datasets,
        experiments=experiments,
        environment=dict(environment),
        version_identities=version_identities,
        limitations=limitations,
        manifest=manifest,
        generated_at=generated_at,
    )


def verify_package(package: ReproducibilityPackage) -> ReviewReport:
    """Independently rebuild the manifest from the package and return a review report.

    The manifest is re-derived purely from the package's own item data, so any mutation of an item
    (e.g. a tampered evidence excerpt) or of the stored manifest itself flips ``integrity_ok`` to
    ``False``. Traceability is re-checked (every claim must resolve to >=1 evidence in the package)
    and the explicit limitations are recomputed. ``passed`` requires integrity AND full
    traceability.
    """
    rebuilt = _build_manifest(
        package_id=package.package_id,
        organization_id=package.organization_id,
        document=package.document,
        claims=package.claims,
        evidence=package.evidence,
        datasets=package.datasets,
        experiments=package.experiments,
        environment=package.environment,
        version_identities=package.version_identities,
    )
    integrity_ok = rebuilt.package_hash == package.manifest.package_hash and tuple(
        e.as_tuple() for e in rebuilt.entries
    ) == tuple(e.as_tuple() for e in package.manifest.entries)

    evidence_by_id = {e.evidence_id: e for e in package.evidence}
    traceable = 0
    untraceable: list[str] = []
    for claim in package.claims:
        if any(eid in evidence_by_id for eid in claim.evidence_ids):
            traceable += 1
        else:
            untraceable.append(claim.claim_id)

    limitations = _resolve_limitations(package.claims, evidence_by_id)

    reasons: list[str] = []
    if not integrity_ok:
        reasons.append("manifest_integrity_failed: recomputed manifest does not match")
    if untraceable:
        reasons.append("untraceable_claims: " + ", ".join(sorted(untraceable)))

    passed = integrity_ok and not untraceable
    return ReviewReport(
        package_id=package.package_id,
        integrity_ok=integrity_ok,
        total_claims=len(package.claims),
        traceable_claims=traceable,
        limitations=limitations,
        passed=passed,
        reasons=tuple(reasons),
    )
