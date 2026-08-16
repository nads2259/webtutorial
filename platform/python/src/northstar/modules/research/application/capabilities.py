"""Research capabilities: one authoritative implementation per action (LAW-04).

Every handler runs through the kernel command/query bus, so each mutation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope + acting subject come from the
authenticated :class:`RequestContext`, NEVER from the payload (rule 50). Handlers depend only on
:mod:`.ports` and the pure :mod:`..domain`.

The central invariants live in the domain and are enforced here by construction:

* ``research.claim.assert`` builds a :class:`Claim` — a zero-evidence claim raises
  :class:`ClaimWithoutEvidence` and is never persisted (FR-RSH-003).
* ``research.document.ai-draft`` reuses ``ai.answer`` through :class:`AiDraftPort`, maps its
  VERIFIED citations to evidence records and then asserts a claim over them — an uncited/fabricated
  or refused draft yields no evidence and is therefore rejected, so it cannot be persisted
  (FR-RSH-005, EVAL-RSH-005).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from northstar.kernel.context import Actor
from northstar.modules.knowledge.domain.blocks import ContentTree

from ..domain.blocks import build_research_block
from ..domain.errors import ResearchNotFound, SimulationNotResolved, TenantScopeMissing
from ..domain.interchange import to_research_document
from ..domain.model import (
    Claim,
    DatasetRef,
    DocumentStatus,
    EvidenceKind,
    EvidenceRecord,
    ExperimentRef,
    ReproducibilityLevel,
    ResearchDocument,
    ResearchDocumentBundle,
    ResearchProject,
    SimulationLink,
    StoredDocumentBlock,
    Workspace,
    compute_version_hash,
    new_revision,
)
from ..domain.project import (
    ContributorRole,
    Hypothesis,
    ProjectMembership,
    ResearchMethod,
    ResearchQuestion,
    authorize_role,
)
from ..domain.reproducibility import (
    LimitationEntry,
    ReproducibilityPackage,
    ReviewReport,
    build_package,
    verify_package,
)
from ..domain.review import (
    DocumentReview,
    ReviewAction,
    ReviewEvent,
    ReviewStatus,
    authorize_actor,
    next_status,
)
from .ports import AiDraftPort, ResearchRepositoryPort, SimulationRefPort

CAP_VERSION = "1.0.0"

CAP_CREATE_WORKSPACE = "research.workspace.create"
CAP_CREATE_PROJECT = "research.project.create"
CAP_AUTHOR_DOCUMENT = "research.document.author"
CAP_PUBLISH_DOCUMENT = "research.document.publish"
CAP_REGISTER_EVIDENCE = "research.evidence.register"
CAP_ASSERT_CLAIM = "research.claim.assert"
CAP_REGISTER_DATASET = "research.dataset.register"
CAP_REGISTER_EXPERIMENT = "research.experiment.register"
CAP_AI_DRAFT = "research.document.ai-draft"
CAP_EXPORT_DOCUMENT = "research.document.export"
CAP_PACKAGE_REPRODUCIBILITY = "research.reproducibility.package"
CAP_UPDATE_PROJECT = "research.project.update"
CAP_ADD_QUESTION = "research.project.add-question"
CAP_ADD_HYPOTHESIS = "research.project.add-hypothesis"
CAP_ADD_METHOD = "research.project.add-method"
CAP_ASSIGN_ROLE = "research.project.assign-role"
CAP_ADD_DOCUMENT_BLOCK = "research.document.add-block"
CAP_LINK_SIMULATION = "research.document.link-simulation"
CAP_OPEN_REVIEW = "research.document.open-review"
CAP_REVIEW_TRANSITION = "research.document.review-transition"

RESEARCH_CAPABILITIES: tuple[str, ...] = (
    CAP_CREATE_WORKSPACE,
    CAP_CREATE_PROJECT,
    CAP_AUTHOR_DOCUMENT,
    CAP_PUBLISH_DOCUMENT,
    CAP_REGISTER_EVIDENCE,
    CAP_ASSERT_CLAIM,
    CAP_REGISTER_DATASET,
    CAP_REGISTER_EXPERIMENT,
    CAP_AI_DRAFT,
    CAP_EXPORT_DOCUMENT,
    CAP_PACKAGE_REPRODUCIBILITY,
    CAP_UPDATE_PROJECT,
    CAP_ADD_QUESTION,
    CAP_ADD_HYPOTHESIS,
    CAP_ADD_METHOD,
    CAP_ASSIGN_ROLE,
    CAP_ADD_DOCUMENT_BLOCK,
    CAP_LINK_SIMULATION,
    CAP_OPEN_REVIEW,
    CAP_REVIEW_TRANSITION,
)

RES_RESEARCH = "research.workspace"

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateWorkspaceCommand:
    name: str


@dataclass(frozen=True, slots=True)
class CreateWorkspaceResult:
    workspace_id: str
    organization_id: str


@dataclass(frozen=True, slots=True)
class CreateProjectCommand:
    workspace_id: str
    title: str
    research_question: str | None = None


@dataclass(frozen=True, slots=True)
class CreateProjectResult:
    project_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class AuthorDocumentCommand:
    project_id: str
    title: str
    blocks: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class AuthorDocumentResult:
    document_id: str
    project_id: str
    status: str


@dataclass(frozen=True, slots=True)
class PublishDocumentCommand:
    document_id: str
    title: str


@dataclass(frozen=True, slots=True)
class PublishDocumentResult:
    document_id: str
    revision_id: str
    parent_revision_id: str | None
    content_hash: str


@dataclass(frozen=True, slots=True)
class RegisterEvidenceCommand:
    document_id: str
    excerpt: str
    kind: str = EvidenceKind.EXTERNAL.value
    object_id: str | None = None
    revision_id: str | None = None
    block_id: str | None = None
    chunk_id: str | None = None
    source_uri: str | None = None
    verified: bool = False


@dataclass(frozen=True, slots=True)
class RegisterEvidenceResult:
    evidence_id: str
    version_hash: str


@dataclass(frozen=True, slots=True)
class AssertClaimCommand:
    document_id: str
    statement: str
    evidence_ids: tuple[str, ...]
    confidence: float | None = None
    generated: bool = False


@dataclass(frozen=True, slots=True)
class AssertClaimResult:
    claim_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegisterDatasetCommand:
    project_id: str
    name: str
    owner_id: str
    version: str
    integrity_hash: str
    license: str
    classification: str
    retention: str


@dataclass(frozen=True, slots=True)
class RegisterDatasetResult:
    dataset_ref_id: str


@dataclass(frozen=True, slots=True)
class RegisterExperimentCommand:
    project_id: str
    name: str
    owner_id: str
    version: str
    reproducibility: str = ReproducibilityLevel.R0_NARRATIVE.value
    dataset_ref_ids: tuple[str, ...] = ()
    environment_digest: str | None = None
    seed: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterExperimentResult:
    experiment_ref_id: str


@dataclass(frozen=True, slots=True)
class AiAssistedDraftCommand:
    document_id: str
    question: str
    package_id: str
    version: str = "1.0.0"
    top_k: int = 5
    data_classification: str = "public"


@dataclass(frozen=True, slots=True)
class AiAssistedDraftResult:
    document_id: str
    claim_id: str
    evidence_ids: tuple[str, ...]
    refused: bool
    trace_id: str


@dataclass(frozen=True, slots=True)
class ExportDocumentQuery:
    document_id: str


@dataclass(frozen=True, slots=True)
class ExportDocumentView:
    document_id: str
    document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PackageReproducibilityCommand:
    document_id: str
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PackageReproducibilityResult:
    package_id: str
    document_id: str
    package_hash: str
    total_claims: int
    traceable_claims: int
    limitations: tuple[LimitationEntry, ...]
    passed: bool
    package: ReproducibilityPackage
    report: ReviewReport


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


def _actor(invocation: object) -> Actor:
    context = getattr(invocation, "context", None)
    return context.actor


def _load_document(
    repo: ResearchRepositoryPort, *, organization_id: str, document_id: str
) -> ResearchDocument:
    document = repo.get_document(organization_id=organization_id, document_id=document_id)
    if document is None:
        raise ResearchNotFound("document", document_id)
    return document


def _load_project(
    repo: ResearchRepositoryPort, *, organization_id: str, project_id: str
) -> ResearchProject:
    project = repo.get_project(organization_id=organization_id, project_id=project_id)
    if project is None:
        raise ResearchNotFound("project", project_id)
    return project


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class CreateWorkspace:
    """``research.workspace.create`` — create a tenant-scoped research workspace (FR-RSH-001)."""

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> CreateWorkspaceResult:
        command = _typed(request, CreateWorkspaceCommand)
        organization_id = _tenant(request)
        workspace = Workspace(
            workspace_id=self._id_factory(),
            organization_id=organization_id,
            name=command.name,
            created_by=_subject(request),
            created_at=self._clock(),
        )
        self._repo.add_workspace(workspace)
        return CreateWorkspaceResult(
            workspace_id=workspace.workspace_id, organization_id=organization_id
        )


class CreateProject:
    """``research.project.create`` — create a project inside a workspace (FR-RSH-001)."""

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> CreateProjectResult:
        command = _typed(request, CreateProjectCommand)
        organization_id = _tenant(request)
        workspace = self._repo.get_workspace(
            organization_id=organization_id, workspace_id=command.workspace_id
        )
        if workspace is None:
            raise ResearchNotFound("workspace", command.workspace_id)
        subject = _subject(request)
        now = self._clock()
        project = ResearchProject(
            project_id=self._id_factory(),
            workspace_id=command.workspace_id,
            organization_id=organization_id,
            title=command.title,
            created_by=subject,
            created_at=now,
            research_question=command.research_question,
        )
        self._repo.add_project(project)
        # The creator is the project LEAD, so role-gated project actions are authorized from the
        # authenticated context out of the box (deny-by-default for everyone else, LAW-19).
        self._repo.set_membership(
            ProjectMembership(
                organization_id=organization_id,
                project_id=project.project_id,
                subject_id=subject,
                role=ContributorRole.LEAD,
                created_at=now,
            )
        )
        return CreateProjectResult(project_id=project.project_id, workspace_id=command.workspace_id)


class AuthorDocument:
    """``research.document.author`` — create a research document (typed blocks, FR-RSH-002)."""

    def __init__(self, *, repository: ResearchRepositoryPort, id_factory: IdFactory) -> None:
        self._repo = repository
        self._id_factory = id_factory

    def handle(self, request: object) -> AuthorDocumentResult:
        command = _typed(request, AuthorDocumentCommand)
        organization_id = _tenant(request)
        _load_project(self._repo, organization_id=organization_id, project_id=command.project_id)
        tree = ContentTree.from_document_blocks(list(command.blocks))
        document = ResearchDocument(
            document_id=self._id_factory(),
            organization_id=organization_id,
            project_id=command.project_id,
            title=command.title,
            tree=tree,
            status=DocumentStatus.DRAFT,
        )
        self._repo.add_document(document)
        return AuthorDocumentResult(
            document_id=document.document_id,
            project_id=command.project_id,
            status=document.status.value,
        )


class PublishDocument:
    """``research.document.publish`` — mint an IMMUTABLE research revision (FR-RSH-002/006)."""

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> PublishDocumentResult:
        command = _typed(request, PublishDocumentCommand)
        organization_id = _tenant(request)
        document = _load_document(
            self._repo, organization_id=organization_id, document_id=command.document_id
        )
        revision = new_revision(
            revision_id=self._id_factory(),
            document=document,
            title=command.title,
            tree=document.tree,
            created_by=_actor(request),
            created_at=self._clock(),
            parent_revision_id=document.latest_revision_id,
        )
        published = replace(
            document, status=DocumentStatus.PUBLISHED, latest_revision_id=revision.revision_id
        )
        self._repo.publish(organization_id=organization_id, document=published, revision=revision)
        return PublishDocumentResult(
            document_id=document.document_id,
            revision_id=revision.revision_id,
            parent_revision_id=revision.parent_revision_id,
            content_hash=revision.content_hash,
        )


class RegisterEvidence:
    """``research.evidence.register`` — record evidence with provenance + version identity."""

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> RegisterEvidenceResult:
        command = _typed(request, RegisterEvidenceCommand)
        organization_id = _tenant(request)
        _load_document(self._repo, organization_id=organization_id, document_id=command.document_id)
        evidence = _build_evidence(
            evidence_id=self._id_factory(),
            organization_id=organization_id,
            command=command,
            created_at=self._clock(),
        )
        self._repo.add_evidence(evidence)
        return RegisterEvidenceResult(
            evidence_id=evidence.evidence_id, version_hash=evidence.version_hash
        )


class AssertClaim:
    """``research.claim.assert`` — assert a claim over >=1 evidence record (FR-RSH-003).

    Deny-by-default: every referenced evidence id must resolve in the caller's tenant + document,
    and the :class:`Claim` constructor rejects zero evidence — an unevidenced claim is never stored.
    """

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> AssertClaimResult:
        command = _typed(request, AssertClaimCommand)
        organization_id = _tenant(request)
        _load_document(self._repo, organization_id=organization_id, document_id=command.document_id)
        for evidence_id in command.evidence_ids:
            evidence = self._repo.get_evidence(
                organization_id=organization_id, evidence_id=evidence_id
            )
            if evidence is None or evidence.document_id != command.document_id:
                raise ResearchNotFound("evidence", evidence_id)
        # Claim(...) raises ClaimWithoutEvidence for an empty evidence set (never persisted).
        claim = Claim(
            claim_id=self._id_factory(),
            organization_id=organization_id,
            document_id=command.document_id,
            statement=command.statement,
            evidence_ids=tuple(command.evidence_ids),
            created_by=_subject(request),
            created_at=self._clock(),
            confidence=command.confidence,
            generated=command.generated,
        )
        self._repo.add_claim(claim)
        return AssertClaimResult(claim_id=claim.claim_id, evidence_ids=claim.evidence_ids)


class RegisterDataset:
    """``research.dataset.register`` — record a dataset reference (ownership + version)."""

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> RegisterDatasetResult:
        command = _typed(request, RegisterDatasetCommand)
        organization_id = _tenant(request)
        _load_project(self._repo, organization_id=organization_id, project_id=command.project_id)
        dataset = DatasetRef(
            dataset_ref_id=self._id_factory(),
            organization_id=organization_id,
            project_id=command.project_id,
            name=command.name,
            owner_id=command.owner_id,
            version=command.version,
            integrity_hash=command.integrity_hash,
            license=command.license,
            classification=command.classification,
            retention=command.retention,
            created_at=self._clock(),
        )
        self._repo.add_dataset(dataset)
        return RegisterDatasetResult(dataset_ref_id=dataset.dataset_ref_id)


class RegisterExperiment:
    """``research.experiment.register`` — record an experiment/run reference (FR-RSH-004)."""

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> RegisterExperimentResult:
        command = _typed(request, RegisterExperimentCommand)
        organization_id = _tenant(request)
        _load_project(self._repo, organization_id=organization_id, project_id=command.project_id)
        experiment = ExperimentRef(
            experiment_ref_id=self._id_factory(),
            organization_id=organization_id,
            project_id=command.project_id,
            name=command.name,
            owner_id=command.owner_id,
            version=command.version,
            reproducibility=ReproducibilityLevel(command.reproducibility),
            created_at=self._clock(),
            dataset_ref_ids=tuple(command.dataset_ref_ids),
            environment_digest=command.environment_digest,
            seed=command.seed,
        )
        self._repo.add_experiment(experiment)
        return RegisterExperimentResult(experiment_ref_id=experiment.experiment_ref_id)


class AiAssistedDraft:
    """``research.document.ai-draft`` — reuse ``ai.answer`` and persist ONLY a grounded claim.

    Calls the one governed AI pipeline through :class:`AiDraftPort`; the citations it returns are
    already verified against retrieved evidence. Each verified citation becomes an evidence record
    with provenance + version identity, and a claim is asserted over them. A refused draft, or one
    whose citations all failed verification, yields no evidence — so :class:`Claim` rejects it and
    nothing is persisted (FR-RSH-005, EVAL-RSH-005, GATE-AI-GA).
    """

    def __init__(
        self,
        *,
        repository: ResearchRepositoryPort,
        ai_draft: AiDraftPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._ai = ai_draft
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> AiAssistedDraftResult:
        command = _typed(request, AiAssistedDraftCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        _load_document(self._repo, organization_id=organization_id, document_id=command.document_id)
        result = self._ai.draft(
            organization_id=organization_id,
            subject_id=subject_id,
            correlation_id=_correlation(request),
            question=command.question,
            package_id=command.package_id,
            version=command.version,
            top_k=command.top_k,
            data_classification=command.data_classification,
        )
        # A refused draft has no groundable claim; drop any citations so the claim is rejected.
        citations = () if result.refused else result.citations
        now = self._clock()
        evidence_records = tuple(
            EvidenceRecord(
                evidence_id=self._id_factory(),
                organization_id=organization_id,
                document_id=command.document_id,
                kind=EvidenceKind.RETRIEVED_PASSAGE,
                excerpt=citation.claim,
                version_hash=compute_version_hash(
                    excerpt=citation.claim,
                    object_id=citation.object_id,
                    revision_id=citation.revision_id,
                    block_id=citation.block_id,
                    chunk_id=citation.chunk_id,
                    source_uri=None,
                ),
                created_at=now,
                object_id=citation.object_id,
                revision_id=citation.revision_id,
                block_id=citation.block_id,
                chunk_id=citation.chunk_id,
                verified=True,
            )
            for citation in citations
        )
        # Build the claim FIRST (raises ClaimWithoutEvidence when there is no verified evidence),
        # so an uncited/fabricated/refused draft persists nothing.
        claim = Claim(
            claim_id=self._id_factory(),
            organization_id=organization_id,
            document_id=command.document_id,
            statement=result.answer,
            evidence_ids=tuple(e.evidence_id for e in evidence_records),
            created_by=subject_id,
            created_at=now,
            generated=True,
        )
        for evidence in evidence_records:
            self._repo.add_evidence(evidence)
        self._repo.add_claim(claim)
        return AiAssistedDraftResult(
            document_id=command.document_id,
            claim_id=claim.claim_id,
            evidence_ids=claim.evidence_ids,
            refused=result.refused,
            trace_id=result.trace_id,
        )


class ExportDocument:
    """``research.document.export`` (query) — canonical export preserving structure + citations."""

    def __init__(self, *, repository: ResearchRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ExportDocumentView:
        query = _typed(request, ExportDocumentQuery)
        organization_id = _tenant(request)
        document = _load_document(
            self._repo, organization_id=organization_id, document_id=query.document_id
        )
        revision = None
        if document.latest_revision_id is not None:
            revision = self._repo.get_revision(
                organization_id=organization_id, revision_id=document.latest_revision_id
            )
        tree = revision.tree if revision is not None else document.tree
        claims = tuple(
            self._repo.list_claims(
                organization_id=organization_id, document_id=document.document_id
            )
        )
        evidence = tuple(
            self._repo.list_evidence(
                organization_id=organization_id, document_id=document.document_id
            )
        )
        datasets = tuple(
            self._repo.list_datasets(
                organization_id=organization_id, project_id=document.project_id
            )
        )
        bundle = ResearchDocumentBundle(
            document_id=document.document_id,
            revision_id=document.latest_revision_id or document.document_id,
            title=revision.title if revision is not None else document.title,
            status=document.status,
            tree=tree,
            claims=claims,
            evidence=evidence,
            datasets=datasets,
            ai_contributions=tuple(c.claim_id for c in claims if c.generated),
        )
        return ExportDocumentView(
            document_id=document.document_id, document=to_research_document(bundle)
        )


class PackageReproducibility:
    """``research.reproducibility.package`` — assemble a deterministic reproducibility package.

    Loads the published document (tenant-scoped), all its claims + linked evidence, and the
    project's dataset/experiment (simulation/notebook) references, projects the canonical
    structure+citation document, then builds a content-addressed package with a deterministic
    package hash (canonical ordering + canonical JSON). Every claim must resolve to >=1 evidence in
    the package (reuses the invariant); a non-reproducible declared output is retained as an
    EXPLICIT limitation. The handler immediately runs the pure :func:`verify_package` review so the
    result
    carries the manifest-integrity + traceability verdict an independent reviewer would reach
    (EVAL-RES-001).
    """

    def __init__(self, *, repository: ResearchRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> PackageReproducibilityResult:
        command = _typed(request, PackageReproducibilityCommand)
        organization_id = _tenant(request)
        document = _load_document(
            self._repo, organization_id=organization_id, document_id=command.document_id
        )
        revision = None
        if document.latest_revision_id is not None:
            revision = self._repo.get_revision(
                organization_id=organization_id, revision_id=document.latest_revision_id
            )
        tree = revision.tree if revision is not None else document.tree
        content_hash = revision.content_hash if revision is not None else tree.content_hash()
        revision_id = document.latest_revision_id or document.document_id

        claims = tuple(
            self._repo.list_claims(
                organization_id=organization_id, document_id=document.document_id
            )
        )
        evidence = tuple(
            self._repo.list_evidence(
                organization_id=organization_id, document_id=document.document_id
            )
        )
        datasets = tuple(
            self._repo.list_datasets(
                organization_id=organization_id, project_id=document.project_id
            )
        )
        experiments = tuple(
            self._repo.list_experiments(
                organization_id=organization_id, project_id=document.project_id
            )
        )
        bundle = ResearchDocumentBundle(
            document_id=document.document_id,
            revision_id=revision_id,
            title=revision.title if revision is not None else document.title,
            status=document.status,
            tree=tree,
            claims=claims,
            evidence=evidence,
            datasets=datasets,
            ai_contributions=tuple(c.claim_id for c in claims if c.generated),
        )
        package = build_package(
            package_id=f"repro-{document.document_id}-{revision_id}",
            organization_id=organization_id,
            document=to_research_document(bundle),
            document_id=document.document_id,
            revision_id=revision_id,
            content_hash=content_hash,
            claims=claims,
            evidence=evidence,
            datasets=datasets,
            experiments=experiments,
            environment=dict(command.environment),
            generated_at=self._clock(),
        )
        report = verify_package(package)
        return PackageReproducibilityResult(
            package_id=package.package_id,
            document_id=document.document_id,
            package_hash=package.manifest.package_hash,
            total_claims=report.total_claims,
            traceable_claims=report.traceable_claims,
            limitations=report.limitations,
            passed=report.passed,
            package=package,
            report=report,
        )


# ---------------------------------------------------------------------------
# Project structure + peer review (EVAL-RSH-001 / EVAL-RSH-002)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UpdateProjectCommand:
    project_id: str
    title: str | None = None
    research_question: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateProjectResult:
    project_id: str


@dataclass(frozen=True, slots=True)
class AddQuestionCommand:
    project_id: str
    prompt: str


@dataclass(frozen=True, slots=True)
class AddQuestionResult:
    question_id: str


@dataclass(frozen=True, slots=True)
class AddHypothesisCommand:
    project_id: str
    question_id: str
    statement: str


@dataclass(frozen=True, slots=True)
class AddHypothesisResult:
    hypothesis_id: str
    question_id: str


@dataclass(frozen=True, slots=True)
class AddMethodCommand:
    project_id: str
    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class AddMethodResult:
    method_id: str


@dataclass(frozen=True, slots=True)
class AssignRoleCommand:
    project_id: str
    subject_id: str
    role: str


@dataclass(frozen=True, slots=True)
class AssignRoleResult:
    project_id: str
    subject_id: str
    role: str


@dataclass(frozen=True, slots=True)
class AddDocumentBlockCommand:
    document_id: str
    kind: str
    block_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    position: int = 0


@dataclass(frozen=True, slots=True)
class AddDocumentBlockResult:
    document_id: str
    block_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class LinkSimulationCommand:
    document_id: str
    simulation_id: str
    version: str


@dataclass(frozen=True, slots=True)
class LinkSimulationResult:
    document_id: str
    simulation_id: str
    version: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class OpenReviewCommand:
    document_id: str
    reviewers: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OpenReviewResult:
    review_id: str
    document_id: str
    status: str


@dataclass(frozen=True, slots=True)
class ReviewTransitionCommand:
    document_id: str
    action: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewTransitionResult:
    review_id: str
    document_id: str
    from_status: str
    to_status: str
    action: str


def _project_role(
    repo: ResearchRepositoryPort, *, organization_id: str, project_id: str, subject_id: str
) -> ContributorRole | None:
    return repo.get_membership_role(
        organization_id=organization_id, project_id=project_id, subject_id=subject_id
    )


class UpdateProject:
    """``research.project.update`` — update a project's title/question (role-gated: LEAD)."""

    def __init__(self, *, repository: ResearchRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> UpdateProjectResult:
        command = _typed(request, UpdateProjectCommand)
        organization_id = _tenant(request)
        subject = _subject(request)
        project = _load_project(
            self._repo, organization_id=organization_id, project_id=command.project_id
        )
        authorize_role(
            CAP_UPDATE_PROJECT,
            _project_role(
                self._repo,
                organization_id=organization_id,
                project_id=command.project_id,
                subject_id=subject,
            ),
        )
        updated = replace(
            project,
            title=command.title if command.title is not None else project.title,
            research_question=(
                command.research_question
                if command.research_question is not None
                else project.research_question
            ),
        )
        self._repo.update_project(updated)
        return UpdateProjectResult(project_id=updated.project_id)


class AddQuestion:
    """``research.project.add-question`` — add a research question (role-gated)."""

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> AddQuestionResult:
        command = _typed(request, AddQuestionCommand)
        organization_id = _tenant(request)
        subject = _subject(request)
        _load_project(self._repo, organization_id=organization_id, project_id=command.project_id)
        authorize_role(
            CAP_ADD_QUESTION,
            _project_role(
                self._repo,
                organization_id=organization_id,
                project_id=command.project_id,
                subject_id=subject,
            ),
        )
        question = ResearchQuestion(
            question_id=self._id_factory(),
            organization_id=organization_id,
            project_id=command.project_id,
            prompt=command.prompt,
            created_by=subject,
            created_at=self._clock(),
        )
        self._repo.add_question(question)
        return AddQuestionResult(question_id=question.question_id)


class AddHypothesis:
    """``research.project.add-hypothesis`` — add a hypothesis that MUST link to a question."""

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> AddHypothesisResult:
        command = _typed(request, AddHypothesisCommand)
        organization_id = _tenant(request)
        subject = _subject(request)
        _load_project(self._repo, organization_id=organization_id, project_id=command.project_id)
        authorize_role(
            CAP_ADD_HYPOTHESIS,
            _project_role(
                self._repo,
                organization_id=organization_id,
                project_id=command.project_id,
                subject_id=subject,
            ),
        )
        # Deny-by-default: the linked question must exist in this tenant + project.
        question = self._repo.get_question(
            organization_id=organization_id, question_id=command.question_id
        )
        if question is None or question.project_id != command.project_id:
            raise ResearchNotFound("question", command.question_id)
        # Hypothesis(...) raises HypothesisWithoutQuestion when question_id is empty.
        hypothesis = Hypothesis(
            hypothesis_id=self._id_factory(),
            organization_id=organization_id,
            project_id=command.project_id,
            question_id=command.question_id,
            statement=command.statement,
            created_by=subject,
            created_at=self._clock(),
        )
        self._repo.add_hypothesis(hypothesis)
        return AddHypothesisResult(
            hypothesis_id=hypothesis.hypothesis_id, question_id=command.question_id
        )


class AddMethod:
    """``research.project.add-method`` — add a method (role-gated)."""

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> AddMethodResult:
        command = _typed(request, AddMethodCommand)
        organization_id = _tenant(request)
        subject = _subject(request)
        _load_project(self._repo, organization_id=organization_id, project_id=command.project_id)
        authorize_role(
            CAP_ADD_METHOD,
            _project_role(
                self._repo,
                organization_id=organization_id,
                project_id=command.project_id,
                subject_id=subject,
            ),
        )
        method = ResearchMethod(
            method_id=self._id_factory(),
            organization_id=organization_id,
            project_id=command.project_id,
            name=command.name,
            description=command.description,
            created_by=subject,
            created_at=self._clock(),
        )
        self._repo.add_method(method)
        return AddMethodResult(method_id=method.method_id)


class AssignRole:
    """``research.project.assign-role`` — assign a contributor role (role-gated: LEAD only)."""

    def __init__(self, *, repository: ResearchRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> AssignRoleResult:
        command = _typed(request, AssignRoleCommand)
        organization_id = _tenant(request)
        subject = _subject(request)
        _load_project(self._repo, organization_id=organization_id, project_id=command.project_id)
        authorize_role(
            CAP_ASSIGN_ROLE,
            _project_role(
                self._repo,
                organization_id=organization_id,
                project_id=command.project_id,
                subject_id=subject,
            ),
        )
        role = ContributorRole(command.role)
        self._repo.set_membership(
            ProjectMembership(
                organization_id=organization_id,
                project_id=command.project_id,
                subject_id=command.subject_id,
                role=role,
                created_at=self._clock(),
            )
        )
        return AssignRoleResult(
            project_id=command.project_id, subject_id=command.subject_id, role=role.value
        )


class AddDocumentBlock:
    """``research.document.add-block`` — attach a figure/table/lit-review block (role-gated)."""

    def __init__(self, *, repository: ResearchRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> AddDocumentBlockResult:
        command = _typed(request, AddDocumentBlockCommand)
        organization_id = _tenant(request)
        subject = _subject(request)
        document = _load_document(
            self._repo, organization_id=organization_id, document_id=command.document_id
        )
        authorize_role(
            CAP_ADD_DOCUMENT_BLOCK,
            _project_role(
                self._repo,
                organization_id=organization_id,
                project_id=document.project_id,
                subject_id=subject,
            ),
        )
        # build_research_block validates the typed block (deny-by-default on unknown kind/payload).
        block = build_research_block(
            command.kind, block_id=command.block_id, payload=command.payload
        )
        stored = StoredDocumentBlock(
            block_id=command.block_id,
            organization_id=organization_id,
            document_id=command.document_id,
            kind=command.kind,
            position=command.position,
            payload=block.to_document_block(),
            created_at=self._clock(),
        )
        self._repo.add_document_block(stored)
        return AddDocumentBlockResult(
            document_id=command.document_id, block_id=command.block_id, kind=command.kind
        )


class LinkSimulation:
    """``research.document.link-simulation`` — link a simulation by IDENTITY via a port.

    Research never reaches the simulation module's tables: it resolves the simulation's identity
    through :class:`SimulationRefPort` and records only that identity. A simulation the port cannot
    resolve is refused (deny-by-default, :class:`SimulationNotResolved`).
    """

    def __init__(
        self, *, repository: ResearchRepositoryPort, simulations: SimulationRefPort, clock: Clock
    ) -> None:
        self._repo = repository
        self._simulations = simulations
        self._clock = clock

    def handle(self, request: object) -> LinkSimulationResult:
        command = _typed(request, LinkSimulationCommand)
        organization_id = _tenant(request)
        subject = _subject(request)
        document = _load_document(
            self._repo, organization_id=organization_id, document_id=command.document_id
        )
        authorize_role(
            CAP_LINK_SIMULATION,
            _project_role(
                self._repo,
                organization_id=organization_id,
                project_id=document.project_id,
                subject_id=subject,
            ),
        )
        ref = self._simulations.resolve(
            organization_id=organization_id,
            simulation_id=command.simulation_id,
            version=command.version,
        )
        if ref is None:
            raise SimulationNotResolved(command.simulation_id, command.version)
        link = SimulationLink(
            document_id=command.document_id,
            organization_id=organization_id,
            simulation_id=ref.simulation_id,
            version=ref.version,
            content_hash=ref.content_hash,
            linked_by=subject,
            linked_at=self._clock(),
        )
        self._repo.set_simulation_link(link)
        return LinkSimulationResult(
            document_id=command.document_id,
            simulation_id=ref.simulation_id,
            version=ref.version,
            content_hash=ref.content_hash,
        )


class OpenReview:
    """``research.document.open-review`` — open a peer review in DRAFT (role-gated)."""

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> OpenReviewResult:
        command = _typed(request, OpenReviewCommand)
        organization_id = _tenant(request)
        subject = _subject(request)
        document = _load_document(
            self._repo, organization_id=organization_id, document_id=command.document_id
        )
        authorize_role(
            CAP_OPEN_REVIEW,
            _project_role(
                self._repo,
                organization_id=organization_id,
                project_id=document.project_id,
                subject_id=subject,
            ),
        )
        # The opener is always an author; explicit authors are merged (dedup, order-preserving).
        authors = tuple(dict.fromkeys((subject, *command.authors)))
        now = self._clock()
        review = DocumentReview(
            review_id=self._id_factory(),
            organization_id=organization_id,
            document_id=command.document_id,
            status=ReviewStatus.DRAFT,
            authors=authors,
            reviewers=tuple(dict.fromkeys(command.reviewers)),
            created_at=now,
            updated_at=now,
        )
        self._repo.add_review(review)
        return OpenReviewResult(
            review_id=review.review_id,
            document_id=command.document_id,
            status=review.status.value,
        )


class TransitionReview:
    """``research.document.review-transition`` — the deterministic, audited peer-review machine.

    The transition is validated by the pure state machine (:func:`next_status`); the actor is
    checked deny-by-default (:func:`authorize_actor`: authors submit/resubmit, assigned reviewers
    review); the review is updated and an immutable :class:`ReviewEvent` records it (LAW-14).
    """

    def __init__(
        self, *, repository: ResearchRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> ReviewTransitionResult:
        command = _typed(request, ReviewTransitionCommand)
        organization_id = _tenant(request)
        subject = _subject(request)
        review = self._repo.get_review(
            organization_id=organization_id, document_id=command.document_id
        )
        if review is None:
            raise ResearchNotFound("review", command.document_id)
        action = ReviewAction(command.action)
        # Deny-by-default actor authorization first (only authors submit/resubmit; only assigned
        # reviewers review), then the deterministic state machine rejects any illegal transition.
        authorize_actor(
            action,
            subject_id=subject,
            authors=review.authors,
            reviewers=review.reviewers,
        )
        target = next_status(review.status, action)
        now = self._clock()
        updated = replace(review, status=target, updated_at=now)
        self._repo.update_review(updated)
        self._repo.add_review_event(
            ReviewEvent(
                event_id=self._id_factory(),
                organization_id=organization_id,
                review_id=review.review_id,
                from_status=review.status,
                to_status=target,
                action=action,
                actor=subject,
                occurred_at=now,
                note=command.note,
            )
        )
        return ReviewTransitionResult(
            review_id=review.review_id,
            document_id=command.document_id,
            from_status=review.status.value,
            to_status=target.value,
            action=action.value,
        )


def _build_evidence(
    *,
    evidence_id: str,
    organization_id: str,
    command: RegisterEvidenceCommand,
    created_at: datetime,
) -> EvidenceRecord:
    version_hash = compute_version_hash(
        excerpt=command.excerpt,
        object_id=command.object_id,
        revision_id=command.revision_id,
        block_id=command.block_id,
        chunk_id=command.chunk_id,
        source_uri=command.source_uri,
    )
    return EvidenceRecord(
        evidence_id=evidence_id,
        organization_id=organization_id,
        document_id=command.document_id,
        kind=EvidenceKind(command.kind),
        excerpt=command.excerpt,
        version_hash=version_hash,
        created_at=created_at,
        object_id=command.object_id,
        revision_id=command.revision_id,
        block_id=command.block_id,
        chunk_id=command.chunk_id,
        source_uri=command.source_uri,
        verified=command.verified,
    )
