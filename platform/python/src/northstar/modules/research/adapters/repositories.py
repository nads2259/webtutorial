"""Research repositories (in-memory + SQLAlchemy) implementing :class:`ResearchRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL RLS applies as defense-in-depth.
:meth:`SqlAlchemyResearchRepository.publish` writes the immutable revision and updates the document
pointer in a SINGLE unit of work; re-publishing an existing ``revision_id`` is rejected (LAW-07).
No string interpolation of values.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from northstar.kernel.context import Actor, ActorType
from northstar.modules.knowledge.domain.blocks import ContentTree

from ..domain.errors import ImmutableRevisionError
from ..domain.model import (
    Claim,
    DatasetRef,
    DocumentStatus,
    EvidenceKind,
    EvidenceRecord,
    ExperimentRef,
    ReproducibilityLevel,
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
from ..domain.review import DocumentReview, ReviewAction, ReviewEvent, ReviewStatus
from .tables import ResearchTables


def _actor_ref(actor: Actor) -> dict[str, str | None]:
    return {"type": actor.type.value, "id": actor.id, "delegated_by": actor.delegated_by}


def _actor_from_ref(ref: dict[str, Any]) -> Actor:
    return Actor(type=ActorType(ref["type"]), id=ref["id"], delegated_by=ref.get("delegated_by"))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class InMemoryResearchRepository:
    """In-memory repository for fast, deterministic unit tests (tenant-scoped like the DB one)."""

    def __init__(self) -> None:
        self._workspaces: dict[str, Workspace] = {}
        self._projects: dict[str, ResearchProject] = {}
        self._documents: dict[str, ResearchDocument] = {}
        self._revisions: dict[str, ResearchRevision] = {}
        self._evidence: dict[str, EvidenceRecord] = {}
        self._claims: dict[str, Claim] = {}
        self._datasets: dict[str, DatasetRef] = {}
        self._experiments: dict[str, ExperimentRef] = {}
        self._questions: dict[str, ResearchQuestion] = {}
        self._hypotheses: dict[str, Hypothesis] = {}
        self._methods: dict[str, ResearchMethod] = {}
        self._memberships: dict[tuple[str, str], ProjectMembership] = {}
        self._document_blocks: dict[str, StoredDocumentBlock] = {}
        self._simulation_links: dict[str, SimulationLink] = {}
        self._reviews: dict[str, DocumentReview] = {}
        self._reviews_by_document: dict[str, str] = {}
        self._review_events: dict[str, ReviewEvent] = {}

    def add_workspace(self, workspace: Workspace) -> None:
        self._workspaces[workspace.workspace_id] = workspace

    def get_workspace(self, *, organization_id: str, workspace_id: str) -> Workspace | None:
        ws = self._workspaces.get(workspace_id)
        return ws if ws is not None and ws.organization_id == organization_id else None

    def add_project(self, project: ResearchProject) -> None:
        self._projects[project.project_id] = project

    def get_project(self, *, organization_id: str, project_id: str) -> ResearchProject | None:
        p = self._projects.get(project_id)
        return p if p is not None and p.organization_id == organization_id else None

    def add_document(self, document: ResearchDocument) -> None:
        self._documents[document.document_id] = document

    def get_document(self, *, organization_id: str, document_id: str) -> ResearchDocument | None:
        d = self._documents.get(document_id)
        return d if d is not None and d.organization_id == organization_id else None

    def publish(
        self, *, organization_id: str, document: ResearchDocument, revision: ResearchRevision
    ) -> None:
        if revision.revision_id in self._revisions:
            raise ImmutableRevisionError(revision.revision_id)
        self._documents[document.document_id] = document
        self._revisions[revision.revision_id] = revision

    def get_revision(self, *, organization_id: str, revision_id: str) -> ResearchRevision | None:
        rev = self._revisions.get(revision_id)
        return rev if rev is not None and rev.organization_id == organization_id else None

    def add_evidence(self, evidence: EvidenceRecord) -> None:
        self._evidence[evidence.evidence_id] = evidence

    def get_evidence(self, *, organization_id: str, evidence_id: str) -> EvidenceRecord | None:
        e = self._evidence.get(evidence_id)
        return e if e is not None and e.organization_id == organization_id else None

    def list_evidence(self, *, organization_id: str, document_id: str) -> Sequence[EvidenceRecord]:
        return [
            e
            for e in self._evidence.values()
            if e.organization_id == organization_id and e.document_id == document_id
        ]

    def add_claim(self, claim: Claim) -> None:
        self._claims[claim.claim_id] = claim

    def list_claims(self, *, organization_id: str, document_id: str) -> Sequence[Claim]:
        return [
            c
            for c in self._claims.values()
            if c.organization_id == organization_id and c.document_id == document_id
        ]

    def add_dataset(self, dataset: DatasetRef) -> None:
        self._datasets[dataset.dataset_ref_id] = dataset

    def list_datasets(self, *, organization_id: str, project_id: str) -> Sequence[DatasetRef]:
        return [
            d
            for d in self._datasets.values()
            if d.organization_id == organization_id and d.project_id == project_id
        ]

    def add_experiment(self, experiment: ExperimentRef) -> None:
        self._experiments[experiment.experiment_ref_id] = experiment

    def list_experiments(self, *, organization_id: str, project_id: str) -> Sequence[ExperimentRef]:
        return [
            x
            for x in self._experiments.values()
            if x.organization_id == organization_id and x.project_id == project_id
        ]

    # -- project structure ------------------------------------------------

    def update_project(self, project: ResearchProject) -> None:
        self._projects[project.project_id] = project

    def add_question(self, question: ResearchQuestion) -> None:
        self._questions[question.question_id] = question

    def get_question(self, *, organization_id: str, question_id: str) -> ResearchQuestion | None:
        q = self._questions.get(question_id)
        return q if q is not None and q.organization_id == organization_id else None

    def list_questions(
        self, *, organization_id: str, project_id: str
    ) -> Sequence[ResearchQuestion]:
        return [
            q
            for q in self._questions.values()
            if q.organization_id == organization_id and q.project_id == project_id
        ]

    def add_hypothesis(self, hypothesis: Hypothesis) -> None:
        self._hypotheses[hypothesis.hypothesis_id] = hypothesis

    def list_hypotheses(self, *, organization_id: str, project_id: str) -> Sequence[Hypothesis]:
        return [
            h
            for h in self._hypotheses.values()
            if h.organization_id == organization_id and h.project_id == project_id
        ]

    def add_method(self, method: ResearchMethod) -> None:
        self._methods[method.method_id] = method

    def list_methods(self, *, organization_id: str, project_id: str) -> Sequence[ResearchMethod]:
        return [
            m
            for m in self._methods.values()
            if m.organization_id == organization_id and m.project_id == project_id
        ]

    def set_membership(self, membership: ProjectMembership) -> None:
        self._memberships[(membership.project_id, membership.subject_id)] = membership

    def get_membership_role(
        self, *, organization_id: str, project_id: str, subject_id: str
    ) -> ContributorRole | None:
        member = self._memberships.get((project_id, subject_id))
        if member is None or member.organization_id != organization_id:
            return None
        return member.role

    def list_memberships(
        self, *, organization_id: str, project_id: str
    ) -> Sequence[ProjectMembership]:
        return [
            m
            for m in self._memberships.values()
            if m.organization_id == organization_id and m.project_id == project_id
        ]

    # -- document structure -----------------------------------------------

    def add_document_block(self, block: StoredDocumentBlock) -> None:
        self._document_blocks[block.block_id] = block

    def list_document_blocks(
        self, *, organization_id: str, document_id: str
    ) -> Sequence[StoredDocumentBlock]:
        return sorted(
            (
                b
                for b in self._document_blocks.values()
                if b.organization_id == organization_id and b.document_id == document_id
            ),
            key=lambda b: (b.position, b.block_id),
        )

    def set_simulation_link(self, link: SimulationLink) -> None:
        self._simulation_links[link.document_id] = link

    def get_simulation_link(
        self, *, organization_id: str, document_id: str
    ) -> SimulationLink | None:
        link = self._simulation_links.get(document_id)
        return link if link is not None and link.organization_id == organization_id else None

    # -- peer review ------------------------------------------------------

    def add_review(self, review: DocumentReview) -> None:
        self._reviews[review.review_id] = review
        self._reviews_by_document[review.document_id] = review.review_id

    def get_review(self, *, organization_id: str, document_id: str) -> DocumentReview | None:
        review_id = self._reviews_by_document.get(document_id)
        if review_id is None:
            return None
        review = self._reviews.get(review_id)
        return review if review is not None and review.organization_id == organization_id else None

    def update_review(self, review: DocumentReview) -> None:
        self._reviews[review.review_id] = review

    def add_review_event(self, event: ReviewEvent) -> None:
        self._review_events[event.event_id] = event

    def list_review_events(self, *, organization_id: str, review_id: str) -> Sequence[ReviewEvent]:
        return sorted(
            (
                e
                for e in self._review_events.values()
                if e.organization_id == organization_id and e.review_id == review_id
            ),
            key=lambda e: (e.occurred_at, e.event_id),
        )


class SqlAlchemyResearchRepository:
    """PostgreSQL repository; every query filters by ``organization_id`` and sets the tenant GUC."""

    def __init__(self, *, session_factory: sessionmaker[SaSession], tables: ResearchTables) -> None:
        self._session_factory = session_factory
        self._tables = tables

    # -- workspace / project ----------------------------------------------

    def add_workspace(self, workspace: Workspace) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, workspace.organization_id)
            uow.session.execute(
                insert(self._tables.workspace).values(
                    workspace_id=workspace.workspace_id,
                    organization_id=workspace.organization_id,
                    name=workspace.name,
                    created_by=workspace.created_by,
                    created_at=workspace.created_at,
                )
            )
            uow.commit()

    def get_workspace(self, *, organization_id: str, workspace_id: str) -> Workspace | None:
        table = self._tables.workspace
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.workspace_id == workspace_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return Workspace(
            workspace_id=row.workspace_id,
            organization_id=row.organization_id,
            name=row.name,
            created_by=row.created_by,
            created_at=_aware(row.created_at),
        )

    def add_project(self, project: ResearchProject) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, project.organization_id)
            uow.session.execute(
                insert(self._tables.research_project).values(
                    project_id=project.project_id,
                    organization_id=project.organization_id,
                    workspace_id=project.workspace_id,
                    title=project.title,
                    research_question=project.research_question,
                    created_by=project.created_by,
                    created_at=project.created_at,
                )
            )
            uow.commit()

    def get_project(self, *, organization_id: str, project_id: str) -> ResearchProject | None:
        table = self._tables.research_project
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.project_id == project_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return ResearchProject(
            project_id=row.project_id,
            workspace_id=row.workspace_id,
            organization_id=row.organization_id,
            title=row.title,
            created_by=row.created_by,
            created_at=_aware(row.created_at),
            research_question=row.research_question,
        )

    # -- document / revision ----------------------------------------------

    def add_document(self, document: ResearchDocument) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, document.organization_id)
            uow.session.execute(
                insert(self._tables.research_document).values(
                    document_id=document.document_id,
                    organization_id=document.organization_id,
                    project_id=document.project_id,
                    title=document.title,
                    status=document.status.value,
                    content_tree=document.tree.to_document_blocks(),
                    latest_revision_id=document.latest_revision_id,
                )
            )
            uow.commit()

    def get_document(self, *, organization_id: str, document_id: str) -> ResearchDocument | None:
        table = self._tables.research_document
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.document_id == document_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return ResearchDocument(
            document_id=row.document_id,
            organization_id=row.organization_id,
            project_id=row.project_id,
            title=row.title,
            tree=ContentTree.from_document_blocks(row.content_tree),
            status=DocumentStatus(row.status),
            latest_revision_id=row.latest_revision_id,
        )

    def publish(
        self, *, organization_id: str, document: ResearchDocument, revision: ResearchRevision
    ) -> None:
        tables = self._tables
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(tables.research_revision.c.revision_id).where(
                    tables.research_revision.c.revision_id == revision.revision_id
                )
            ).first()
            if existing is not None:
                raise ImmutableRevisionError(revision.revision_id)
            session.execute(
                insert(tables.research_revision).values(
                    revision_id=revision.revision_id,
                    organization_id=organization_id,
                    document_id=revision.document_id,
                    parent_revision_id=revision.parent_revision_id,
                    title=revision.title,
                    status=revision.status.value,
                    content_tree=revision.tree.to_document_blocks(),
                    content_hash=revision.content_hash,
                    created_by=_actor_ref(revision.created_by),
                    created_at=revision.created_at,
                )
            )
            session.execute(
                update(tables.research_document)
                .where(
                    tables.research_document.c.document_id == document.document_id,
                    tables.research_document.c.organization_id == organization_id,
                )
                .values(
                    status=document.status.value,
                    latest_revision_id=document.latest_revision_id,
                )
            )
            uow.commit()

    def get_revision(self, *, organization_id: str, revision_id: str) -> ResearchRevision | None:
        table = self._tables.research_revision
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.revision_id == revision_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return ResearchRevision(
            revision_id=row.revision_id,
            document_id=row.document_id,
            organization_id=row.organization_id,
            title=row.title,
            tree=ContentTree.from_document_blocks(row.content_tree),
            content_hash=row.content_hash,
            created_by=_actor_from_ref(row.created_by),
            created_at=_aware(row.created_at),
            parent_revision_id=row.parent_revision_id,
            status=DocumentStatus(row.status),
        )

    # -- evidence / claim -------------------------------------------------

    def add_evidence(self, evidence: EvidenceRecord) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, evidence.organization_id)
            uow.session.execute(
                insert(self._tables.evidence_record).values(
                    evidence_id=evidence.evidence_id,
                    organization_id=evidence.organization_id,
                    document_id=evidence.document_id,
                    kind=evidence.kind.value,
                    excerpt=evidence.excerpt,
                    version_hash=evidence.version_hash,
                    object_id=evidence.object_id,
                    revision_id=evidence.revision_id,
                    block_id=evidence.block_id,
                    chunk_id=evidence.chunk_id,
                    source_uri=evidence.source_uri,
                    verified=evidence.verified,
                    created_at=evidence.created_at,
                )
            )
            uow.commit()

    def get_evidence(self, *, organization_id: str, evidence_id: str) -> EvidenceRecord | None:
        table = self._tables.evidence_record
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.evidence_id == evidence_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        return _evidence_from_row(row) if row is not None else None

    def list_evidence(self, *, organization_id: str, document_id: str) -> Sequence[EvidenceRecord]:
        table = self._tables.evidence_record
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.document_id == document_id,
                )
                .order_by(table.c.created_at, table.c.evidence_id)
            ).all()
        return [_evidence_from_row(row) for row in rows]

    def add_claim(self, claim: Claim) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, claim.organization_id)
            uow.session.execute(
                insert(self._tables.claim).values(
                    claim_id=claim.claim_id,
                    organization_id=claim.organization_id,
                    document_id=claim.document_id,
                    statement=claim.statement,
                    evidence_ids=list(claim.evidence_ids),
                    confidence=claim.confidence,
                    generated=claim.generated,
                    created_by=claim.created_by,
                    created_at=claim.created_at,
                )
            )
            uow.commit()

    def list_claims(self, *, organization_id: str, document_id: str) -> Sequence[Claim]:
        table = self._tables.claim
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.document_id == document_id,
                )
                .order_by(table.c.created_at, table.c.claim_id)
            ).all()
        return [_claim_from_row(row) for row in rows]

    # -- dataset / experiment --------------------------------------------

    def add_dataset(self, dataset: DatasetRef) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, dataset.organization_id)
            uow.session.execute(
                insert(self._tables.dataset_ref).values(
                    dataset_ref_id=dataset.dataset_ref_id,
                    organization_id=dataset.organization_id,
                    project_id=dataset.project_id,
                    name=dataset.name,
                    owner_id=dataset.owner_id,
                    version=dataset.version,
                    integrity_hash=dataset.integrity_hash,
                    license=dataset.license,
                    classification=dataset.classification,
                    retention=dataset.retention,
                    created_at=dataset.created_at,
                )
            )
            uow.commit()

    def list_datasets(self, *, organization_id: str, project_id: str) -> Sequence[DatasetRef]:
        table = self._tables.dataset_ref
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.project_id == project_id,
                )
                .order_by(table.c.created_at, table.c.dataset_ref_id)
            ).all()
        return [
            DatasetRef(
                dataset_ref_id=row.dataset_ref_id,
                organization_id=row.organization_id,
                project_id=row.project_id,
                name=row.name,
                owner_id=row.owner_id,
                version=row.version,
                integrity_hash=row.integrity_hash,
                license=row.license,
                classification=row.classification,
                retention=row.retention,
                created_at=_aware(row.created_at),
            )
            for row in rows
        ]

    def add_experiment(self, experiment: ExperimentRef) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, experiment.organization_id)
            uow.session.execute(
                insert(self._tables.experiment_ref).values(
                    experiment_ref_id=experiment.experiment_ref_id,
                    organization_id=experiment.organization_id,
                    project_id=experiment.project_id,
                    name=experiment.name,
                    owner_id=experiment.owner_id,
                    version=experiment.version,
                    reproducibility=experiment.reproducibility.value,
                    dataset_ref_ids=list(experiment.dataset_ref_ids),
                    environment_digest=experiment.environment_digest,
                    seed=experiment.seed,
                    created_at=experiment.created_at,
                )
            )
            uow.commit()

    def list_experiments(self, *, organization_id: str, project_id: str) -> Sequence[ExperimentRef]:
        table = self._tables.experiment_ref
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.project_id == project_id,
                )
                .order_by(table.c.created_at, table.c.experiment_ref_id)
            ).all()
        return [
            ExperimentRef(
                experiment_ref_id=row.experiment_ref_id,
                organization_id=row.organization_id,
                project_id=row.project_id,
                name=row.name,
                owner_id=row.owner_id,
                version=row.version,
                reproducibility=ReproducibilityLevel(row.reproducibility),
                created_at=_aware(row.created_at),
                dataset_ref_ids=tuple(row.dataset_ref_ids or ()),
                environment_digest=row.environment_digest,
                seed=row.seed,
            )
            for row in rows
        ]

    # -- project structure ------------------------------------------------

    def update_project(self, project: ResearchProject) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, project.organization_id)
            uow.session.execute(
                update(self._tables.research_project)
                .where(
                    self._tables.research_project.c.project_id == project.project_id,
                    self._tables.research_project.c.organization_id == project.organization_id,
                )
                .values(title=project.title, research_question=project.research_question)
            )
            uow.commit()

    def add_question(self, question: ResearchQuestion) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, question.organization_id)
            uow.session.execute(
                insert(self._tables.research_question).values(
                    question_id=question.question_id,
                    organization_id=question.organization_id,
                    project_id=question.project_id,
                    prompt=question.prompt,
                    created_by=question.created_by,
                    created_at=question.created_at,
                )
            )
            uow.commit()

    def get_question(self, *, organization_id: str, question_id: str) -> ResearchQuestion | None:
        table = self._tables.research_question
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.question_id == question_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return ResearchQuestion(
            question_id=row.question_id,
            organization_id=row.organization_id,
            project_id=row.project_id,
            prompt=row.prompt,
            created_by=row.created_by,
            created_at=_aware(row.created_at),
        )

    def list_questions(
        self, *, organization_id: str, project_id: str
    ) -> Sequence[ResearchQuestion]:
        table = self._tables.research_question
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.project_id == project_id,
                )
                .order_by(table.c.created_at, table.c.question_id)
            ).all()
        return [
            ResearchQuestion(
                question_id=row.question_id,
                organization_id=row.organization_id,
                project_id=row.project_id,
                prompt=row.prompt,
                created_by=row.created_by,
                created_at=_aware(row.created_at),
            )
            for row in rows
        ]

    def add_hypothesis(self, hypothesis: Hypothesis) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, hypothesis.organization_id)
            uow.session.execute(
                insert(self._tables.hypothesis).values(
                    hypothesis_id=hypothesis.hypothesis_id,
                    organization_id=hypothesis.organization_id,
                    project_id=hypothesis.project_id,
                    question_id=hypothesis.question_id,
                    statement=hypothesis.statement,
                    created_by=hypothesis.created_by,
                    created_at=hypothesis.created_at,
                )
            )
            uow.commit()

    def list_hypotheses(self, *, organization_id: str, project_id: str) -> Sequence[Hypothesis]:
        table = self._tables.hypothesis
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.project_id == project_id,
                )
                .order_by(table.c.created_at, table.c.hypothesis_id)
            ).all()
        return [
            Hypothesis(
                hypothesis_id=row.hypothesis_id,
                organization_id=row.organization_id,
                project_id=row.project_id,
                question_id=row.question_id,
                statement=row.statement,
                created_by=row.created_by,
                created_at=_aware(row.created_at),
            )
            for row in rows
        ]

    def add_method(self, method: ResearchMethod) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, method.organization_id)
            uow.session.execute(
                insert(self._tables.research_method).values(
                    method_id=method.method_id,
                    organization_id=method.organization_id,
                    project_id=method.project_id,
                    name=method.name,
                    description=method.description,
                    created_by=method.created_by,
                    created_at=method.created_at,
                )
            )
            uow.commit()

    def list_methods(self, *, organization_id: str, project_id: str) -> Sequence[ResearchMethod]:
        table = self._tables.research_method
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.project_id == project_id,
                )
                .order_by(table.c.created_at, table.c.method_id)
            ).all()
        return [
            ResearchMethod(
                method_id=row.method_id,
                organization_id=row.organization_id,
                project_id=row.project_id,
                name=row.name,
                description=row.description,
                created_by=row.created_by,
                created_at=_aware(row.created_at),
            )
            for row in rows
        ]

    def set_membership(self, membership: ProjectMembership) -> None:
        table = self._tables.project_membership
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, membership.organization_id)
            existing = session.execute(
                select(table.c.subject_id).where(
                    table.c.project_id == membership.project_id,
                    table.c.subject_id == membership.subject_id,
                    table.c.organization_id == membership.organization_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=membership.organization_id,
                        project_id=membership.project_id,
                        subject_id=membership.subject_id,
                        role=membership.role.value,
                        created_at=membership.created_at,
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.project_id == membership.project_id,
                        table.c.subject_id == membership.subject_id,
                        table.c.organization_id == membership.organization_id,
                    )
                    .values(role=membership.role.value)
                )
            uow.commit()

    def get_membership_role(
        self, *, organization_id: str, project_id: str, subject_id: str
    ) -> ContributorRole | None:
        table = self._tables.project_membership
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table.c.role).where(
                    table.c.project_id == project_id,
                    table.c.subject_id == subject_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        return ContributorRole(row.role) if row is not None else None

    def list_memberships(
        self, *, organization_id: str, project_id: str
    ) -> Sequence[ProjectMembership]:
        table = self._tables.project_membership
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.project_id == project_id,
                )
                .order_by(table.c.created_at, table.c.subject_id)
            ).all()
        return [
            ProjectMembership(
                organization_id=row.organization_id,
                project_id=row.project_id,
                subject_id=row.subject_id,
                role=ContributorRole(row.role),
                created_at=_aware(row.created_at),
            )
            for row in rows
        ]

    # -- document structure -----------------------------------------------

    def add_document_block(self, block: StoredDocumentBlock) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, block.organization_id)
            uow.session.execute(
                insert(self._tables.document_block).values(
                    block_id=block.block_id,
                    organization_id=block.organization_id,
                    document_id=block.document_id,
                    kind=block.kind,
                    position=block.position,
                    payload=block.payload,
                    created_at=block.created_at,
                )
            )
            uow.commit()

    def list_document_blocks(
        self, *, organization_id: str, document_id: str
    ) -> Sequence[StoredDocumentBlock]:
        table = self._tables.document_block
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.document_id == document_id,
                )
                .order_by(table.c.position, table.c.block_id)
            ).all()
        return [
            StoredDocumentBlock(
                block_id=row.block_id,
                organization_id=row.organization_id,
                document_id=row.document_id,
                kind=row.kind,
                position=row.position,
                payload=dict(row.payload),
                created_at=_aware(row.created_at),
            )
            for row in rows
        ]

    def set_simulation_link(self, link: SimulationLink) -> None:
        table = self._tables.document_simulation_link
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, link.organization_id)
            existing = session.execute(
                select(table.c.document_id).where(
                    table.c.document_id == link.document_id,
                    table.c.organization_id == link.organization_id,
                )
            ).first()
            values = {
                "simulation_id": link.simulation_id,
                "version": link.version,
                "content_hash": link.content_hash,
                "linked_by": link.linked_by,
                "linked_at": link.linked_at,
            }
            if existing is None:
                session.execute(
                    insert(table).values(
                        document_id=link.document_id,
                        organization_id=link.organization_id,
                        **values,
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.document_id == link.document_id,
                        table.c.organization_id == link.organization_id,
                    )
                    .values(**values)
                )
            uow.commit()

    def get_simulation_link(
        self, *, organization_id: str, document_id: str
    ) -> SimulationLink | None:
        table = self._tables.document_simulation_link
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.document_id == document_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return SimulationLink(
            document_id=row.document_id,
            organization_id=row.organization_id,
            simulation_id=row.simulation_id,
            version=row.version,
            content_hash=row.content_hash,
            linked_by=row.linked_by,
            linked_at=_aware(row.linked_at),
        )

    # -- peer review ------------------------------------------------------

    def add_review(self, review: DocumentReview) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, review.organization_id)
            uow.session.execute(
                insert(self._tables.document_review).values(
                    review_id=review.review_id,
                    organization_id=review.organization_id,
                    document_id=review.document_id,
                    status=review.status.value,
                    authors=list(review.authors),
                    reviewers=list(review.reviewers),
                    created_at=review.created_at,
                    updated_at=review.updated_at,
                )
            )
            uow.commit()

    def get_review(self, *, organization_id: str, document_id: str) -> DocumentReview | None:
        table = self._tables.document_review
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.document_id == document_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return DocumentReview(
            review_id=row.review_id,
            organization_id=row.organization_id,
            document_id=row.document_id,
            status=ReviewStatus(row.status),
            authors=tuple(row.authors or ()),
            reviewers=tuple(row.reviewers or ()),
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
        )

    def update_review(self, review: DocumentReview) -> None:
        table = self._tables.document_review
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, review.organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.review_id == review.review_id,
                    table.c.organization_id == review.organization_id,
                )
                .values(
                    status=review.status.value,
                    authors=list(review.authors),
                    reviewers=list(review.reviewers),
                    updated_at=review.updated_at,
                )
            )
            uow.commit()

    def add_review_event(self, event: ReviewEvent) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, event.organization_id)
            uow.session.execute(
                insert(self._tables.review_event).values(
                    event_id=event.event_id,
                    organization_id=event.organization_id,
                    review_id=event.review_id,
                    from_status=event.from_status.value,
                    to_status=event.to_status.value,
                    action=event.action.value,
                    actor=event.actor,
                    note=event.note,
                    occurred_at=event.occurred_at,
                )
            )
            uow.commit()

    def list_review_events(self, *, organization_id: str, review_id: str) -> Sequence[ReviewEvent]:
        table = self._tables.review_event
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.review_id == review_id,
                )
                .order_by(table.c.occurred_at, table.c.event_id)
            ).all()
        return [
            ReviewEvent(
                event_id=row.event_id,
                organization_id=row.organization_id,
                review_id=row.review_id,
                from_status=ReviewStatus(row.from_status),
                to_status=ReviewStatus(row.to_status),
                action=ReviewAction(row.action),
                actor=row.actor,
                occurred_at=_aware(row.occurred_at),
                note=row.note,
            )
            for row in rows
        ]


def _evidence_from_row(row: object) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=row.evidence_id,  # type: ignore[attr-defined]
        organization_id=row.organization_id,  # type: ignore[attr-defined]
        document_id=row.document_id,  # type: ignore[attr-defined]
        kind=EvidenceKind(row.kind),  # type: ignore[attr-defined]
        excerpt=row.excerpt,  # type: ignore[attr-defined]
        version_hash=row.version_hash,  # type: ignore[attr-defined]
        created_at=_aware(row.created_at),  # type: ignore[attr-defined]
        object_id=row.object_id,  # type: ignore[attr-defined]
        revision_id=row.revision_id,  # type: ignore[attr-defined]
        block_id=row.block_id,  # type: ignore[attr-defined]
        chunk_id=row.chunk_id,  # type: ignore[attr-defined]
        source_uri=row.source_uri,  # type: ignore[attr-defined]
        verified=bool(row.verified),  # type: ignore[attr-defined]
    )


def _claim_from_row(row: object) -> Claim:
    return Claim(
        claim_id=row.claim_id,  # type: ignore[attr-defined]
        organization_id=row.organization_id,  # type: ignore[attr-defined]
        document_id=row.document_id,  # type: ignore[attr-defined]
        statement=row.statement,  # type: ignore[attr-defined]
        evidence_ids=tuple(row.evidence_ids),  # type: ignore[attr-defined]
        created_by=row.created_by,  # type: ignore[attr-defined]
        created_at=_aware(row.created_at),  # type: ignore[attr-defined]
        confidence=row.confidence,  # type: ignore[attr-defined]
        generated=bool(row.generated),  # type: ignore[attr-defined]
    )
