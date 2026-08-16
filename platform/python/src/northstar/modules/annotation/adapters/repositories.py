"""Annotation repositories (in-memory + SQLAlchemy) implementing :class:`AnnotationRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL RLS applies as defense-in-depth
(FR-POL-004). The ORIGINAL target (``source_revision_id`` + ``selectors``) is written once and never
updated by :meth:`update`, which persists only mutable state/visibility/audience and the separate
``current_remap`` projection (FR-ANN-003). No string interpolation of values.
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

from ..domain.model import (
    Annotation,
    AnnotationBody,
    AnnotationState,
    AnnotationTarget,
    AnnotationVisibility,
    BodyType,
    ModerationAction,
    Motivation,
)
from ..domain.remap import RemapResult
from ..domain.selectors import parse_selectors, selectors_to_dicts
from .tables import AnnotationTables


def _actor_ref(actor: Actor) -> dict[str, str | None]:
    return {"type": actor.type.value, "id": actor.id, "delegated_by": actor.delegated_by}


def _actor_from_ref(ref: dict[str, Any]) -> Actor:
    return Actor(type=ActorType(ref["type"]), id=ref["id"], delegated_by=ref.get("delegated_by"))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _annotation_values(annotation: Annotation) -> dict[str, Any]:
    target = annotation.target
    return {
        "annotation_id": annotation.annotation_id,
        "organization_id": annotation.organization_id,
        "object_id": target.object_id,
        "source_revision_id": target.source_revision_id,
        "motivation": annotation.motivation.value,
        "body_type": annotation.body.type.value,
        "body_content": annotation.body.content,
        "body_locale": annotation.body.locale,
        "selectors": selectors_to_dicts(target.selectors),
        "source_fingerprint": target.source_fingerprint,
        "visibility": annotation.visibility.value,
        "audience_ids": list(annotation.audience_ids),
        "state": annotation.state.value,
        "thread_id": annotation.thread_id,
        "parent_annotation_id": annotation.parent_annotation_id,
        "current_remap": target.current_remap.to_dict() if target.current_remap else None,
        "creator": _actor_ref(annotation.creator),
        "created_at": annotation.created_at,
        "policy_decision_id": annotation.policy_decision_id,
    }


def _annotation_from_row(row: Any) -> Annotation:  # noqa: ANN401 SQLAlchemy Row is dynamic
    remap = RemapResult.from_dict(row.current_remap) if row.current_remap else None
    target = AnnotationTarget(
        object_id=row.object_id,
        source_revision_id=row.source_revision_id,
        selectors=parse_selectors(row.selectors),
        source_fingerprint=row.source_fingerprint,
        current_remap=remap,
    )
    return Annotation(
        annotation_id=row.annotation_id,
        organization_id=row.organization_id,
        motivation=Motivation(row.motivation),
        body=AnnotationBody(
            type=BodyType(row.body_type), content=row.body_content, locale=row.body_locale
        ),
        target=target,
        visibility=AnnotationVisibility(row.visibility),
        creator=_actor_from_ref(row.creator),
        created_at=_aware(row.created_at),
        state=AnnotationState(row.state),
        audience_ids=tuple(row.audience_ids or ()),
        thread_id=row.thread_id,
        parent_annotation_id=row.parent_annotation_id,
        policy_decision_id=row.policy_decision_id,
    )


class InMemoryAnnotationRepository:
    """In-memory repository for fast, deterministic unit tests."""

    def __init__(self) -> None:
        self._annotations: dict[str, Annotation] = {}
        self.moderations: list[ModerationAction] = []

    def add(self, annotation: Annotation) -> None:
        self._annotations[annotation.annotation_id] = annotation

    def get(self, *, organization_id: str, annotation_id: str) -> Annotation | None:
        annotation = self._annotations.get(annotation_id)
        if annotation is None or annotation.organization_id != organization_id:
            return None
        return annotation

    def update(self, annotation: Annotation) -> None:
        existing = self.get(
            organization_id=annotation.organization_id, annotation_id=annotation.annotation_id
        )
        if existing is None:
            return
        self._annotations[annotation.annotation_id] = annotation

    def list_for_target(self, *, organization_id: str, object_id: str) -> Sequence[Annotation]:
        return [
            annotation
            for annotation in self._annotations.values()
            if annotation.organization_id == organization_id
            and annotation.target.object_id == object_id
        ]

    def add_moderation(self, action: ModerationAction) -> None:
        self.moderations.append(action)


class SqlAlchemyAnnotationRepository:
    """PostgreSQL repository; scoped queries filter by ``organization_id`` and set the GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: AnnotationTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add(self, annotation: Annotation) -> None:
        table = self._tables.annotation
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, annotation.organization_id)
            uow.session.execute(insert(table).values(**_annotation_values(annotation)))
            uow.commit()

    def get(self, *, organization_id: str, annotation_id: str) -> Annotation | None:
        table = self._tables.annotation
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.annotation_id == annotation_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return _annotation_from_row(row)

    def update(self, annotation: Annotation) -> None:
        table = self._tables.annotation
        target = annotation.target
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, annotation.organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.annotation_id == annotation.annotation_id,
                    table.c.organization_id == annotation.organization_id,
                )
                .values(
                    visibility=annotation.visibility.value,
                    audience_ids=list(annotation.audience_ids),
                    state=annotation.state.value,
                    current_remap=(
                        target.current_remap.to_dict() if target.current_remap else None
                    ),
                    policy_decision_id=annotation.policy_decision_id,
                )
            )
            uow.commit()

    def list_for_target(self, *, organization_id: str, object_id: str) -> Sequence[Annotation]:
        table = self._tables.annotation
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.object_id == object_id,
                    table.c.organization_id == organization_id,
                )
            ).all()
        return [_annotation_from_row(row) for row in rows]

    def add_moderation(self, action: ModerationAction) -> None:
        table = self._tables.moderation
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, action.organization_id)
            uow.session.execute(
                insert(table).values(
                    moderation_id=action.moderation_id,
                    annotation_id=action.annotation_id,
                    organization_id=action.organization_id,
                    kind=action.kind.value,
                    reason=action.reason,
                    actor=_actor_ref(action.actor),
                    created_at=action.created_at,
                )
            )
            uow.commit()
