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

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Table, func, insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import Select

from northstar.adapters.persistence_sqlalchemy.outbox import SqlAlchemyOutbox
from northstar.adapters.persistence_sqlalchemy.runtime_tables import RUNTIME_TABLES, RuntimeTables
from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from northstar.kernel.context import Actor, ActorType
from northstar.kernel.events.domain_event import DomainEvent

from ..application.ports import CatalogRow, TermCount
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

    def _terms_for(self, organization_id: str, object_id: str) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for t in self._taxonomy.values():
            if t.object_id == object_id and t.organization_id == organization_id:
                grouped.setdefault(t.scheme, []).append(t.term)
        return grouped

    def list_published(
        self,
        *,
        organization_id: str,
        filters: Mapping[str, str] = {},
        limit: int = 200,
        offset: int = 0,
        title_query: str | None = None,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        sort: str = "order",
    ) -> Sequence[CatalogRow]:
        rows: list[tuple[str, datetime | None, CatalogRow]] = []
        needle = (title_query or "").strip().lower()
        for document in self._documents.values():
            if document.organization_id != organization_id:
                continue
            if document.lifecycle is not Lifecycle.PUBLISHED:
                continue
            terms = self._terms_for(organization_id, document.object_id)
            if any(term not in terms.get(scheme, []) for scheme, term in filters.items()):
                continue
            revision = (
                self._revisions.get(document.latest_revision_id or "")
                if document.latest_revision_id
                else None
            )
            title = revision.title if revision else document.object_id
            if needle and not _title_matches(title, needle):
                continue
            published_at = revision.created_at if revision is not None else None
            if published_after is not None and (
                published_at is None or published_at < published_after
            ):
                continue
            if published_before is not None and (
                published_at is None or published_at > published_before
            ):
                continue
            order_key = (terms.get("order") or ["999999"])[0]
            rows.append(
                (
                    order_key,
                    published_at,
                    CatalogRow(
                        object_id=document.object_id,
                        revision_id=document.latest_revision_id,
                        title=title,
                        summary=revision.summary if revision else None,
                        document_type=document.document_type,
                        locale=document.canonical_locale,
                        terms=terms,
                        published_at=published_at,
                    ),
                )
            )
        if sort == "recent":
            rows.sort(
                key=lambda pair: (
                    0 if pair[1] is not None else 1,
                    -(pair[1].timestamp()) if pair[1] is not None else 0,
                    pair[2].title,
                )
            )
        else:
            rows.sort(key=lambda pair: (pair[0], pair[2].title))
        return [row for _, _, row in rows[offset : offset + limit]]

    def count_published(
        self,
        *,
        organization_id: str,
        filters: Mapping[str, str] = {},
        title_query: str | None = None,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
    ) -> int:
        return len(
            self.list_published(
                organization_id=organization_id,
                filters=filters,
                limit=10_000_000,
                offset=0,
                title_query=title_query,
                published_after=published_after,
                published_before=published_before,
            )
        )

    def distinct_terms(self, *, organization_id: str, scheme: str) -> Sequence[TermCount]:
        counts: dict[str, set[str]] = {}
        for t in self._taxonomy.values():
            if t.organization_id == organization_id and t.scheme == scheme:
                counts.setdefault(t.term, set()).add(t.object_id)
        return [TermCount(term=term, count=len(objs)) for term, objs in sorted(counts.items())]


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

    def list_published(
        self,
        *,
        organization_id: str,
        filters: Mapping[str, str] = {},
        limit: int = 200,
        offset: int = 0,
        title_query: str | None = None,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        sort: str = "order",
    ) -> Sequence[CatalogRow]:
        ko = self._tables.knowledge_object
        rev = self._tables.revision
        tax = self._tables.taxonomy_assignment
        pub = self._tables.publication
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            order_term, published_at = _catalog_order_columns(ko, tax, pub, organization_id)
            stmt = _catalog_base_select(
                ko, rev, order_term, published_at, organization_id=organization_id
            )
            stmt = _apply_catalog_filters(
                stmt,
                ko=ko,
                rev=rev,
                tax=tax,
                pub=pub,
                organization_id=organization_id,
                filters=filters,
                title_query=title_query,
                published_after=published_after,
                published_before=published_before,
            )
            if sort == "recent":
                stmt = stmt.order_by(published_at.desc().nulls_last(), rev.c.title.asc())
            else:
                stmt = stmt.order_by(order_term.asc(), rev.c.title.asc())
            stmt = stmt.limit(limit).offset(offset)
            rows = session.execute(stmt).all()

            object_ids = [r.object_id for r in rows]
            terms_by_object: dict[str, dict[str, list[str]]] = {oid: {} for oid in object_ids}
            if object_ids:
                tax_rows = session.execute(
                    select(tax.c.object_id, tax.c.scheme, tax.c.term).where(
                        tax.c.organization_id == organization_id,
                        tax.c.object_id.in_(object_ids),
                    )
                ).all()
                for tr in tax_rows:
                    terms_by_object.setdefault(tr.object_id, {}).setdefault(tr.scheme, []).append(
                        tr.term
                    )

        return [
            CatalogRow(
                object_id=r.object_id,
                revision_id=r.latest_revision_id,
                title=r.title or r.object_id,
                summary=r.summary,
                document_type=r.document_type,
                locale=r.canonical_locale,
                terms=terms_by_object.get(r.object_id, {}),
                published_at=_aware(r.published_at) if getattr(r, "published_at", None) else None,
            )
            for r in rows
        ]

    def count_published(
        self,
        *,
        organization_id: str,
        filters: Mapping[str, str] = {},
        title_query: str | None = None,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
    ) -> int:
        ko = self._tables.knowledge_object
        rev = self._tables.revision
        tax = self._tables.taxonomy_assignment
        pub = self._tables.publication
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            stmt = (
                select(ko.c.object_id)
                .select_from(ko.outerjoin(rev, rev.c.revision_id == ko.c.latest_revision_id))
                .where(
                    ko.c.organization_id == organization_id,
                    ko.c.lifecycle == Lifecycle.PUBLISHED.value,
                )
            )
            stmt = _apply_catalog_filters(
                stmt,
                ko=ko,
                rev=rev,
                tax=tax,
                pub=pub,
                organization_id=organization_id,
                filters=filters,
                title_query=title_query,
                published_after=published_after,
                published_before=published_before,
            )
            counted = session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        return int(counted or 0)

    def distinct_terms(self, *, organization_id: str, scheme: str) -> Sequence[TermCount]:
        tax = self._tables.taxonomy_assignment
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(
                    tax.c.term,
                    func.count(func.distinct(tax.c.object_id)).label("cnt"),
                )
                .where(
                    tax.c.organization_id == organization_id,
                    tax.c.scheme == scheme,
                )
                .group_by(tax.c.term)
                .order_by(tax.c.term.asc())
            ).all()
        return [TermCount(term=r.term, count=int(r.cnt)) for r in rows]


