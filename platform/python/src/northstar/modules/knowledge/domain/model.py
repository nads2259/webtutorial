"""Knowledge aggregate: identity, draft, revision, publication and taxonomy (docs/06 §4).

Separates identity (:class:`KnowledgeObject`), working copy (:class:`Draft`), immutable
:class:`Revision` and :class:`Publication`, mirroring the content data model in docs/06 §4. A
published revision is IMMUTABLE (LAW-07/ARCH-007): corrections create a NEW revision whose
``parent_revision_id`` points at its predecessor and whose ``content_hash`` records provenance.
Pure and infrastructure-free (rule 10, LAW-02).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from northstar.kernel.context import Actor

from .blocks import ContentTree
from .errors import ImmutableRevisionError, KnowledgeInvariantViolation

RES_DOCUMENT = "knowledge.document"

# Document types accepted by content-document.schema.json (kept in lock-step with the schema enum).
_DOCUMENT_TYPES = frozenset(
    {
        "tutorial",
        "lesson",
        "course_outline",
        "research_document",
        "report",
        "simulation_guide",
        "knowledge_page",
    }
)


class Lifecycle(StrEnum):
    """Lifecycle of a knowledge object (docs/06 §4 ``lifecycle``)."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"


class Visibility(StrEnum):
    """Publication visibility (maps to the event/data classification, docs/06 §4)."""

    PRIVATE = "private"
    ORGANIZATION = "organization"
    PUBLIC = "public"


def _require(condition: bool, message: str, code: str = "knowledge.invariant.violated") -> None:
    if not condition:
        raise KnowledgeInvariantViolation(message, code=code)


@dataclass(frozen=True, slots=True)
class KnowledgeObject:
    """Stable document identity + lifecycle pointer (the ``knowledge_object`` row, docs/06 §4)."""

    object_id: str
    organization_id: str
    document_type: str
    canonical_locale: str
    lifecycle: Lifecycle = Lifecycle.DRAFT
    latest_revision_id: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.object_id), "object_id must be non-empty", code="knowledge.object.id")
        _require(
            bool(self.organization_id),
            "organization_id must be non-empty",
            code="knowledge.object.scope",
        )
        _require(
            self.document_type in _DOCUMENT_TYPES,
            f"document_type must be one of {sorted(_DOCUMENT_TYPES)}",
            code="knowledge.object.type",
        )
        _require(
            bool(self.canonical_locale),
            "canonical_locale must be non-empty",
            code="knowledge.object.locale",
        )


@dataclass(frozen=True, slots=True)
class Draft:
    """A mutable working copy of a document's content tree (the ``draft`` row, docs/06 §4)."""

    draft_id: str
    object_id: str
    tree: ContentTree
    version: int = 1
    base_revision_id: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.draft_id), "draft_id must be non-empty", code="knowledge.draft.id")
        _require(self.version >= 1, "draft version must be >= 1", code="knowledge.draft.version")


