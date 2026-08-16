"""Analytics repositories (in-memory + SQLAlchemy) implementing :class:`AnalyticsRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL FORCED RLS applies as
defense-in-depth. No string interpolation of values.

First-party events are persisted as the AUTHORITATIVE analytics source (FR-ANL-001/002); registering
an event definition rejects an already-registered ``(event_name, version)`` so the catalog is
immutable (FR-ANL-003); identity stitches are idempotent on ``(anonymous_id, user_id)``
(FR-ANL-004).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.errors import DefinitionAlreadyRegistered
from ..domain.model import (
    AnalyticsEvent,
    AnalyticsEventDefinition,
    ConsentCategory,
    IdentityStitch,
    PropertySpec,
)
from .tables import AnalyticsTables


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _definition_to_row(
    organization_id: str, definition: AnalyticsEventDefinition
) -> dict[str, object]:
    return {
        "organization_id": organization_id,
        "event_name": definition.event_name,
        "version": definition.version,
        "owner": definition.owner,
        "purpose": definition.purpose,
        "consent_category": definition.consent_category.value,
        "retention_days": definition.retention_days,
        "destinations": list(definition.destinations),
        "properties": {name: spec.to_dict() for name, spec in definition.properties.items()},
        "trigger": definition.trigger,
        "sampling": (str(definition.sampling) if definition.sampling is not None else None),
        "created_at": _now(),
    }


def _row_to_definition(row: object) -> AnalyticsEventDefinition:
    properties = {
        str(name): PropertySpec.from_dict(spec) for name, spec in dict(row.properties or {}).items()
    }
    return AnalyticsEventDefinition(
        event_name=row.event_name,
        version=row.version,
        owner=row.owner,
        purpose=row.purpose,
        consent_category=ConsentCategory(row.consent_category),
        retention_days=row.retention_days,
        destinations=tuple(row.destinations or ()),
        properties=properties,
        trigger=row.trigger,
        sampling=(float(row.sampling) if row.sampling is not None else None),
    )


def _row_to_event(row: object) -> AnalyticsEvent:
    return AnalyticsEvent(
        event_name=row.event_name,
        event_version=row.event_version,
        occurred_at=_aware(row.occurred_at),
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        properties=dict(row.properties or {}),
        anonymous_id=row.anonymous_id,
        event_id=row.event_id,
    )


# ---------------------------------------------------------------------------
# In-memory repository (fast, deterministic unit/security tests)
# ---------------------------------------------------------------------------


class InMemoryAnalyticsRepository:
    """In-memory, tenant-scoped repository for fast, deterministic tests."""

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str, int], AnalyticsEventDefinition] = {}
        self._events: dict[tuple[str, str], AnalyticsEvent] = {}
        self._stitches: dict[tuple[str, str, str], IdentityStitch] = {}

    def add_definition(self, *, organization_id: str, definition: AnalyticsEventDefinition) -> None:
        key = (organization_id, definition.event_name, definition.version)
        if key in self._definitions:
            raise DefinitionAlreadyRegistered(definition.event_name, definition.version)
        self._definitions[key] = definition

    def get_definition(
        self, *, organization_id: str, event_name: str
    ) -> AnalyticsEventDefinition | None:
        matches = [
            definition
            for (org, name, _v), definition in self._definitions.items()
            if org == organization_id and name == event_name
        ]
        if not matches:
            return None
        return max(matches, key=lambda d: d.version)

    def list_definitions(self, *, organization_id: str) -> Sequence[AnalyticsEventDefinition]:
        return [
            definition
            for (org, _name, _v), definition in self._definitions.items()
            if org == organization_id
        ]

    def record_event(self, *, organization_id: str, event: AnalyticsEvent) -> None:
        self._events[(organization_id, event.event_id or "")] = event

    def list_events(self, *, organization_id: str, event_name: str) -> Sequence[AnalyticsEvent]:
        return [
            event
            for (org, _eid), event in self._events.items()
            if org == organization_id and event.event_name == event_name
        ]

    def add_stitch(self, *, organization_id: str, stitch: IdentityStitch) -> None:
        self._stitches[(organization_id, stitch.anonymous_id, stitch.user_id)] = stitch

    def list_stitches(self, *, organization_id: str) -> Sequence[IdentityStitch]:
        return [
            stitch for (org, _a, _u), stitch in self._stitches.items() if org == organization_id
        ]


# ---------------------------------------------------------------------------
# SQLAlchemy repository (PostgreSQL; RLS-forced schema)
# ---------------------------------------------------------------------------


class SqlAlchemyAnalyticsRepository:
    """PostgreSQL repository; every query filters by ``organization_id`` and sets the tenant GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: AnalyticsTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    # Event catalog ------------------------------------------------------
    def add_definition(self, *, organization_id: str, definition: AnalyticsEventDefinition) -> None:
        table = self._tables.event_definition
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.version).where(
                    table.c.organization_id == organization_id,
                    table.c.event_name == definition.event_name,
                    table.c.version == definition.version,
                )
            ).first()
            if existing is not None:
                raise DefinitionAlreadyRegistered(definition.event_name, definition.version)
            session.execute(insert(table).values(**_definition_to_row(organization_id, definition)))
            uow.commit()

    def get_definition(
        self, *, organization_id: str, event_name: str
    ) -> AnalyticsEventDefinition | None:
        table = self._tables.event_definition
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.event_name == event_name,
                )
                .order_by(table.c.version.desc())
            ).first()
        return None if row is None else _row_to_definition(row)

    def list_definitions(self, *, organization_id: str) -> Sequence[AnalyticsEventDefinition]:
        table = self._tables.event_definition
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(table.c.organization_id == organization_id)
            ).all()
        return [_row_to_definition(row) for row in rows]

    # First-party events (authoritative) --------------------------------
    def record_event(self, *, organization_id: str, event: AnalyticsEvent) -> None:
        table = self._tables.event
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    event_id=event.event_id,
                    event_name=event.event_name,
                    event_version=event.event_version,
                    actor_type=event.actor_type,
                    actor_id=event.actor_id,
                    anonymous_id=event.anonymous_id,
                    occurred_at=_aware(event.occurred_at),
                    properties=dict(event.properties),
                    created_at=_now(),
                )
            )
            uow.commit()

    def list_events(self, *, organization_id: str, event_name: str) -> Sequence[AnalyticsEvent]:
        table = self._tables.event
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.event_name == event_name,
                )
            ).all()
        return [_row_to_event(row) for row in rows]

    # Identity stitching -------------------------------------------------
    def add_stitch(self, *, organization_id: str, stitch: IdentityStitch) -> None:
        table = self._tables.identity_stitch
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.anonymous_id).where(
                    table.c.organization_id == organization_id,
                    table.c.anonymous_id == stitch.anonymous_id,
                    table.c.user_id == stitch.user_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        anonymous_id=stitch.anonymous_id,
                        user_id=stitch.user_id,
                        consent_category=stitch.consent_category,
                        created_at=_aware(stitch.created_at),
                    )
                )
            uow.commit()

    def list_stitches(self, *, organization_id: str) -> Sequence[IdentityStitch]:
        table = self._tables.identity_stitch
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(table.c.organization_id == organization_id)
            ).all()
        return [
            IdentityStitch(
                anonymous_id=row.anonymous_id,
                user_id=row.user_id,
                consent_category=row.consent_category,
                created_at=_aware(row.created_at),
            )
            for row in rows
        ]


__all__ = [
    "InMemoryAnalyticsRepository",
    "SqlAlchemyAnalyticsRepository",
]
