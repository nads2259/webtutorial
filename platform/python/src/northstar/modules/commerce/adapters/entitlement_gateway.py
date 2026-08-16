"""Entitlement-engine gateway (FR-COM-001/002/004, ARCH-019).

Commerce does NOT re-implement entitlements. This adapter is the reference
:class:`EntitlementGrantPort` that REUSES the existing entitlement engine
(``northstar.modules.entitlement``): it constructs the
engine's :class:`EntitlementGrant` model, persists grants commerce owns for its purchases (LAW-13),
and answers entitlement *decisions* with the engine's authoritative :func:`decide` function. Refund
revocation flips the same grant's ``revoked_at`` so the SAME decision function returns deny.

The offer's structural ``billing_type`` is mapped to an entitlement grant ORIGIN TYPE here — the
commerce domain never learns and never branches on a plan or payment-provider name (ARCH-019):

* free       -> FREE_POLICY
* one_time   -> PURCHASE
* recurring  -> SUBSCRIPTION
* invoice    -> ENTERPRISE_AGREEMENT

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` and sets the
per-transaction tenant GUC so PostgreSQL FORCED RLS applies as defense-in-depth (rule 50).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from northstar.kernel.context import ResourceRef
from northstar.modules.entitlement.domain.model import (
    EntitlementGrant,
    GrantOrigin,
    QuotaDisposition,
    decide,
)

from ..domain.model import BillingType
from .tables import CommerceTables

# billing TYPE -> grant ORIGIN TYPE (never a plan/payment-provider name — ARCH-019).
_ORIGIN_BY_BILLING: dict[BillingType, GrantOrigin] = {
    BillingType.FREE: GrantOrigin.FREE_POLICY,
    BillingType.ONE_TIME: GrantOrigin.PURCHASE,
    BillingType.RECURRING: GrantOrigin.SUBSCRIPTION,
    BillingType.INVOICE: GrantOrigin.ENTERPRISE_AGREEMENT,
}

_DECISION_RESOURCE = ResourceRef(type="entitlement.capability", id="capability")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Commerce-owned entitlement grant store (in-memory + SQLAlchemy)
# ---------------------------------------------------------------------------


class InMemoryCommerceEntitlementRepository:
    """In-memory, tenant-scoped store of the entitlement grants commerce issues (fast tests)."""

    def __init__(self) -> None:
        self._grants: dict[tuple[str, str], EntitlementGrant] = {}

    def add_grant(self, *, organization_id: str, grant: EntitlementGrant) -> None:
        self._grants[(organization_id, grant.grant_id)] = grant

    def list_grants_for_subject(
        self, *, organization_id: str, subject_id: str
    ) -> Sequence[EntitlementGrant]:
        return [
            grant
            for (org, _gid), grant in self._grants.items()
            if org == organization_id and grant.subject_id == subject_id
        ]

    def revoke(self, *, organization_id: str, grant_id: str, now: datetime) -> bool:
        key = (organization_id, grant_id)
        grant = self._grants.get(key)
        if grant is None or grant.revoked_at is not None:
            return False  # idempotent: unknown or already-revoked grant makes no change
        self._grants[key] = _revoked(grant, now)
        return True


class SqlAlchemyCommerceEntitlementRepository:
    """PostgreSQL store for commerce-issued grants; every query is org-scoped + sets the GUC."""

    def __init__(self, *, session_factory: sessionmaker[SaSession], tables: CommerceTables) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add_grant(self, *, organization_id: str, grant: EntitlementGrant) -> None:
        table = self._tables.entitlement_grant
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    grant_id=grant.grant_id,
                    subject_id=grant.subject_id,
                    capability=grant.capability,
                    origin=grant.origin.value,
                    starts_at=_aware(grant.starts_at),
                    ends_at=(_aware(grant.ends_at) if grant.ends_at else None),
                    quota_limit=grant.quota_limit,
                    quota_used=grant.quota_used,
                    quota_disposition=grant.quota_disposition.value,
                    revoked=grant.revoked_at is not None,
                    revoked_at=(_aware(grant.revoked_at) if grant.revoked_at else None),
                )
            )
            uow.commit()

    def list_grants_for_subject(
        self, *, organization_id: str, subject_id: str
    ) -> Sequence[EntitlementGrant]:
        table = self._tables.entitlement_grant
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                )
            ).all()
        return [_row_to_grant(row) for row in rows]

    def revoke(self, *, organization_id: str, grant_id: str, now: datetime) -> bool:
        table = self._tables.entitlement_grant
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.revoked).where(
                    table.c.organization_id == organization_id,
                    table.c.grant_id == grant_id,
                )
            ).first()
            if existing is None or bool(existing.revoked):
                uow.commit()
                return False  # idempotent: unknown or already-revoked grant makes no change
            session.execute(
                update(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.grant_id == grant_id,
                )
                .values(revoked=True, revoked_at=_aware(now))
            )
            uow.commit()
        return True


def _revoked(grant: EntitlementGrant, now: datetime) -> EntitlementGrant:
    return EntitlementGrant(
        grant_id=grant.grant_id,
        subject_id=grant.subject_id,
        capability=grant.capability,
        origin=grant.origin,
        starts_at=grant.starts_at,
        ends_at=grant.ends_at,
        quota_limit=grant.quota_limit,
        quota_used=grant.quota_used,
        quota_disposition=grant.quota_disposition,
        organization_id=grant.organization_id,
        revoked_at=now,
    )


def _row_to_grant(row: object) -> EntitlementGrant:
    return EntitlementGrant(
        grant_id=row.grant_id,
        subject_id=row.subject_id,
        capability=row.capability,
        origin=GrantOrigin(row.origin),
        starts_at=_aware(row.starts_at),
        ends_at=(_aware(row.ends_at) if row.ends_at else None),
        quota_limit=row.quota_limit,
        quota_used=row.quota_used,
        quota_disposition=QuotaDisposition(row.quota_disposition),
        organization_id=row.organization_id,
        revoked_at=(_aware(row.revoked_at) if row.revoked_at else None),
    )


class _CommerceEntitlementRepository:
    """Structural type shared by the in-memory and SQLAlchemy commerce entitlement stores."""

    def add_grant(self, *, organization_id: str, grant: EntitlementGrant) -> None: ...

    def list_grants_for_subject(
        self, *, organization_id: str, subject_id: str
    ) -> Sequence[EntitlementGrant]: ...

    def revoke(self, *, organization_id: str, grant_id: str, now: datetime) -> bool: ...


# ---------------------------------------------------------------------------
# The gateway that reuses the entitlement engine (EntitlementGrantPort)
# ---------------------------------------------------------------------------


class EntitlementEngineGateway:
    """Reference :class:`EntitlementGrantPort` that reuses the entitlement engine (ARCH-019)."""

    def __init__(
        self,
        *,
        repository: _CommerceEntitlementRepository,
        id_factory: Callable[[], str],
    ) -> None:
        self._repo = repository
        self._id_factory = id_factory

    def grant(
        self,
        *,
        subject_id: str,
        capability: str,
        billing_type: BillingType,
        starts_at: datetime,
        ends_at: datetime | None = None,
        organization_id: str,
    ) -> str:
        origin = _ORIGIN_BY_BILLING[billing_type]
        grant = EntitlementGrant(
            grant_id=self._id_factory(),
            subject_id=subject_id,
            capability=capability,
            origin=origin,
            starts_at=starts_at,
            ends_at=ends_at,
            organization_id=organization_id,
        )
        self._repo.add_grant(organization_id=organization_id, grant=grant)
        return grant.grant_id

    def revoke(self, *, grant_id: str, now: datetime, organization_id: str) -> bool:
        return self._repo.revoke(organization_id=organization_id, grant_id=grant_id, now=now)

    def is_entitled(self, *, subject_id: str, action: str, organization_id: str) -> bool:
        grants = tuple(
            self._repo.list_grants_for_subject(
                organization_id=organization_id, subject_id=subject_id
            )
        )
        decision = decide(
            decision_id=self._id_factory(),
            actor_id=subject_id,
            action=action,
            resource_type=_DECISION_RESOURCE.type,
            resource_id=_DECISION_RESOURCE.id,
            grants=grants,
            now=datetime.now(UTC),
        )
        return decision.allowed
