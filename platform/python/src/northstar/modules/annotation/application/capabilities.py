"""Annotation capabilities: one authoritative implementation per action (LAW-04).

Each handler runs through the kernel command/query bus, so every mutation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope is taken from the authenticated
:class:`RequestContext` (``tenant_scope``), **never** from the payload (rule 50). Remapping
delegates to the pure :class:`~northstar.modules.annotation.domain.remap.Remapper`; ambiguous or
orphaned targets are routed to a review state and the ORIGINAL target is never moved (FR-ANN-004).
Visibility on read is projected server-side by the pure visibility policy (FR-ANN-005). Handlers
depend only on :mod:`.ports` and the pure :mod:`..domain`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from northstar.kernel.context import Actor

from ..domain.errors import AnnotationInvariantViolation, AnnotationNotFound, TenantScopeMissing
from ..domain.model import (
    Annotation,
    AnnotationBody,
    AnnotationState,
    AnnotationTarget,
    AnnotationVisibility,
    BodyType,
    ModerationAction,
    ModerationKind,
    Motivation,
    assert_moderatable,
    assert_reply_visibility,
    group_into_threads,
    project_visible,
)
from ..domain.remap import Remapper
from ..domain.selectors import parse_selectors
from .ports import AnnotationRepositoryPort, RevisionSnapshotProviderPort

CAP_VERSION = "1.0.0"

CAP_CREATE_ANNOTATION = "annotation.annotation.create"
CAP_REPLY = "annotation.annotation.reply"
CAP_SET_VISIBILITY = "annotation.annotation.set-visibility"
CAP_MODERATE = "annotation.annotation.moderate"
CAP_REMAP = "annotation.annotation.remap"
CAP_LIST_FOR_TARGET = "annotation.annotation.list"

ANNOTATION_CAPABILITIES: tuple[str, ...] = (
    CAP_CREATE_ANNOTATION,
    CAP_REPLY,
    CAP_SET_VISIBILITY,
    CAP_MODERATE,
    CAP_REMAP,
    CAP_LIST_FOR_TARGET,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateAnnotationCommand:
    object_id: str
    revision_id: str
    selectors: tuple[dict[str, Any], ...]
    motivation: str
    visibility: str
    body_type: str
    body_content: Any
    body_locale: str | None = None
    audience_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CreateAnnotationResult:
    annotation_id: str
    thread_id: str
    state: str


@dataclass(frozen=True, slots=True)
class ReplyCommand:
    parent_annotation_id: str
    motivation: str
    visibility: str
    body_type: str
    body_content: Any
    body_locale: str | None = None
    audience_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReplyResult:
    annotation_id: str
    thread_id: str
    parent_annotation_id: str


@dataclass(frozen=True, slots=True)
class SetVisibilityCommand:
    annotation_id: str
    visibility: str
    audience_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SetVisibilityResult:
    annotation_id: str
    visibility: str


@dataclass(frozen=True, slots=True)
class ModerateCommand:
    annotation_id: str
    kind: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ModerateResult:
    annotation_id: str
    moderation_id: str
    state: str


@dataclass(frozen=True, slots=True)
class RemapOnNewRevisionCommand:
    annotation_id: str
    new_revision_id: str


@dataclass(frozen=True, slots=True)
class RemapOnNewRevisionResult:
    annotation_id: str
    strategy: str
    confidence: float
    mapped: bool
    state: str
    current_revision_id: str
    review_reason: str | None


@dataclass(frozen=True, slots=True)
class ListForTargetQuery:
    object_id: str


@dataclass(frozen=True, slots=True)
class AnnotationView:
    annotation_id: str
    motivation: str
    visibility: str
    state: str
    object_id: str
    source_revision_id: str
    current_revision_id: str
    thread_id: str | None
    parent_annotation_id: str | None


@dataclass(frozen=True, slots=True)
class ThreadView:
    thread_id: str
    root_annotation_id: str
    annotation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ListForTargetResult:
    annotations: tuple[AnnotationView, ...]
    threads: tuple[ThreadView, ...]


# ---------------------------------------------------------------------------
# Shared helpers
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


def _actor(invocation: object) -> Actor:
    context = getattr(invocation, "context", None)
    return context.actor


def _enum[EnumT](enum_type: type[EnumT], value: str, code: str) -> EnumT:
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except ValueError as exc:
        raise AnnotationInvariantViolation(
            f"invalid {enum_type.__name__} {value!r}", code=code
        ) from exc


def _load(
    repo: AnnotationRepositoryPort, *, organization_id: str, annotation_id: str
) -> Annotation:
    annotation = repo.get(organization_id=organization_id, annotation_id=annotation_id)
    if annotation is None:
        raise AnnotationNotFound()
    return annotation


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class CreateAnnotation:
    """``annotation.annotation.create`` — anchor a new annotation to a content target."""

    def __init__(
        self, *, repository: AnnotationRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> CreateAnnotationResult:
        command = _typed(request, CreateAnnotationCommand)
        organization_id = _tenant(request)
        selectors = parse_selectors(list(command.selectors))
        body = AnnotationBody(
            type=_enum(BodyType, command.body_type, "annotation.body.type"),
            content=command.body_content,
            locale=command.body_locale,
        )
        target = AnnotationTarget(
            object_id=command.object_id,
            source_revision_id=command.revision_id,
            selectors=selectors,
        )
        annotation_id = self._id_factory()
        thread_id = self._id_factory()
        annotation = Annotation(
            annotation_id=annotation_id,
            organization_id=organization_id,
            motivation=_enum(Motivation, command.motivation, "annotation.motivation"),
            body=body,
            target=target,
            visibility=_enum(AnnotationVisibility, command.visibility, "annotation.visibility"),
            creator=_actor(request),
            created_at=self._clock(),
            state=AnnotationState.ACTIVE,
            audience_ids=tuple(command.audience_ids),
            thread_id=thread_id,
            parent_annotation_id=None,
        )
        self._repo.add(annotation)
        return CreateAnnotationResult(
            annotation_id=annotation_id, thread_id=thread_id, state=annotation.state.value
        )


class Reply:
    """``annotation.annotation.reply`` — add a reply to a thread without losing target context."""

    def __init__(
        self, *, repository: AnnotationRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> ReplyResult:
        command = _typed(request, ReplyCommand)
        organization_id = _tenant(request)
        parent = _load(
            self._repo,
            organization_id=organization_id,
            annotation_id=command.parent_annotation_id,
        )
        visibility = _enum(AnnotationVisibility, command.visibility, "annotation.visibility")
        assert_reply_visibility(reply=visibility, parent=parent.visibility)
        body = AnnotationBody(
            type=_enum(BodyType, command.body_type, "annotation.body.type"),
            content=command.body_content,
            locale=command.body_locale,
        )
        thread_id = parent.thread_id or parent.annotation_id
        annotation_id = self._id_factory()
        # A reply inherits the parent's ORIGINAL target context (FR-ANN-006).
        target = AnnotationTarget(
            object_id=parent.target.object_id,
            source_revision_id=parent.target.source_revision_id,
            selectors=parent.target.selectors,
            source_fingerprint=parent.target.source_fingerprint,
        )
        reply = Annotation(
            annotation_id=annotation_id,
            organization_id=organization_id,
            motivation=_enum(Motivation, command.motivation, "annotation.motivation"),
            body=body,
            target=target,
            visibility=visibility,
            creator=_actor(request),
            created_at=self._clock(),
            state=AnnotationState.ACTIVE,
            audience_ids=tuple(command.audience_ids),
            thread_id=thread_id,
            parent_annotation_id=parent.annotation_id,
        )
        self._repo.add(reply)
        return ReplyResult(
            annotation_id=annotation_id,
            thread_id=thread_id,
            parent_annotation_id=parent.annotation_id,
        )


class SetVisibility:
    """``annotation.annotation.set-visibility`` — change visibility (policy-enforced scope)."""

    def __init__(self, *, repository: AnnotationRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> SetVisibilityResult:
        command = _typed(request, SetVisibilityCommand)
        organization_id = _tenant(request)
        annotation = _load(
            self._repo, organization_id=organization_id, annotation_id=command.annotation_id
        )
        visibility = _enum(AnnotationVisibility, command.visibility, "annotation.visibility")
        if annotation.parent_annotation_id is not None:
            parent = _load(
                self._repo,
                organization_id=organization_id,
                annotation_id=annotation.parent_annotation_id,
            )
            assert_reply_visibility(reply=visibility, parent=parent.visibility)
        updated = annotation.with_visibility(visibility)
        if command.audience_ids is not None:
            updated = replace(updated, audience_ids=tuple(command.audience_ids))
        self._repo.update(updated)
        return SetVisibilityResult(
            annotation_id=updated.annotation_id, visibility=updated.visibility.value
        )


class Moderate:
    """``annotation.annotation.moderate`` — reversible moderation hook for shared annotations."""

    def __init__(
        self, *, repository: AnnotationRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> ModerateResult:
        command = _typed(request, ModerateCommand)
        organization_id = _tenant(request)
        annotation = _load(
            self._repo, organization_id=organization_id, annotation_id=command.annotation_id
        )
        assert_moderatable(annotation)
        kind = _enum(ModerationKind, command.kind, "annotation.moderation.kind")
        action = ModerationAction(
            moderation_id=self._id_factory(),
            annotation_id=annotation.annotation_id,
            organization_id=organization_id,
            kind=kind,
            actor=_actor(request),
            created_at=self._clock(),
            reason=command.reason,
        )
        self._repo.add_moderation(action)
        new_state = action.resulting_state()
        if new_state is not None:
            annotation = annotation.with_state(new_state)
            self._repo.update(annotation)
        return ModerateResult(
            annotation_id=annotation.annotation_id,
            moderation_id=action.moderation_id,
            state=annotation.state.value,
        )


class RemapOnNewRevision:
    """``annotation.annotation.remap`` — deterministically remap onto a new revision (FR-ANN-004).

    Routes ambiguous/orphaned targets to a review state and NEVER moves the original target.
    """

    def __init__(
        self,
        *,
        repository: AnnotationRepositoryPort,
        snapshots: RevisionSnapshotProviderPort,
        remapper: Remapper,
    ) -> None:
        self._repo = repository
        self._snapshots = snapshots
        self._remapper = remapper

    def handle(self, request: object) -> RemapOnNewRevisionResult:
        command = _typed(request, RemapOnNewRevisionCommand)
        organization_id = _tenant(request)
        annotation = _load(
            self._repo, organization_id=organization_id, annotation_id=command.annotation_id
        )
        source = self._snapshots.snapshot(
            organization_id=organization_id, revision_id=annotation.target.source_revision_id
        )
        destination = self._snapshots.snapshot(
            organization_id=organization_id, revision_id=command.new_revision_id
        )
        if source is None or destination is None:
            raise AnnotationInvariantViolation(
                "a source and destination revision are required to remap",
                code="annotation.revision.not_found",
            )
        result = self._remapper.remap(
            selectors=annotation.target.selectors, source=source, destination=destination
        )
        updated = annotation.with_remap(result)
        self._repo.update(updated)
        return RemapOnNewRevisionResult(
            annotation_id=updated.annotation_id,
            strategy=result.strategy.value,
            confidence=result.confidence,
            mapped=result.mapped,
            state=updated.state.value,
            current_revision_id=updated.target.current_revision_id,
            review_reason=result.review_reason.value if result.review_reason else None,
        )


class ListForTarget:
    """``annotation.annotation.list`` (query) — list visible annotations for a content target."""

    def __init__(self, *, repository: AnnotationRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ListForTargetResult:
        query = _typed(request, ListForTargetQuery)
        organization_id = _tenant(request)
        viewer_id = _actor(request).id
        items = self._repo.list_for_target(
            organization_id=organization_id, object_id=query.object_id
        )
        visible = project_visible(items, viewer_id=viewer_id)
        threads = group_into_threads(visible)
        return ListForTargetResult(
            annotations=tuple(_view(annotation) for annotation in visible),
            threads=tuple(
                ThreadView(
                    thread_id=thread.thread_id,
                    root_annotation_id=thread.root_annotation_id,
                    annotation_ids=thread.annotation_ids,
                )
                for thread in threads
            ),
        )


def _view(annotation: Annotation) -> AnnotationView:
    return AnnotationView(
        annotation_id=annotation.annotation_id,
        motivation=annotation.motivation.value,
        visibility=annotation.visibility.value,
        state=annotation.state.value,
        object_id=annotation.target.object_id,
        source_revision_id=annotation.target.source_revision_id,
        current_revision_id=annotation.target.current_revision_id,
        thread_id=annotation.thread_id,
        parent_annotation_id=annotation.parent_annotation_id,
    )
