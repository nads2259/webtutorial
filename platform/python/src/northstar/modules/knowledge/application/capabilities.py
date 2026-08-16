"""Knowledge capabilities: one authoritative implementation per action (LAW-04).

Each handler runs through the kernel command/query bus, so every mutation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope is taken from the authenticated
:class:`RequestContext` (``tenant_scope``), **never** from the request payload (rule 50). The
``publish`` capability validates the content, writes an IMMUTABLE revision with provenance and
appends ``northstar.knowledge.document-published.v1`` to the transactional outbox in the SAME unit
of work (LAW-07/LAW-10). Handlers depend only on :mod:`.ports` and the pure :mod:`..domain`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from northstar.kernel.context import Actor
from northstar.kernel.events.domain_event import DomainEvent, EventScope

from ..domain.blocks import ContentTree
from ..domain.errors import KnowledgeInvariantViolation, TenantScopeMissing
from ..domain.model import (
    Draft,
    KnowledgeObject,
    Lifecycle,
    Publication,
    Revision,
    TaxonomyAssignment,
    Visibility,
    new_revision,
)
from .ports import KnowledgeRepositoryPort

CAP_VERSION = "1.0.0"

CAP_CREATE_DOCUMENT = "knowledge.document.create"
CAP_EDIT_DRAFT = "knowledge.draft.edit"
CAP_SUBMIT_FOR_REVIEW = "knowledge.document.submit"
CAP_PUBLISH_DOCUMENT = "knowledge.document.publish"
CAP_GET_DOCUMENT = "knowledge.document.get"
CAP_GET_REVISION = "knowledge.revision.get"
CAP_ASSIGN_TAXONOMY = "knowledge.taxonomy.assign"

KNOWLEDGE_CAPABILITIES: tuple[str, ...] = (
    CAP_CREATE_DOCUMENT,
    CAP_EDIT_DRAFT,
    CAP_SUBMIT_FOR_REVIEW,
    CAP_PUBLISH_DOCUMENT,
    CAP_GET_DOCUMENT,
    CAP_GET_REVISION,
    CAP_ASSIGN_TAXONOMY,
)

EVENT_DOCUMENT_PUBLISHED = "northstar.knowledge.document-published.v1"
_EVENT_SOURCE = "module://northstar.knowledge"
_EVENT_DATASCHEMA = "https://schemas.northstar.example/events/knowledge-document-published/1.0.0"

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]

_VISIBILITY_CLASSIFICATION = {
    Visibility.PUBLIC: "public",
    Visibility.ORGANIZATION: "internal",
    Visibility.PRIVATE: "confidential",
}


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateDocumentCommand:
    document_type: str
    locale: str
    title: str
    blocks: tuple[dict[str, Any], ...] = ()
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class CreateDocumentResult:
    object_id: str
    draft_id: str
    organization_id: str


@dataclass(frozen=True, slots=True)
class EditDraftCommand:
    object_id: str
    blocks: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class EditDraftResult:
    object_id: str
    draft_id: str
    version: int


@dataclass(frozen=True, slots=True)
class SubmitForReviewCommand:
    object_id: str


@dataclass(frozen=True, slots=True)
class SubmitForReviewResult:
    object_id: str
    lifecycle: str


@dataclass(frozen=True, slots=True)
class PublishDocumentCommand:
    object_id: str
    title: str
    channel: str = "default"
    visibility: str = "organization"
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class PublishDocumentResult:
    object_id: str
    revision_id: str
    parent_revision_id: str | None
    content_hash: str
    event_id: str


@dataclass(frozen=True, slots=True)
class GetDocumentQuery:
    object_id: str


@dataclass(frozen=True, slots=True)
class DocumentView:
    object_id: str
    organization_id: str
    document_type: str
    locale: str
    lifecycle: str
    latest_revision_id: str | None


@dataclass(frozen=True, slots=True)
class GetRevisionQuery:
    revision_id: str


@dataclass(frozen=True, slots=True)
class RevisionView:
    revision_id: str
    object_id: str
    parent_revision_id: str | None
    document_type: str
    locale: str
    title: str
    content_hash: str
    blocks: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class AssignTaxonomyCommand:
    object_id: str
    scheme: str
    term: str


@dataclass(frozen=True, slots=True)
class AssignTaxonomyResult:
    object_id: str
    assignment_id: str
    scheme: str
    term: str


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


def _context(invocation: object) -> object:
    return getattr(invocation, "context", None)


def _load_document(
    repo: KnowledgeRepositoryPort, *, organization_id: str, object_id: str
) -> KnowledgeObject:
    document = repo.get_document(organization_id=organization_id, object_id=object_id)
    if document is None:
        # Absent or belongs to another tenant: fail closed, do not disclose (rule 50).
        raise KnowledgeInvariantViolation(
            "document is not available in this scope", code="knowledge.document.not_found"
        )
    return document


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class CreateDocument:
    """``knowledge.document.create`` — create a document with an initial typed draft tree."""

    def __init__(
        self, *, repository: KnowledgeRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> CreateDocumentResult:
        command = _typed(request, CreateDocumentCommand)
        organization_id = _tenant(request)
        tree = ContentTree.from_document_blocks(list(command.blocks))
        document = KnowledgeObject(
            object_id=self._id_factory(),
            organization_id=organization_id,
            document_type=command.document_type,
            canonical_locale=command.locale,
            lifecycle=Lifecycle.DRAFT,
        )
        _require_title(command.title)
        draft = Draft(draft_id=self._id_factory(), object_id=document.object_id, tree=tree)
        self._repo.add_document(document)
        self._repo.save_draft(organization_id=organization_id, draft=draft)
        return CreateDocumentResult(
            object_id=document.object_id,
            draft_id=draft.draft_id,
            organization_id=organization_id,
        )


class EditDraft:
    """``knowledge.draft.edit`` — replace the working draft's typed content tree."""

    def __init__(self, *, repository: KnowledgeRepositoryPort, id_factory: IdFactory) -> None:
        self._repo = repository
        self._id_factory = id_factory

    def handle(self, request: object) -> EditDraftResult:
        command = _typed(request, EditDraftCommand)
        organization_id = _tenant(request)
        _load_document(self._repo, organization_id=organization_id, object_id=command.object_id)
        existing = self._repo.get_draft(
            organization_id=organization_id, object_id=command.object_id
        )
        tree = ContentTree.from_document_blocks(list(command.blocks))
        if existing is None:
            draft = Draft(
                draft_id=self._id_factory(), object_id=command.object_id, tree=tree, version=1
            )
        else:
            draft = Draft(
                draft_id=existing.draft_id,
                object_id=command.object_id,
                tree=tree,
                version=existing.version + 1,
                base_revision_id=existing.base_revision_id,
            )
        self._repo.save_draft(organization_id=organization_id, draft=draft)
        return EditDraftResult(
            object_id=command.object_id, draft_id=draft.draft_id, version=draft.version
        )