@dataclass(frozen=True, slots=True)
class Revision:
    """An IMMUTABLE published content revision with provenance (LAW-07, docs/06 §4).

    ``content_hash`` and ``parent_revision_id`` record provenance; the frozen dataclass and the
    explicit :meth:`mutate` guard make in-place mutation impossible — corrections must go through
    :meth:`correct`, which mints a *new* revision id descending from this one.
    """

    revision_id: str
    object_id: str
    document_type: str
    locale: str
    title: str
    tree: ContentTree
    content_hash: str
    created_by: Actor
    created_at: datetime
    parent_revision_id: str | None = None
    summary: str | None = None
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        _require(
            bool(self.revision_id), "revision_id must be non-empty", code="knowledge.revision.id"
        )
        _require(
            1 <= len(self.title) <= 300,
            "title must be 1..300 characters",
            code="knowledge.revision.title",
        )
        _require(
            self.content_hash == self.tree.content_hash(),
            "content_hash must match the content tree (provenance integrity)",
            code="knowledge.revision.hash",
        )
        _require(
            self.created_at.tzinfo is not None,
            "created_at must be timezone-aware (UTC)",
            code="knowledge.revision.time",
        )

    def mutate(self, *_args: object, **_kwargs: object) -> None:
        """Reject any in-place mutation of a published revision (LAW-07).

        Published revisions are immutable; this guard makes an accidental in-place edit fail loudly
        instead of silently corrupting provenance. Use :meth:`correct` to create a new revision.
        """
        raise ImmutableRevisionError(self.revision_id)

    def correct(
        self,
        *,
        revision_id: str,
        tree: ContentTree,
        created_by: Actor,
        created_at: datetime,
        title: str | None = None,
        summary: str | None = None,
    ) -> Revision:
        """Create a NEW revision descending from this one (a correction, LAW-07/ARCH-007)."""
        return Revision(
            revision_id=revision_id,
            object_id=self.object_id,
            document_type=self.document_type,
            locale=self.locale,
            title=title if title is not None else self.title,
            tree=tree,
            content_hash=tree.content_hash(),
            created_by=created_by,
            created_at=created_at,
            parent_revision_id=self.revision_id,
            summary=summary if summary is not None else self.summary,
        )


@dataclass(frozen=True, slots=True)
class Publication:
    """An immutable publication record for a revision on a channel (docs/06 §4)."""

    publication_id: str
    object_id: str
    revision_id: str
    channel: str
    locale: str
    visibility: Visibility
    published_at: datetime

    def __post_init__(self) -> None:
        _require(
            bool(self.publication_id),
            "publication_id must be non-empty",
            code="knowledge.publication.id",
        )
        _require(
            bool(self.channel), "channel must be non-empty", code="knowledge.publication.channel"
        )


@dataclass(frozen=True, slots=True)
class TaxonomyAssignment:
    """A taxonomy term (topic/path) assigned to a document (docs/06 §9, FR-CNT-009)."""

    assignment_id: str
    object_id: str
    organization_id: str
    scheme: str
    term: str

    def __post_init__(self) -> None:
        _require(
            bool(self.scheme), "taxonomy scheme must be non-empty", code="knowledge.taxonomy.scheme"
        )
        _require(bool(self.term), "taxonomy term must be non-empty", code="knowledge.taxonomy.term")


def new_revision(
    *,
    revision_id: str,
    document: KnowledgeObject,
    title: str,
    tree: ContentTree,
    created_by: Actor,
    created_at: datetime,
    parent_revision_id: str | None,
    summary: str | None = None,
) -> Revision:
    """Build the first (or a subsequent) revision for a document, computing its provenance hash."""
    return Revision(
        revision_id=revision_id,
        object_id=document.object_id,
        document_type=document.document_type,
        locale=document.canonical_locale,
        title=title,
        tree=tree,
        content_hash=tree.content_hash(),
        created_by=created_by,
        created_at=created_at,
        parent_revision_id=parent_revision_id,
        summary=summary,
    )


def to_canonical_document(
    revision: Revision, *, created_by_ref: dict[str, object]
) -> dict[str, object]:
    """Render a revision as a canonical ``content-document`` envelope (validates vs the schema).

    ``created_by_ref`` is the canonical ``actorRef`` mapping ``{type, id, delegated_by?}`` supplied
    by the caller (the domain does not know the wire actor shape beyond :class:`Actor`).
    """
    provenance: dict[str, object] = {
        "created_by": created_by_ref,
        "created_at": revision.created_at.isoformat(),
        "content_hash": revision.content_hash,
    }
    document: dict[str, object] = {
        "schema_version": "1.0",
        "object_id": revision.object_id,
        "revision_id": revision.revision_id,
        "parent_revision_id": revision.parent_revision_id,
        "document_type": revision.document_type,
        "locale": revision.locale,
        "title": revision.title,
        "blocks": revision.tree.to_document_blocks(),
        "provenance": provenance,
    }
    if revision.summary is not None:
        document["summary"] = revision.summary
    return document
