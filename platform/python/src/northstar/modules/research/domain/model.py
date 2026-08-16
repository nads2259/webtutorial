"""Research object model (docs/37 §1-4, FR-RSH-001..006). Pure and infrastructure-free (rule 10).

The aggregate mirrors the research object model: a :class:`Workspace` scopes a
:class:`ResearchProject` which contains :class:`ResearchDocument` (reviews/notes built on the shared
knowledge typed-block :class:`ContentTree`, reused — one authoritative block model, LAW-04).
Publishing mints an IMMUTABLE :class:`ResearchRevision` with a ``content_hash`` (FR-RSH-002/006).

Evidence and claims encode the central invariant: an :class:`EvidenceRecord` carries provenance and
a stable version identity (immutable once created); a :class:`Claim` MUST link to >=1 evidence and
its constructor REJECTS zero evidence (FR-RSH-003). :class:`DatasetRef`/:class:`ExperimentRef`
record ownership, license, classification, integrity hash, retention and version (FR-RSH-004).
Everything here is a frozen value object; no database, network or provider SDK is reachable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from northstar.kernel.context import Actor
from northstar.modules.knowledge.domain.blocks import ContentTree

from .errors import ClaimWithoutEvidence, ResearchInvariantViolation

RES_WORKSPACE = "research.workspace"
RES_PROJECT = "research.project"
RES_DOCUMENT = "research.document"
RES_EVIDENCE = "research.evidence"


def _require(condition: bool, message: str, code: str = "research.invariant") -> None:
    if not condition:
        raise ResearchInvariantViolation(message, code=code)


class DocumentStatus(StrEnum):
    """Research-document lifecycle (mirrors research-document.schema.json ``status``)."""

    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ReproducibilityLevel(StrEnum):
    """Reproducibility levels R0-R4 (docs/37 §4). Never imply more than evidence supports."""

    R0_NARRATIVE = "R0"
    R1_CAPTURED = "R1"
    R2_REPEATABLE = "R2"
    R3_REPRODUCIBLE = "R3"
    R4_REVIEWED = "R4"


class EvidenceKind(StrEnum):
    """Where an evidence record's provenance originates (docs/37 §2/§6)."""

    RETRIEVED_PASSAGE = "retrieved_passage"
    CITATION = "citation"
    DATASET = "dataset"
    EXPERIMENT = "experiment"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class Workspace:
    """A research workspace, always scoped to an organization/tenant (FR-RSH-001)."""

    workspace_id: str
    organization_id: str
    name: str
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.workspace_id), "workspace_id required", code="research.workspace.id")
        _require(
            bool(self.organization_id),
            "organization_id required",
            code="research.workspace.scope",
        )
        _require(1 <= len(self.name) <= 300, "workspace name must be 1..300 chars")


@dataclass(frozen=True, slots=True)
class ResearchProject:
    """A project within a workspace (research questions, hypotheses, documents live under it)."""

    project_id: str
    workspace_id: str
    organization_id: str
    title: str
    created_by: str
    created_at: datetime
    research_question: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.project_id), "project_id required", code="research.project.id")
        _require(bool(self.workspace_id), "workspace_id required", code="research.project.scope")
        _require(
            bool(self.organization_id),
            "organization_id required",
            code="research.project.scope",
        )
        _require(1 <= len(self.title) <= 300, "project title must be 1..300 chars")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """An evidence record with provenance + stable version identity (FR-RSH-003).

    ``version_hash`` is the stable version identity: it is computed once from the provenance +
    excerpt and the record is a frozen value (immutable once created), so a claim that references
    it can never have its evidence silently mutated underneath it. ``object_id``/``revision_id``/
    ``block_id``/``chunk_id`` carry the exact source provenance (e.g. the knowledge revision a
    verified AI citation resolved to); ``verified`` marks evidence checked against retrieved source.
    """

    evidence_id: str
    organization_id: str
    document_id: str
    kind: EvidenceKind
    excerpt: str
    version_hash: str
    created_at: datetime
    object_id: str | None = None
    revision_id: str | None = None
    block_id: str | None = None
    chunk_id: str | None = None
    source_uri: str | None = None
    verified: bool = False

    def __post_init__(self) -> None:
        _require(bool(self.evidence_id), "evidence_id required", code="research.evidence.id")
        _require(
            bool(self.organization_id),
            "organization_id required",
            code="research.evidence.scope",
        )
        _require(
            bool(self.version_hash),
            "evidence requires a stable version_hash (version identity)",
            code="research.evidence.version",
        )

    @property
    def provenance(self) -> dict[str, str | None]:
        """The canonical provenance mapping used by the export/citation projection."""
        return {
            "object_id": self.object_id,
            "revision_id": self.revision_id,
            "block_id": self.block_id,
            "chunk_id": self.chunk_id,
            "source_uri": self.source_uri,
            "version_hash": self.version_hash,
        }


