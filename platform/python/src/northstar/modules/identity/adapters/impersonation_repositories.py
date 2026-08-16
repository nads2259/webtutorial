"""Impersonation/break-glass repositories (in-memory + SQLAlchemy) — FR-IDN-007/008.

Both implement :class:`ImpersonationRepositoryPort`. Every SQLAlchemy read/write is parameterised
and filtered by ``tenant_scope`` (rule 50, tenant isolation) and sets the per-transaction tenant GUC
so PostgreSQL FORCED Row-Level Security (migration 000022) applies as defense-in-depth. Writes go
through the kernel unit of work so they are transactional.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..application.impersonation import ImpersonationRepositoryPort
from ..domain.impersonation import (
    BreakGlassAccess,
    ImpersonationGrant,
    PostUseReview,
    ReviewStatus,
)
from .tables import IdentityTables


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# In-memory repository (fast, deterministic unit/security tests)
# ---------------------------------------------------------------------------


class InMemoryImpersonationRepository(ImpersonationRepositoryPort):
    """In-memory, tenant-scoped impersonation/break-glass repository for deterministic tests."""

    def __init__(self) -> None:
        self._grants: dict[tuple[str, str], ImpersonationGrant] = {}
        self._accesses: dict[tuple[str, str], BreakGlassAccess] = {}
        self._reviews: dict[tuple[str, str], PostUseReview] = {}

    def add_impersonation(self, *, tenant_scope: str, grant: ImpersonationGrant) -> None:
        self._grants[(tenant_scope, grant.grant_id)] = grant

    def get_impersonation(self, *, tenant_scope: str, grant_id: str) -> ImpersonationGrant | None:
        return self._grants.get((tenant_scope, grant_id))

    def save_impersonation(self, *, tenant_scope: str, grant: ImpersonationGrant) -> None:
        self._grants[(tenant_scope, grant.grant_id)] = grant

    def add_break_glass(self, *, tenant_scope: str, access: BreakGlassAccess) -> None:
        self._accesses[(tenant_scope, access.access_id)] = access

    def add_post_use_review(self, *, tenant_scope: str, review: PostUseReview) -> None:
        self._reviews[(tenant_scope, review.review_id)] = review

    def get_post_use_review(self, *, tenant_scope: str, review_id: str) -> PostUseReview | None:
        return self._reviews.get((tenant_scope, review_id))

    def save_post_use_review(self, *, tenant_scope: str, review: PostUseReview) -> None:
        self._reviews[(tenant_scope, review.review_id)] = review

    def pending_reviews(self, *, tenant_scope: str) -> tuple[PostUseReview, ...]:
        return tuple(
            r
            for (tenant, _rid), r in self._reviews.items()
            if tenant == tenant_scope and r.is_pending
        )


# ---------------------------------------------------------------------------
# SQLAlchemy repository (PostgreSQL; RLS-forced schema)
# ---------------------------------------------------------------------------


class SqlAlchemyImpersonationRepository(ImpersonationRepositoryPort):
    """PostgreSQL repository; every query filters by ``tenant_scope`` + sets the tenant GUC."""

    def __init__(self, *, session_factory: sessionmaker[SaSession], tables: IdentityTables) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add_impersonation(self, *, tenant_scope: str, grant: ImpersonationGrant) -> None:
        table = self._tables.impersonation_grant
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, tenant_scope)
            uow.session.execute(
                insert(table).values(
                    grant_id=grant.grant_id,
                    tenant_scope=tenant_scope,
                    real_actor_id=grant.real_actor_id,
                    impersonated_subject_id=grant.impersonated_subject_id,
                    reason=grant.reason,
                    started_at=_aware(grant.started_at),
                    expires_at=_aware(grant.expires_at),
                    approved_by=grant.approved_by,
                    ended_at=(_aware(grant.ended_at) if grant.ended_at else None),
                )
            )
            uow.commit()

    def get_impersonation(self, *, tenant_scope: str, grant_id: str) -> ImpersonationGrant | None:
        table = self._tables.impersonation_grant
        with self._session_factory() as session:
            set_tenant_guc(session, tenant_scope)
            row = session.execute(
                select(table).where(
                    table.c.tenant_scope == tenant_scope, table.c.grant_id == grant_id
                )
            ).first()
        return None if row is None else _row_to_grant(row)

    def save_impersonation(self, *, tenant_scope: str, grant: ImpersonationGrant) -> None:
        table = self._tables.impersonation_grant
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, tenant_scope)
            uow.session.execute(
                update(table)
                .where(table.c.tenant_scope == tenant_scope, table.c.grant_id == grant.grant_id)
                .values(ended_at=(_aware(grant.ended_at) if grant.ended_at else None))
            )
            uow.commit()

    def add_break_glass(self, *, tenant_scope: str, access: BreakGlassAccess) -> None:
        table = self._tables.break_glass_access
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, tenant_scope)
            uow.session.execute(
                insert(table).values(
                    access_id=access.access_id,
                    tenant_scope=tenant_scope,
                    operator_id=access.operator_id,
                    justification=access.justification,
                    severity=access.severity,
                    invoked_at=_aware(access.invoked_at),
                    expires_at=_aware(access.expires_at),
                    authorized_by=access.authorized_by,
                )
            )
            uow.commit()

    def add_post_use_review(self, *, tenant_scope: str, review: PostUseReview) -> None:
        table = self._tables.post_use_review
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, tenant_scope)
            uow.session.execute(insert(table).values(**_review_values(tenant_scope, review)))
            uow.commit()

    def get_post_use_review(self, *, tenant_scope: str, review_id: str) -> PostUseReview | None:
        table = self._tables.post_use_review
        with self._session_factory() as session:
            set_tenant_guc(session, tenant_scope)
            row = session.execute(
                select(table).where(
                    table.c.tenant_scope == tenant_scope, table.c.review_id == review_id
                )
            ).first()
        return None if row is None else _row_to_review(row)

    def save_post_use_review(self, *, tenant_scope: str, review: PostUseReview) -> None:
        table = self._tables.post_use_review
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, tenant_scope)
            uow.session.execute(
                update(table)
                .where(table.c.tenant_scope == tenant_scope, table.c.review_id == review.review_id)
                .values(
                    status=review.status.value,
                    resolved_at=(_aware(review.resolved_at) if review.resolved_at else None),
                    resolved_by=review.resolved_by,
                    resolution=review.resolution,
                )
            )
            uow.commit()


# ---------------------------------------------------------------------------
# Row/value mappers
# ---------------------------------------------------------------------------


def _row_to_grant(row: object) -> ImpersonationGrant:
    return ImpersonationGrant(
        grant_id=row.grant_id,
        real_actor_id=row.real_actor_id,
        impersonated_subject_id=row.impersonated_subject_id,
        reason=row.reason,
        started_at=_aware(row.started_at),
        expires_at=_aware(row.expires_at),
        tenant_scope=row.tenant_scope,
        approved_by=row.approved_by,
        ended_at=(_aware(row.ended_at) if row.ended_at else None),
    )


def _review_values(tenant_scope: str, review: PostUseReview) -> dict[str, object]:
    return {
        "review_id": review.review_id,
        "tenant_scope": tenant_scope,
        "access_id": review.access_id,
        "status": review.status.value,
        "opened_at": _aware(review.opened_at),
        "resolved_at": (_aware(review.resolved_at) if review.resolved_at else None),
        "resolved_by": review.resolved_by,
        "resolution": review.resolution,
    }


def _row_to_review(row: object) -> PostUseReview:
    return PostUseReview(
        review_id=row.review_id,
        access_id=row.access_id,
        opened_at=_aware(row.opened_at),
        tenant_scope=row.tenant_scope,
        status=ReviewStatus(row.status),
        resolved_at=(_aware(row.resolved_at) if row.resolved_at else None),
        resolved_by=row.resolved_by,
        resolution=row.resolution,
    )


__all__ = [
    "InMemoryImpersonationRepository",
    "SqlAlchemyImpersonationRepository",
]
