"""Ports (abstractions) for the research application layer (rule 10/20, DIP).

Every infrastructure/cross-module seam is a Protocol so the capabilities stay infrastructure-free:

* :class:`ResearchRepositoryPort` — the module's own tenant-scoped persistence (LAW-13).
* :class:`AiDraftPort` — the seam onto the AI module's ``ai.answer`` capability. Research REUSES the
  one governed RAG pipeline (no second AI path, FR-RSH-005); the adapter dispatches ``ai.answer`` so
  the citation verifier there guarantees every returned citation is grounded in retrieved evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain.model import (
    Claim,
    DatasetRef,
    EvidenceRecord,
    ExperimentRef,
    ResearchDocument,
    ResearchProject,
    ResearchRevision,
    SimulationLink,
    StoredDocumentBlock,
    Workspace,
)
from ..domain.project import (
    ContributorRole,
    Hypothesis,
    ProjectMembership,
    ResearchMethod,
    ResearchQuestion,
)
from ..domain.review import DocumentReview, ReviewEvent


@runtime_checkable
class ResearchRepositoryPort(Protocol):
    """Persists the research aggregate; every method is tenant-scoped (rule 50, LAW-13)."""

    def add_workspace(self, workspace: Workspace) -> None: ...

    def get_workspace(self, *, organization_id: str, workspace_id: str) -> Workspace | None: ...

    def add_project(self, project: ResearchProject) -> None: ...

    def get_project(self, *, organization_id: str, project_id: str) -> ResearchProject | None: ...

    def update_project(self, project: ResearchProject) -> None: ...

    # -- project structure: questions / hypotheses / methods (FR-RSH-001) ----------------------

    def add_question(self, question: ResearchQuestion) -> None: ...

    def get_question(
        self, *, organization_id: str, question_id: str
    ) -> ResearchQuestion | None: ...

    def list_questions(
        self, *, organization_id: str, project_id: str
    ) -> Sequence[ResearchQuestion]: ...

    def add_hypothesis(self, hypothesis: Hypothesis) -> None: ...

    def list_hypotheses(self, *, organization_id: str, project_id: str) -> Sequence[Hypothesis]: ...

    def add_method(self, method: ResearchMethod) -> None: ...

    def list_methods(
        self, *, organization_id: str, project_id: str
    ) -> Sequence[ResearchMethod]: ...

    # -- role-scoped membership (FR-RSH-001) ---------------------------------------------------

    def set_membership(self, membership: ProjectMembership) -> None: ...

    def get_membership_role(
        self, *, organization_id: str, project_id: str, subject_id: str
    ) -> ContributorRole | None: ...

    def list_memberships(
        self, *, organization_id: str, project_id: str
    ) -> Sequence[ProjectMembership]: ...

    # -- document structure: rich blocks + simulation link (FR-RSH-002) ------------------------

    def add_document_block(self, block: StoredDocumentBlock) -> None: ...

    def list_document_blocks(
        self, *, organization_id: str, document_id: str
    ) -> Sequence[StoredDocumentBlock]: ...

    def set_simulation_link(self, link: SimulationLink) -> None: ...

    def get_simulation_link(
        self, *, organization_id: str, document_id: str
    ) -> SimulationLink | None: ...

    # -- peer review journey (FR-RSH-002) ------------------------------------------------------

    def add_review(self, review: DocumentReview) -> None: ...

    def get_review(self, *, organization_id: str, document_id: str) -> DocumentReview | None: ...

    def update_review(self, review: DocumentReview) -> None: ...

    def add_review_event(self, event: ReviewEvent) -> None: ...

    def list_review_events(
        self, *, organization_id: str, review_id: str
    ) -> Sequence[ReviewEvent]: ...

    def add_document(self, document: ResearchDocument) -> None: ...

    def get_document(
        self, *, organization_id: str, document_id: str
    ) -> ResearchDocument | None: ...

    def publish(
        self, *, organization_id: str, document: ResearchDocument, revision: ResearchRevision
    ) -> None: ...

    def get_revision(
        self, *, organization_id: str, revision_id: str
    ) -> ResearchRevision | None: ...

    def add_evidence(self, evidence: EvidenceRecord) -> None: ...

    def get_evidence(self, *, organization_id: str, evidence_id: str) -> EvidenceRecord | None: ...

    def list_evidence(
        self, *, organization_id: str, document_id: str
    ) -> Sequence[EvidenceRecord]: ...

    def add_claim(self, claim: Claim) -> None: ...

    def list_claims(self, *, organization_id: str, document_id: str) -> Sequence[Claim]: ...

    def add_dataset(self, dataset: DatasetRef) -> None: ...

    def list_datasets(self, *, organization_id: str, project_id: str) -> Sequence[DatasetRef]: ...

    def add_experiment(self, experiment: ExperimentRef) -> None: ...

    def list_experiments(
        self, *, organization_id: str, project_id: str
    ) -> Sequence[ExperimentRef]: ...


@dataclass(frozen=True, slots=True)
class SimulationRef:
    """The IDENTITY of a published simulation, as resolved through :class:`SimulationRefPort`."""

    simulation_id: str
    version: str
    content_hash: str


@runtime_checkable
class SimulationRefPort(Protocol):
    """Seam onto the simulation module's IDENTITY (docs/37 §3, LAW-13).

    Research links a simulation only by its identity; it never reaches the simulation module's
    tables/domain. ``resolve`` returns the simulation's stable identity (id/version/content hash) or
    ``None`` when the (tenant, simulation_id, version) is unknown, so a link to a non-existent
    simulation is refused (deny-by-default).
    """

    def resolve(
        self, *, organization_id: str, simulation_id: str, version: str
    ) -> SimulationRef | None: ...


@dataclass(frozen=True, slots=True)
class DraftedCitation:
    """One verified citation the AI pipeline returned (identity-bearing, already grounded)."""

    object_id: str
    revision_id: str
    block_id: str
    chunk_id: str
    claim: str


@dataclass(frozen=True, slots=True)
class AiDraftResult:
    """The normalized result of reusing ``ai.answer`` for a research draft (FR-RSH-005).

    ``citations`` contains ONLY citations the AI module verified against actually-retrieved evidence
    (fabricated/unsupported ones were already dropped). ``refused`` is ``True`` when a governance
    defense downgraded the answer, in which case there is no groundable claim.
    """

    answer: str
    refused: bool
    citations: tuple[DraftedCitation, ...] = field(default_factory=tuple)
    provider: str = ""
    model: str = ""
    prompt_package: str = ""
    trace_id: str = ""


@runtime_checkable
class AiDraftPort(Protocol):
    """Seam onto the AI module's ``ai.answer`` capability (the single authoritative AI path)."""

    def draft(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        question: str,
        package_id: str,
        version: str,
        top_k: int,
        data_classification: str,
    ) -> AiDraftResult: ...