def compute_version_hash(
    *,
    excerpt: str,
    object_id: str | None,
    revision_id: str | None,
    block_id: str | None,
    chunk_id: str | None,
    source_uri: str | None,
) -> str:
    """Deterministically compute an evidence record's stable version identity (``sha256:``)."""
    canonical = "|".join(
        part or "" for part in (object_id, revision_id, block_id, chunk_id, source_uri, excerpt)
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True, slots=True)
class Claim:
    """A research claim that MUST link to >=1 evidence record (FR-RSH-003).

    The constructor is the invariant: empty ``evidence_ids`` raises :class:`ClaimWithoutEvidence`,
    so a claim with zero evidence cannot even be built — and therefore can never be persisted. An
    AI-produced claim whose citations all failed verification carries no evidence and is rejected
    here (FR-RSH-005). ``generated`` distinguishes AI-generated inference from source-grounded
    author claims (docs/37 §6); ``confidence`` is optional in [0, 1].
    """

    claim_id: str
    organization_id: str
    document_id: str
    statement: str
    evidence_ids: tuple[str, ...]
    created_by: str
    created_at: datetime
    confidence: float | None = None
    generated: bool = False

    def __post_init__(self) -> None:
        _require(bool(self.claim_id), "claim_id required", code="research.claim.id")
        _require(bool(self.statement.strip()), "claim statement must be non-empty")
        if len(self.evidence_ids) == 0:
            raise ClaimWithoutEvidence()
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            _require(False, "confidence must be within [0, 1]", code="research.claim.confidence")


@dataclass(frozen=True, slots=True)
class DatasetRef:
    """A dataset reference recording ownership, license, classification, integrity + retention.

    FR-RSH-004: datasets record ownership, license, classification, integrity hash and retention
    constraints. ``version`` gives stable version identity; ``integrity_hash`` binds the referenced
    bytes so a swapped dataset is detectable.
    """

    dataset_ref_id: str
    organization_id: str
    project_id: str
    name: str
    owner_id: str
    version: str
    integrity_hash: str
    license: str
    classification: str
    retention: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.dataset_ref_id), "dataset_ref_id required", code="research.dataset.id")
        _require(
            bool(self.organization_id), "organization_id required", code="research.dataset.scope"
        )
        _require(bool(self.owner_id), "dataset owner_id required", code="research.dataset.owner")
        _require(bool(self.version), "dataset version required", code="research.dataset.version")
        _require(
            bool(self.integrity_hash),
            "dataset integrity_hash required",
            code="research.dataset.integrity",
        )


@dataclass(frozen=True, slots=True)
class ExperimentRef:
    """An experiment/notebook-run reference with ownership + reproducibility metadata (FR-RSH-004).

    Executable work runs in the simulation runtime (docs/37 §3, IMPL-016, out of scope here); this
    reference records ownership, version, the datasets used, the environment digest, the seed and a
    declared reproducibility level so the product never implies more reproducibility than evidence
    supports.
    """

    experiment_ref_id: str
    organization_id: str
    project_id: str
    name: str
    owner_id: str
    version: str
    reproducibility: ReproducibilityLevel
    created_at: datetime
    dataset_ref_ids: tuple[str, ...] = ()
    environment_digest: str | None = None
    seed: str | None = None

    def __post_init__(self) -> None:
        _require(
            bool(self.experiment_ref_id),
            "experiment_ref_id required",
            code="research.experiment.id",
        )
        _require(
            bool(self.organization_id),
            "organization_id required",
            code="research.experiment.scope",
        )
        _require(
            bool(self.owner_id), "experiment owner_id required", code="research.experiment.owner"
        )
        _require(
            bool(self.version), "experiment version required", code="research.experiment.version"
        )


@dataclass(frozen=True, slots=True)
class ResearchDocument:
    """A research document (literature review/notes) built on the shared typed-block tree.

    Reuses the knowledge :class:`ContentTree` (one authoritative block model, LAW-04). Identity +
    lifecycle pointer; the mutable working ``tree`` is the current draft content and
    ``latest_revision_id`` points at the newest immutable published revision (FR-RSH-002/006).
    """

    document_id: str
    organization_id: str
    project_id: str
    title: str
    tree: ContentTree
    status: DocumentStatus = DocumentStatus.DRAFT
    latest_revision_id: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.document_id), "document_id required", code="research.document.id")
        _require(
            bool(self.organization_id),
            "organization_id required",
            code="research.document.scope",
        )
        _require(bool(self.project_id), "project_id required", code="research.document.scope")
        _require(1 <= len(self.title) <= 300, "document title must be 1..300 chars")


