"""Media repositories (in-memory + SQLAlchemy) implementing :class:`MediaRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL RLS applies as defense-in-depth
(FR-POL-004). Accessible alternatives (transcript, caption tracks) are serialised to/from JSONB via
the pure domain value objects; no string interpolation of values.
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

from ..domain.model import CaptionTrack, MediaAsset, MediaState, MediaType, Transcript
from .tables import MediaTables


def _actor_ref(actor: Actor) -> dict[str, str | None]:
    return {"type": actor.type.value, "id": actor.id, "delegated_by": actor.delegated_by}


def _actor_from_ref(ref: dict[str, Any]) -> Actor:
    return Actor(type=ActorType(ref["type"]), id=ref["id"], delegated_by=ref.get("delegated_by"))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _asset_values(asset: MediaAsset) -> dict[str, Any]:
    return {
        "asset_id": asset.asset_id,
        "organization_id": asset.organization_id,
        "media_type": asset.media_type.value,
        "content_type": asset.content_type,
        "blob_ref": asset.blob_ref,
        "byte_size": asset.byte_size,
        "title": asset.title,
        "state": asset.state.value,
        "transcript": asset.transcript.to_dict() if asset.transcript is not None else None,
        "captions": [track.to_dict() for track in asset.captions],
        "alt_text": asset.alt_text,
        "decorative": asset.decorative,
        "duration_seconds": asset.duration_seconds,
        "created_by": _actor_ref(asset.created_by),
        "created_at": asset.created_at,
        "policy_decision_id": asset.policy_decision_id,
    }


def _asset_from_row(row: Any) -> MediaAsset:  # noqa: ANN401 SQLAlchemy Row is dynamic
    transcript = Transcript.from_dict(row.transcript) if row.transcript else None
    captions = tuple(CaptionTrack.from_dict(item) for item in (row.captions or ()))
    return MediaAsset(
        asset_id=row.asset_id,
        organization_id=row.organization_id,
        media_type=MediaType(row.media_type),
        content_type=row.content_type,
        blob_ref=row.blob_ref,
        byte_size=row.byte_size,
        created_by=_actor_from_ref(row.created_by),
        created_at=_aware(row.created_at),
        state=MediaState(row.state),
        title=row.title,
        transcript=transcript,
        captions=captions,
        alt_text=row.alt_text,
        decorative=bool(row.decorative),
        duration_seconds=row.duration_seconds,
        policy_decision_id=row.policy_decision_id,
    )


class InMemoryMediaRepository:
    """In-memory repository for fast, deterministic unit tests."""

    def __init__(self) -> None:
        self._assets: dict[str, MediaAsset] = {}

    def add(self, asset: MediaAsset) -> None:
        self._assets[asset.asset_id] = asset

    def get(self, *, organization_id: str, asset_id: str) -> MediaAsset | None:
        asset = self._assets.get(asset_id)
        if asset is None or asset.organization_id != organization_id:
            return None
        return asset

    def update(self, asset: MediaAsset) -> None:
        existing = self.get(organization_id=asset.organization_id, asset_id=asset.asset_id)
        if existing is None:
            return
        self._assets[asset.asset_id] = asset

    def list_for_org(self, *, organization_id: str) -> Sequence[MediaAsset]:
        return [a for a in self._assets.values() if a.organization_id == organization_id]


class SqlAlchemyMediaRepository:
    """PostgreSQL repository; scoped queries filter by ``organization_id`` and set the GUC."""

    def __init__(self, *, session_factory: sessionmaker[SaSession], tables: MediaTables) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add(self, asset: MediaAsset) -> None:
        table = self._tables.media_asset
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, asset.organization_id)
            uow.session.execute(insert(table).values(**_asset_values(asset)))
            uow.commit()

    def get(self, *, organization_id: str, asset_id: str) -> MediaAsset | None:
        table = self._tables.media_asset
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.asset_id == asset_id,
                    table.c.organization_id == organization_id,
                )
            ).first()
        if row is None:
            return None
        return _asset_from_row(row)

    def update(self, asset: MediaAsset) -> None:
        table = self._tables.media_asset
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, asset.organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.asset_id == asset.asset_id,
                    table.c.organization_id == asset.organization_id,
                )
                .values(
                    title=asset.title,
                    state=asset.state.value,
                    transcript=(
                        asset.transcript.to_dict() if asset.transcript is not None else None
                    ),
                    captions=[track.to_dict() for track in asset.captions],
                    alt_text=asset.alt_text,
                    decorative=asset.decorative,
                    duration_seconds=asset.duration_seconds,
                    policy_decision_id=asset.policy_decision_id,
                )
            )
            uow.commit()

    def list_for_org(self, *, organization_id: str) -> Sequence[MediaAsset]:
        table = self._tables.media_asset
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(table.c.organization_id == organization_id)
            ).all()
        return [_asset_from_row(row) for row in rows]
