"""Annotation aggregate, threads and the visibility projection (FR-ANN-001/003/005/006).

An :class:`Annotation` anchors a body to an exact content target via a selector set. It RETAINS its
ORIGINAL revision target forever (``target.source_revision_id`` + ``target.selectors``, FR-ANN-003);
a later remap is recorded as a *separate* ``current_remap`` without ever mutating the original.
Visibility is a first-class field enforced by the policy engine and by the pure
:func:`AnnotationVisibilityPolicy.can_view` projection (FR-ANN-005) — never hidden UI. Threads group
replies (FR-ANN-006); a reply may never be more visible than its parent (no disclosure leak).
Pure and infrastructure-free (rule 10, LAW-02).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from northstar.kernel.context import Actor

from .errors import AnnotationInvariantViolation, VisibilityBroadeningRejected
from .remap import RemapResult, ReviewReason
from .selectors import Selector, selectors_to_dicts

RES_ANNOTATION = "annotation.annotation"

SCHEMA_VERSION = "1.0"


class Motivation(StrEnum):
    """Why the annotation exists (``annotation.schema.json`` ``motivation`` enum, FR-ANN-001)."""

    COMMENTING = "commenting"
    DESCRIBING = "describing"
    HIGHLIGHTING = "highlighting"
    QUESTIONING = "questioning"
    SUGGESTING = "suggesting"
    TAGGING = "tagging"
    MODERATING = "moderating"
    AI_ASSISTANCE = "ai_assistance"


class AnnotationVisibility(StrEnum):
    """Visibility scope (``annotation.schema.json`` ``visibility`` enum, FR-ANN-005)."""

    PRIVATE = "private"
    TEAM = "team"
    WORKSPACE = "workspace"
    PUBLIC = "public"
    EDITORIAL = "editorial"
    MODERATION_ONLY = "moderation_only"


class AnnotationState(StrEnum):
    """Lifecycle state (``annotation.schema.json`` ``state`` enum)."""

    ACTIVE = "active"
    RESOLVED = "resolved"
    HIDDEN = "hidden"
    DELETED = "deleted"
    ORPHANED = "orphaned"
    PENDING_REVIEW = "pending_review"


class BodyType(StrEnum):
    """Body payload kind (``annotation.schema.json`` ``body.type`` enum)."""

    TEXT = "text"
    RICH_TEXT = "rich_text"
    REFERENCE = "reference"
    MODERATION_REPORT = "moderation_report"
    AI_CONVERSATION_REF = "ai_conversation_ref"


# Broadness ordering: a reply may not exceed its parent's visibility (rule 50, no leak).
_VISIBILITY_RANK: dict[AnnotationVisibility, int] = {
    AnnotationVisibility.PRIVATE: 0,
    AnnotationVisibility.MODERATION_ONLY: 1,
    AnnotationVisibility.EDITORIAL: 2,
    AnnotationVisibility.TEAM: 3,
    AnnotationVisibility.WORKSPACE: 4,
    AnnotationVisibility.PUBLIC: 5,
}

# Public content is world-visible; the rest are least-disclosure and map to non-public classes.
_VISIBILITY_CLASSIFICATION: dict[AnnotationVisibility, str] = {
    AnnotationVisibility.PUBLIC: "public",
    AnnotationVisibility.WORKSPACE: "internal",
    AnnotationVisibility.TEAM: "internal",
    AnnotationVisibility.EDITORIAL: "confidential",
    AnnotationVisibility.MODERATION_ONLY: "confidential",
    AnnotationVisibility.PRIVATE: "confidential",
}

_REVIEW_STATE: dict[ReviewReason, AnnotationState] = {
    ReviewReason.ORPHANED: AnnotationState.ORPHANED,
    ReviewReason.AMBIGUOUS: AnnotationState.PENDING_REVIEW,
}


@dataclass(frozen=True, slots=True)
class AnnotationBody:
    """The typed body content of an annotation (text/rich-text/reference/report/AI ref)."""

    type: BodyType
    content: Any
    locale: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, "content": self.content, "locale": self.locale}


@dataclass(frozen=True, slots=True)
class AnnotationTarget:
    """The content anchor: original (immutable) target + optional current remap (FR-ANN-003)."""

    object_id: str
    source_revision_id: str
    selectors: tuple[Selector, ...]
    source_fingerprint: str | None = None
    current_remap: RemapResult | None = None

    def __post_init__(self) -> None:
        if not self.selectors:
            raise AnnotationInvariantViolation(
                "an annotation target requires at least one selector",
                code="annotation.target.selectors",
            )

    @property
    def current_revision_id(self) -> str:
        """The revision the annotation currently resolves to.

        Stays the ORIGINAL revision until a *confident* remap maps it forward; a review outcome
        never advances the current target (FR-ANN-004).
        """
        if self.current_remap is not None and self.current_remap.mapped:
            return self.current_remap.target_revision_id or self.source_revision_id
        return self.source_revision_id

    def with_remap(self, result: RemapResult) -> AnnotationTarget:
        """Attach a remap outcome WITHOUT ever altering the original revision/selectors."""
        return replace(self, current_remap=result)

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "revision_id": self.source_revision_id,
            "selectors": selectors_to_dicts(self.selectors),
            "current_remap": self.current_remap.to_dict() if self.current_remap else None,
        }


@dataclass(frozen=True, slots=True)
class Annotation:
    """A selector-anchored annotation over versioned content (the aggregate root)."""

    annotation_id: str
    organization_id: str
    motivation: Motivation
    body: AnnotationBody
    target: AnnotationTarget
    visibility: AnnotationVisibility
    creator: Actor
    created_at: datetime
    state: AnnotationState = AnnotationState.ACTIVE
    audience_ids: tuple[str, ...] = ()
    thread_id: str | None = None
    parent_annotation_id: str | None = None
    policy_decision_id: str | None = None

    def with_remap(self, result: RemapResult) -> Annotation:
        """Return a new annotation carrying ``result``.

        A confident mapping keeps the annotation active; a review outcome routes it to the
        orphaned/pending-review state and NEVER moves the original target (FR-ANN-004).
        """
        target = self.target.with_remap(result)
        if result.needs_review and result.review_reason is not None:
            return replace(self, target=target, state=_REVIEW_STATE[result.review_reason])
        return replace(self, target=target)

    def with_visibility(self, visibility: AnnotationVisibility) -> Annotation:
        return replace(self, visibility=visibility)

    def with_state(self, state: AnnotationState) -> Annotation:
        return replace(self, state=state)

    @property
    def classification(self) -> str:
        return _VISIBILITY_CLASSIFICATION[self.visibility]

    def to_contract_dict(self) -> dict[str, Any]:
        """Project to a document conforming to ``annotation.schema.json`` (validated in tests)."""
        document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "annotation_id": self.annotation_id,
            "motivation": self.motivation.value,
            "body": self.body.to_dict(),
            "target": self.target.to_dict(),
            "visibility": self.visibility.value,
            "creator": {
                "type": self.creator.type.value,
                "id": self.creator.id,
                "delegated_by": self.creator.delegated_by,
            },
            "created_at": self.created_at.isoformat(),
            "state": self.state.value,
            "thread_id": self.thread_id,
            "policy_decision_id": self.policy_decision_id,
        }
        if self.audience_ids:
            document["audience_ids"] = list(dict.fromkeys(self.audience_ids))
        return document


class ModerationKind(StrEnum):
    """A reversible moderation action recorded as tamper-evident evidence (FR-ANN-007, LAW-14)."""

    HIDE = "hide"
    UNHIDE = "unhide"
    RESOLVE = "resolve"
    REPORT = "report"


# Moderation applies to non-private annotations only (a private note has no audience to moderate).
_MODERATION_STATE: dict[ModerationKind, AnnotationState | None] = {
    ModerationKind.HIDE: AnnotationState.HIDDEN,
    ModerationKind.UNHIDE: AnnotationState.ACTIVE,
    ModerationKind.RESOLVE: AnnotationState.RESOLVED,
    ModerationKind.REPORT: None,
}


@dataclass(frozen=True, slots=True)
class ModerationAction:
    """One recorded moderation action against an annotation (evidence row)."""

    moderation_id: str
    annotation_id: str
    organization_id: str
    kind: ModerationKind
    actor: Actor
    created_at: datetime
    reason: str | None = None

    def resulting_state(self) -> AnnotationState | None:
        return _MODERATION_STATE[self.kind]


def assert_moderatable(annotation: Annotation) -> None:
    """Reject moderation of a private note (only shared annotations have a moderation surface)."""
    if annotation.visibility is AnnotationVisibility.PRIVATE:
        raise AnnotationInvariantViolation(
            "private annotations have no moderation surface",
            code="annotation.moderation.private",
        )


@dataclass(frozen=True, slots=True)
class Thread:
    """A group of annotations sharing a ``thread_id`` (root + replies, FR-ANN-006)."""

    thread_id: str
    object_id: str
    root_annotation_id: str
    annotation_ids: tuple[str, ...] = field(default_factory=tuple)


def assert_reply_visibility(*, reply: AnnotationVisibility, parent: AnnotationVisibility) -> None:
    """Reject a reply broader than its parent (a public reply can't reveal a private parent)."""
    if _VISIBILITY_RANK[reply] > _VISIBILITY_RANK[parent]:
        raise VisibilityBroadeningRejected(reply.value, parent.value)


class AnnotationVisibilityPolicy:
    """Pure, deterministic read projection (FR-ANN-005): which annotations a viewer may see.

    The command/query bus authorizes the *capability* deny-by-default; this projection then filters
    individual annotations by their declared visibility and audience — server-side, never in the UI.
    The creator always sees their own annotations. ``public`` is visible to any tenant member;
    ``private`` only to the creator; ``team``/``workspace``/``editorial``/``moderation_only`` are
    scoped to the explicit ``audience_ids`` (team membership / editorial role / moderator set is
    resolved into the audience at the edge).
    """

    @staticmethod
    def can_view(annotation: Annotation, *, viewer_id: str) -> bool:
        if annotation.state is AnnotationState.DELETED:
            return False
        if viewer_id == annotation.creator.id:
            return True
        visibility = annotation.visibility
        if visibility is AnnotationVisibility.PUBLIC:
            return annotation.state not in (AnnotationState.HIDDEN,)
        if visibility is AnnotationVisibility.PRIVATE:
            return False
        return viewer_id in annotation.audience_ids


def project_visible(annotations: Iterable[Annotation], *, viewer_id: str) -> tuple[Annotation, ...]:
    """Filter ``annotations`` to those ``viewer_id`` may see (visibility projection, FR-ANN-005)."""
    return tuple(
        annotation
        for annotation in annotations
        if AnnotationVisibilityPolicy.can_view(annotation, viewer_id=viewer_id)
    )


def group_into_threads(annotations: Iterable[Annotation]) -> tuple[Thread, ...]:
    """Group annotations into threads by ``thread_id`` (root = the annotation with no parent)."""
    by_thread: dict[str, list[Annotation]] = {}
    for annotation in annotations:
        thread_id = annotation.thread_id or annotation.annotation_id
        by_thread.setdefault(thread_id, []).append(annotation)
    threads: list[Thread] = []
    for thread_id, members in sorted(by_thread.items()):
        ordered = sorted(members, key=lambda a: a.created_at)
        root = next((a for a in ordered if a.parent_annotation_id is None), ordered[0])
        threads.append(
            Thread(
                thread_id=thread_id,
                object_id=root.target.object_id,
                root_annotation_id=root.annotation_id,
                annotation_ids=tuple(a.annotation_id for a in ordered),
            )
        )
    return threads
