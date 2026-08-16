"""Knowledge repositories (in-memory + SQLAlchemy) implementing :class:`KnowledgeRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL RLS applies as defense-in-depth
(FR-POL-004). :meth:`SqlAlchemyKnowledgeRepository.publish` writes the immutable revision, its
block index, the publication row and the document-pointer update **and** appends the
``document-published`` event to the transactional outbox in a SINGLE unit of work (LAW-07/LAW-10);
re-publishing an existing ``revision_id`` is rejected (immutability). No string interpolation of
values.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.outbox import SqlAlchemyOutbox
from northstar.adapters.persistence_sqlalchemy.runtime_tables import RUNTIME_TABLES, RuntimeTables
from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from northstar.kernel.context import Actor, ActorType
from northstar.kernel.events.domain_event import DomainEvent

from ..domain.blocks import Block, ContentTree
from ..domain.errors import ImmutableRevisionError
from ..domain.model import (
    Draft,
    KnowledgeObject,
    Lifecycle,
    Publication,
    Revision,
    TaxonomyAssignment,
)
from .tables import KnowledgeTables


def _actor_ref(actor: Actor) -> dict[str, str | None]:
    return {"type": actor.type.value, "id": actor.id, "delegated_by": actor.delegated_by}


def _actor_from_ref(ref: dict[str, Any]) -> Actor:
    return Actor(type=ActorType(ref["type"]), id=ref["id"], delegated_by=ref.get("delegated_by"))


def _flatten_blocks(blocks: Sequence[Block], *, path: str = "") -> list[tuple[str, str, int, str]]:
    """Yield ``(block_id, block_type, ordinal, path)`` rows for the block index (stable ids)."""
    rows: list[tuple[str, str, int, str]] = []
    for ordinal, block in enumerate(blocks):
        here = f"{path}{ordinal}"
        rows.append((block.block_id, block.block_type, ordinal, here))
        rows.extend(_flatten_blocks(block.children, path=f"{here}."))
    return rows


class InMemoryKnowledgeRepository:
    """In-memory repository for fast, deterministic unit tests (records emitted events)."""

    def __init__(self) -> None:
        self._documents: dict[str, KnowledgeObject] = {}
        self._drafts: dict[str, Draft] = {}
        self._revisions: dict[str, Revision] = {}
        self._publications: dict[str, Publication] = {}
        self._taxonomy: dict[str, TaxonomyAssignment] = {}
        self.events: list[DomainEvent] = []

    def add_document(self, document: KnowledgeObject) -> None:
        self._documents[document.object_id] = document

    def get_document(self, *, organization_id: str, object_id: str) -> KnowledgeObject | None:
        document = self._documents.get(object_id)
        if document is None or document.organization_id != organization_id:
            return None
        return document

    def save_draft(self, *, organization_id: str, draft: Draft) -> None:
        self._drafts[draft.object_id] = draft

    def get_draft(self, *, organization_id: str, object_id: str) -> Draft | None:
        document = self.get_document(organization_id=organization_id, object_id=object_id)
        if document is None:
            return None
        return self._drafts.get(object_id)

    def set_lifecycle(self, *, organization_id: str, object_id: str, lifecycle: str) -> None:
        document = self.get_document(organization_id=organization_id, object_id=object_id)
        if document is None:
            return
        self._documents[object_id] = replace(document, lifecycle=Lifecycle(lifecycle))

    def publish(
        self,
        *,
        organization_id: str,
        document: KnowledgeObject,
        revision: Revision,
        publication: Publication,
        event: DomainEvent,
    ) -> None:
        if revision.revision_id in self._revisions:
            raise ImmutableRevisionError(revision.revision_id)
        self._documents[document.object_id] = document
        self._revisions[revision.revision_id] = revision
        self._publications[publication.publication_id] = publication
        self.events.append(event)

    def get_revision(self, *, organization_id: str, revision_id: str) -> Revision | None:
        revision = self._revisions.get(revision_id)
        if revision is None:
            return None
        document = self._documents.get(revision.object_id)
        if document is None or document.organization_id != organization_id:
            return None
        return revision

    def assign_taxonomy(self, *, organization_id: str, assignment: TaxonomyAssignment) -> None:
        self._taxonomy[assignment.assignment_id] = assignment

    def list_taxonomy(
        self, *, organization_id: str, object_id: str
    ) -> Sequence[TaxonomyAssignment]:
        return [
            t
            for t in self._taxonomy.values()
            if t.object_id == object_id and t.organization_id == organization_id
        ]


class SqlAlchemyKnowledgeRepository:
    """PostgreSQL repository; scoped queries filter by ``organization_id`` and set the GUC."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        tables: KnowledgeTables,
        runtime_tables: RuntimeTables = RUNTIME_TABLES,
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables
        self._runtime_tables = runtime_tables

    def add_document(self, document: KnowledgeObject) -> None:
        table = self._tables.knowledge_object
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, document.organization_id)
            uow.session.execute(
                insert(table).values(
                    object_id=document.object_id,
                    organization_id=document.organization_id,
                    document_type=document.document_type,
                    canonical_locale=document.canonical_locale,
                    lifecycle=document.lifecycle.value,
                    latest_revision_id=document.latest_revision_id,
                )
            )
            uow.commit()

    def get_document(self, *, organization_id: str, object_id: str) -> KnowledgeObject | None:
        table = self._tables.knowledge_object
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.object_id == object_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return KnowledgeObject(
            object_id=row.object_id,
            organization_id=row.organization_id,
            document_type=row.document_type,
            canonical_locale=row.canonical_locale,
            lifecycle=Lifecycle(row.lifecycle),
            latest_revision_id=row.latest_revision_id,
        )

    def save_draft(self, *, organization_id: str, draft: Draft) -> None:
        table = self._tables.draft
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(table.delete().where(table.c.object_id == draft.object_id))
            uow.session.execute(
                insert(table).values(
                    draft_id=draft.draft_id,
                    object_id=draft.object_id,
                    organization_id=organization_id,
                    base_revision_id=draft.base_revision_id,
                    content_tree=draft.tree.to_document_blocks(),
                    version=draft.version,
                )
            )
            uow.commit()

    def get_draft(self, *, organization_id: str, object_id: str) -> Draft | None:
        table = self._tables.draft
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.object_id == object_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return Draft(
            draft_id=row.draft_id,
            object_id=row.object_id,
            tree=ContentTree.from_document_blocks(row.content_tree),
            version=row.version,
            base_revision_id=row.base_revision_id,
        )

    def set_lifecycle(self, *, organization_id: str, object_id: str, lifecycle: str) -> None:
        table = self._tables.knowledge_object
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.object_id == object_id,
                    table.c.organization_id == organization_id,
                )
                .values(lifecycle=lifecycle)
            )
            uow.commit()

    def publish(
        self,
        *,
        organization_id: str,
        document: KnowledgeObject,
        revision: Revision,
        publication: Publication,
        event: DomainEvent,
    ) -> None:
        tables = self._tables
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)

            existing = session.execute(
                select(tables.revision.c.revision_id).where(
                    tables.revision.c.revision_id == revision.revision_id
                )
            ).first()
            if existing is not None:
                # A published revision is immutable; refuse to overwrite it (LAW-07).
                raise ImmutableRevisionError(revision.revision_id)

            session.execute(
                insert(tables.revision).values(
                    revision_id=revision.revision_id,
                    object_id=revision.object_id,
                    organization_id=organization_id,
                    parent_revision_id=revision.parent_revision_id,
                    document_type=revision.document_type,
                    locale=revision.locale,
                    title=revision.title,
                    summary=revision.summary,
                    content_tree=revision.tree.to_document_blocks(),
                    content_hash=revision.content_hash,
                    schema_version=revision.schema_version,
                    created_by=_actor_ref(revision.created_by),
                    created_at=revision.created_at,
                )
            )
            for block_id, block_type, ordinal, path in _flatten_blocks(revision.tree.blocks):
                session.execute(
                    insert(tables.block).values(
                        revision_id=revision.revision_id,
                        block_id=block_id,
                        object_id=revision.object_id,
                        organization_id=organization_id,
                        block_type=block_type,
                        ordinal=ordinal,
                        path=path,
                    )
                )
            session.execute(
                insert(tables.publication).values(
                    publication_id=publication.publication_id,
                    object_id=publication.object_id,
                    organization_id=organization_id,
                    revision_id=publication.revision_id,
                    channel=publication.channel,
                    locale=publication.locale,
                    visibility=publication.visibility.value,
                    published_at=publication.published_at,
                )
            )
            session.execute(
                update(tables.knowledge_object)
                .where(
                    tables.knowledge_object.c.object_id == document.object_id,
                    tables.knowledge_object.c.organization_id == organization_id,
                )
                .values(
                    lifecycle=document.lifecycle.value,
                    latest_revision_id=document.latest_revision_id,
                )
            )
            # Same unit of work as the state change: the event is durable iff the revision commits.
            SqlAlchemyOutbox(session, tables=self._runtime_tables).append(event)
            uow.commit()

    def get_revision(self, *, organization_id: str, revision_id: str) -> Revision | None:
        table = self._tables.revision
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
        return Revision(
            revision_id=row.revision_id,
            object_id=row.object_id,
            document_type=row.document_type,
            locale=row.locale,
            title=row.title,
            tree=ContentTree.from_document_blocks(row.content_tree),
            content_hash=row.content_hash,
            created_by=_actor_from_ref(row.created_by),
            created_at=_aware(row.created_at),
            parent_revision_id=row.parent_revision_id,
            summary=row.summary,
            schema_version=row.schema_version,
        )

    def assign_taxonomy(self, *, organization_id: str, assignment: TaxonomyAssignment) -> None:
        table = self._tables.taxonomy_assignment
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                insert(table).values(
                    assignment_id=assignment.assignment_id,
                    object_id=assignment.object_id,
                    organization_id=organization_id,
                    scheme=assignment.scheme,
                    term=assignment.term,
                )
            )
            uow.commit()

    def list_taxonomy(
        self, *, organization_id: str, object_id: str
    ) -> Sequence[TaxonomyAssignment]:
        table = self._tables.taxonomy_assignment
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.object_id == object_id,
                    table.c.organization_id == organization_id,
                )
            ).all()
        return [
            TaxonomyAssignment(
                assignment_id=r.assignment_id,
                object_id=r.object_id,
                organization_id=r.organization_id,
                scheme=r.scheme,
                term=r.term,
            )
            for r in rows
        ]


def _aware(value: datetime) -> datetime:
    """Ensure a timezone-aware UTC datetime (PostgreSQL returns tz-aware; SQLite may not)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
