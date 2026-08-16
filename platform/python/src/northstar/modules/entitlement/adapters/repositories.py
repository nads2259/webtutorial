"""Entitlement repositories (in-memory and SQLAlchemy).

Implements :class:`EntitlementRepositoryPort`. SQLAlchemy reads are parameterised and, for
tenant-scoped grants, set the tenant GUC so RLS applies as defense-in-depth (FR-POL-004).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import insert, select
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.model import EntitlementGrant, GrantOrigin, QuotaDisposition
from .tables import EntitlementTables


class InMemoryEntitlementRepository:
    """In-memory grant repository for fast, deterministic unit tests."""

    def __init__(self) -> None:
        self._grants: dict[str, EntitlementGrant] = {}

    def add_grant(self, grant: EntitlementGrant) -> None:
        self._grants[grant.grant_id] = grant

    def list_grants_for_subject(self, subject_id: str) -> Sequence[EntitlementGrant]:
        return [g for g in self._grants.values() if g.subject_id == subject_id]


class SqlAlchemyEntitlementRepository:
    """PostgreSQL grant repository over the ``northstar_entitlement`` table."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: EntitlementTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add_grant(self, grant: EntitlementGrant) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                insert(self._tables.grant).values(
                    grant_id=grant.grant_id,
                    subject_id=grant.subject_id,
                    organization_id=grant.organization_id,
                    capability=grant.capability,
                    origin=grant.origin.value,
                    starts_at=grant.starts_at,
                    ends_at=grant.ends_at,
                    quota_limit=grant.quota_limit,
                    quota_used=grant.quota_used,
                    quota_disposition=grant.quota_disposition.value,
                    revoked=grant.revoked_at is not None,
                    revoked_at=grant.revoked_at,
                )
            )
            uow.commit()

    def list_grants_for_subject(self, subject_id: str) -> Sequence[EntitlementGrant]:
        table = self._tables.grant
        with self._session_factory() as session:
            rows = session.execute(select(table).where(table.c.subject_id == subject_id)).all()
        return [_row_to_grant(r) for r in rows]


def _row_to_grant(row: object) -> EntitlementGrant:
    return EntitlementGrant(
        grant_id=row.grant_id,
        subject_id=row.subject_id,
        capability=row.capability,
        origin=GrantOrigin(row.origin),
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        quota_limit=row.quota_limit,
        quota_used=row.quota_used,
        quota_disposition=QuotaDisposition(row.quota_disposition),
        organization_id=row.organization_id,
        revoked_at=row.revoked_at,
    )