class SubmitForReview:
    """``knowledge.document.submit`` — move a document into the review state."""

    def __init__(self, *, repository: KnowledgeRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> SubmitForReviewResult:
        command = _typed(request, SubmitForReviewCommand)
        organization_id = _tenant(request)
        _load_document(self._repo, organization_id=organization_id, object_id=command.object_id)
        self._repo.set_lifecycle(
            organization_id=organization_id,
            object_id=command.object_id,
            lifecycle=Lifecycle.IN_REVIEW.value,
        )
        return SubmitForReviewResult(
            object_id=command.object_id, lifecycle=Lifecycle.IN_REVIEW.value
        )


class PublishDocument:
    """``knowledge.document.publish`` — validate + write an immutable revision + emit the event.

    Publishing is the single authoritative path that mints a new immutable revision (with a
    ``content_hash`` and predecessor pointer) and emits
    ``northstar.knowledge.document-published.v1`` transactionally (LAW-07/LAW-10).
    """

    def __init__(
        self, *, repository: KnowledgeRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> PublishDocumentResult:
        command = _typed(request, PublishDocumentCommand)
        organization_id = _tenant(request)
        context = _context(request)
        document = _load_document(
            self._repo, organization_id=organization_id, object_id=command.object_id
        )
        draft = self._repo.get_draft(organization_id=organization_id, object_id=command.object_id)
        if draft is None:
            raise KnowledgeInvariantViolation(
                "cannot publish a document without a draft", code="knowledge.publish.no_draft"
            )
        visibility = _visibility(command.visibility)
        now = self._clock()
        actor: Actor = context.actor
        _require_title(command.title)

        revision = new_revision(
            revision_id=self._id_factory(),
            document=document,
            title=command.title,
            tree=draft.tree,
            created_by=actor,
            created_at=now,
            parent_revision_id=document.latest_revision_id,
            summary=command.summary,
        )
        publication = Publication(
            publication_id=self._id_factory(),
            object_id=document.object_id,
            revision_id=revision.revision_id,
            channel=command.channel,
            locale=revision.locale,
            visibility=visibility,
            published_at=now,
        )
        event = self._build_event(
            document=document,
            revision=revision,
            visibility=visibility,
            actor=actor,
            correlation_id=context.correlation_id,
            occurred_at=now,
        )
        published = _replace_pointer(document, revision.revision_id)
        self._repo.publish(
            organization_id=organization_id,
            document=published,
            revision=revision,
            publication=publication,
            event=event,
        )
        return PublishDocumentResult(
            object_id=document.object_id,
            revision_id=revision.revision_id,
            parent_revision_id=revision.parent_revision_id,
            content_hash=revision.content_hash,
            event_id=event.event_id,
        )

    def _build_event(
        self,
        *,
        document: KnowledgeObject,
        revision: Revision,
        visibility: Visibility,
        actor: Actor,
        correlation_id: str,
        occurred_at: datetime,
    ) -> DomainEvent:
        return DomainEvent(
            event_id=self._id_factory(),
            event_type=EVENT_DOCUMENT_PUBLISHED,
            source=_EVENT_SOURCE,
            correlation_id=correlation_id,
            actor=actor,
            aggregate_type="knowledge-document",
            aggregate_id=document.object_id,
            occurred_at=occurred_at,
            data={
                "object_id": document.object_id,
                "revision_id": revision.revision_id,
                "parent_revision_id": revision.parent_revision_id,
                "document_type": revision.document_type,
                "locale": revision.locale,
                "title": revision.title,
                "content_hash": revision.content_hash,
                "visibility": visibility.value,
            },
            dataschema=_EVENT_DATASCHEMA,
            classification=_VISIBILITY_CLASSIFICATION[visibility],
            scope=EventScope(organization_id=document.organization_id),
        )


class GetDocument:
    """``knowledge.document.get`` (query) — read a document header in the caller's tenant."""

    def __init__(self, *, repository: KnowledgeRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> DocumentView:
        query = _typed(request, GetDocumentQuery)
        organization_id = _tenant(request)
        document = _load_document(
            self._repo, organization_id=organization_id, object_id=query.object_id
        )
        return DocumentView(
            object_id=document.object_id,
            organization_id=document.organization_id,
            document_type=document.document_type,
            locale=document.canonical_locale,
            lifecycle=document.lifecycle.value,
            latest_revision_id=document.latest_revision_id,
        )


class GetRevision:
    """``knowledge.revision.get`` (query) — read an immutable revision in the caller's tenant."""

    def __init__(self, *, repository: KnowledgeRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> RevisionView:
        query = _typed(request, GetRevisionQuery)
        organization_id = _tenant(request)
        revision = self._repo.get_revision(
            organization_id=organization_id, revision_id=query.revision_id
        )
        if revision is None:
            raise KnowledgeInvariantViolation(
                "revision is not available in this scope", code="knowledge.revision.not_found"
            )
        return RevisionView(
            revision_id=revision.revision_id,
            object_id=revision.object_id,
            parent_revision_id=revision.parent_revision_id,
            document_type=revision.document_type,
            locale=revision.locale,
            title=revision.title,
            content_hash=revision.content_hash,
            blocks=tuple(revision.tree.to_document_blocks()),
        )


class AssignTaxonomy:
    """``knowledge.taxonomy.assign`` — assign a taxonomy term (topic/path) to a document."""

    def __init__(self, *, repository: KnowledgeRepositoryPort, id_factory: IdFactory) -> None:
        self._repo = repository
        self._id_factory = id_factory

    def handle(self, request: object) -> AssignTaxonomyResult:
        command = _typed(request, AssignTaxonomyCommand)
        organization_id = _tenant(request)
        _load_document(self._repo, organization_id=organization_id, object_id=command.object_id)
        assignment = TaxonomyAssignment(
            assignment_id=self._id_factory(),
            object_id=command.object_id,
            organization_id=organization_id,
            scheme=command.scheme,
            term=command.term,
        )
        self._repo.assign_taxonomy(organization_id=organization_id, assignment=assignment)
        return AssignTaxonomyResult(
            object_id=command.object_id,
            assignment_id=assignment.assignment_id,
            scheme=assignment.scheme,
            term=assignment.term,
        )


def _require_title(title: str) -> None:
    if not 1 <= len(title) <= 300:
        raise KnowledgeInvariantViolation(
            "title must be 1..300 characters", code="knowledge.document.title"
        )


def _visibility(value: str) -> Visibility:
    try:
        return Visibility(value)
    except ValueError as exc:
        raise KnowledgeInvariantViolation(
            f"unknown visibility {value!r}", code="knowledge.publish.visibility"
        ) from exc


def _replace_pointer(document: KnowledgeObject, revision_id: str) -> KnowledgeObject:
    return replace(document, lifecycle=Lifecycle.PUBLISHED, latest_revision_id=revision_id)