@dataclass(frozen=True, slots=True)
class ResearchRevision:
    """An IMMUTABLE published research-document revision with provenance (FR-RSH-002/006).

    ``content_hash`` must match the tree's canonical hash (provenance integrity);
    ``parent_revision_id`` points at its predecessor so corrections form an append-only chain. The
    frozen dataclass plus the :meth:`mutate` guard make in-place mutation impossible.
    """

    revision_id: str
    document_id: str
    organization_id: str
    title: str
    tree: ContentTree
    content_hash: str
    created_by: Actor
    created_at: datetime
    parent_revision_id: str | None = None
    status: DocumentStatus = DocumentStatus.PUBLISHED

    def __post_init__(self) -> None:
        _require(bool(self.revision_id), "revision_id required", code="research.revision.id")
        _require(1 <= len(self.title) <= 300, "revision title must be 1..300 chars")
        _require(
            self.content_hash == self.tree.content_hash(),
            "content_hash must match the content tree (provenance integrity)",
            code="research.revision.hash",
        )
        _require(
            self.created_at.tzinfo is not None,
            "created_at must be timezone-aware (UTC)",
            code="research.revision.time",
        )

    def mutate(self, *_args: object, **_kwargs: object) -> None:
        from .errors import ImmutableRevisionError

        raise ImmutableRevisionError(self.revision_id)


def new_revision(
    *,
    revision_id: str,
    document: ResearchDocument,
    title: str,
    tree: ContentTree,
    created_by: Actor,
    created_at: datetime,
    parent_revision_id: str | None,
) -> ResearchRevision:
    """Build the first (or a subsequent) immutable revision, computing its provenance hash."""
    return ResearchRevision(
        revision_id=revision_id,
        document_id=document.document_id,
        organization_id=document.organization_id,
        title=title,
        tree=tree,
        content_hash=tree.content_hash(),
        created_by=created_by,
        created_at=created_at,
        parent_revision_id=parent_revision_id,
    )


@dataclass(frozen=True, slots=True)
class StoredDocumentBlock:
    """A research typed block attached to a document, with its canonical block projection.

    ``kind`` is one of :data:`..domain.blocks.RESEARCH_BLOCK_KINDS`; ``payload`` is the block's
    canonical ``to_document_block()`` projection (reuses the shared typed-block model). ``position``
    orders the block within the document.
    """

    block_id: str
    organization_id: str
    document_id: str
    kind: str
    position: int
    payload: dict[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.block_id), "block_id required", code="research.document.block.id")
        _require(
            bool(self.organization_id),
            "organization_id required",
            code="research.document.block.scope",
        )
        _require(
            bool(self.document_id), "document_id required", code="research.document.block.document"
        )


@dataclass(frozen=True, slots=True)
class SimulationLink:
    """A document's link to a simulation, recorded by IDENTITY only (never a cross-module row).

    The identity (``simulation_id``/``version``/``content_hash``) is resolved through the
    :class:`..application.ports.SimulationRefPort`; research never reaches the simulation module's
    tables (LAW-13/rule 10).
    """

    document_id: str
    organization_id: str
    simulation_id: str
    version: str
    content_hash: str
    linked_by: str
    linked_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.document_id), "document_id required", code="research.simlink.document")
        _require(
            bool(self.organization_id), "organization_id required", code="research.simlink.scope"
        )
        _require(
            bool(self.simulation_id), "simulation_id required", code="research.simlink.simulation"
        )


@dataclass(frozen=True, slots=True)
class ResearchDocumentBundle:
    """The full export/import shape: a document revision plus its claims, evidence and datasets.

    Used by the canonical interchange (see :mod:`.interchange`) so an export preserves document
    structure AND citations (evidence) deterministically and round-trips (FR-RSH-006).
    """

    document_id: str
    revision_id: str
    title: str
    status: DocumentStatus
    tree: ContentTree
    claims: tuple[Claim, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    datasets: tuple[DatasetRef, ...] = ()
    created_by: str = ""
    created_at: datetime | None = None
    ai_contributions: tuple[str, ...] = field(default_factory=tuple)