def _like_pattern(raw: str) -> str:
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _title_matches(title: str, needle: str) -> bool:
    """Case-insensitive title match; single tokens use word boundaries (so RAG ≠ Storage)."""
    if _TOKEN_RE.fullmatch(needle):
        return re.search(rf"\b{re.escape(needle)}\b", title, flags=re.IGNORECASE) is not None
    return needle.lower() in title.lower()


def _title_filter(column: ColumnElement[object], raw: str) -> ColumnElement[bool]:
    if _TOKEN_RE.fullmatch(raw):
        return column.op("~*")(rf"\m{re.escape(raw)}\M")
    return column.ilike(_like_pattern(raw), escape="\\")


def _catalog_order_columns(
    ko: Table, tax: Table, pub: Table, organization_id: str
) -> tuple[ColumnElement[object], ColumnElement[object]]:
    order_term = (
        select(tax.c.term)
        .where(
            tax.c.object_id == ko.c.object_id,
            tax.c.organization_id == organization_id,
            tax.c.scheme == "order",
        )
        .limit(1)
        .scalar_subquery()
    )
    published_at = (
        select(func.max(pub.c.published_at))
        .where(
            pub.c.object_id == ko.c.object_id,
            pub.c.organization_id == organization_id,
        )
        .scalar_subquery()
    )
    return order_term, published_at


def _catalog_base_select(
    ko: Table,
    rev: Table,
    order_term: ColumnElement[object],
    published_at: ColumnElement[object],
    *,
    organization_id: str,
) -> Select[tuple[object, ...]]:
    return (
        select(
            ko.c.object_id,
            ko.c.latest_revision_id,
            ko.c.document_type,
            ko.c.canonical_locale,
            rev.c.title,
            rev.c.summary,
            order_term.label("order_term"),
            published_at.label("published_at"),
        )
        .select_from(ko.outerjoin(rev, rev.c.revision_id == ko.c.latest_revision_id))
        .where(
            ko.c.organization_id == organization_id,
            ko.c.lifecycle == Lifecycle.PUBLISHED.value,
        )
    )


def _apply_catalog_filters(
    stmt: Select[tuple[object, ...]],
    *,
    ko: Table,
    rev: Table,
    tax: Table,
    pub: Table,
    organization_id: str,
    filters: Mapping[str, str],
    title_query: str | None,
    published_after: datetime | None,
    published_before: datetime | None,
) -> Select[tuple[object, ...]]:
    for scheme, term in filters.items():
        exists_q = (
            select(tax.c.assignment_id)
            .where(
                tax.c.object_id == ko.c.object_id,
                tax.c.organization_id == organization_id,
                tax.c.scheme == scheme,
                tax.c.term == term,
            )
            .exists()
        )
        stmt = stmt.where(exists_q)
    if title_query:
        stmt = stmt.where(_title_filter(rev.c.title, title_query))
    if published_after is not None or published_before is not None:
        pub_exists = select(pub.c.publication_id).where(
            pub.c.object_id == ko.c.object_id,
            pub.c.organization_id == organization_id,
        )
        if published_after is not None:
            pub_exists = pub_exists.where(pub.c.published_at >= published_after)
        if published_before is not None:
            pub_exists = pub_exists.where(pub.c.published_at <= published_before)
        stmt = stmt.where(pub_exists.exists())
    return stmt


def _aware(value: datetime) -> datetime:
    """Ensure a timezone-aware UTC datetime (PostgreSQL returns tz-aware; SQLite may not)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
