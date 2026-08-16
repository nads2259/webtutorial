"""Commerce repositories (in-memory + SQLAlchemy) implementing :class:`CommerceRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL FORCED RLS applies as
defense-in-depth. No string interpolation of values.

The ``payment_event`` insert is the idempotency gate: a duplicate ``(organization_id, event_id)`` is
a primary-key collision, so a replayed provider callback is recorded once and has a single effect
(FR-COM-003).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.model import (
    AdKind,
    Offer,
    Product,
    Purchase,
    PurchaseStatus,
    SponsoredPlacement,
)
from .tables import CommerceTables


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# In-memory repository (fast, deterministic unit/security tests)
# ---------------------------------------------------------------------------


class InMemoryCommerceRepository:
    """In-memory, tenant-scoped commerce repository for fast, deterministic tests."""

    def __init__(self) -> None:
        self._products: dict[tuple[str, str], Product] = {}
        self._offers: dict[tuple[str, str, str], Offer] = {}
        self._purchases: dict[tuple[str, str], Purchase] = {}
        self._events: dict[tuple[str, str], str] = {}
        self._refunds: dict[tuple[str, str], str] = {}
        self._placements: dict[tuple[str, str], SponsoredPlacement] = {}

    def add_product(self, *, organization_id: str, product: Product) -> None:
        self._products[(organization_id, product.product_id)] = product

    def upsert_offer(self, *, organization_id: str, offer: Offer) -> None:
        self._offers[(organization_id, offer.offer_id, offer.version)] = offer

    def get_offer(self, *, organization_id: str, offer_id: str, version: str) -> Offer | None:
        return self._offers.get((organization_id, offer_id, version))

    def add_purchase(self, *, organization_id: str, purchase: Purchase) -> None:
        self._purchases[(organization_id, purchase.purchase_id)] = purchase

    def get_purchase(self, *, organization_id: str, purchase_id: str) -> Purchase | None:
        return self._purchases.get((organization_id, purchase_id))

    def save_purchase(self, *, organization_id: str, purchase: Purchase) -> None:
        self._purchases[(organization_id, purchase.purchase_id)] = purchase

    def record_payment_event(
        self, *, organization_id: str, event_id: str, event_type: str, purchase_id: str
    ) -> bool:
        key = (organization_id, event_id)
        if key in self._events:
            return False
        self._events[key] = event_type
        return True

    def add_refund(
        self, *, organization_id: str, refund_id: str, purchase_id: str, now: datetime
    ) -> None:
        self._refunds[(organization_id, refund_id)] = purchase_id

    def add_placement(self, *, organization_id: str, placement: SponsoredPlacement) -> None:
        self._placements[(organization_id, placement.placement_id)] = placement

    def list_placements(self, *, organization_id: str) -> Sequence[SponsoredPlacement]:
        return [p for (org, _pid), p in self._placements.items() if org == organization_id]


# ---------------------------------------------------------------------------
# SQLAlchemy repository (PostgreSQL; RLS-forced schema)
# ---------------------------------------------------------------------------


class SqlAlchemyCommerceRepository:
    """PostgreSQL commerce repository; every query filters by ``organization_id`` + sets the GUC."""

    def __init__(self, *, session_factory: sessionmaker[SaSession], tables: CommerceTables) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def add_product(self, *, organization_id: str, product: Product) -> None:
        table = self._tables.product
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.product_id).where(
                    table.c.organization_id == organization_id,
                    table.c.product_id == product.product_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        product_id=product.product_id,
                        name=product.name,
                        kind=product.kind,
                        created_at=_now(),
                    )
                )
            uow.commit()

    def upsert_offer(self, *, organization_id: str, offer: Offer) -> None:
        table = self._tables.offer
        contract = offer.to_contract()
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.offer_id).where(
                    table.c.organization_id == organization_id,
                    table.c.offer_id == offer.offer_id,
                    table.c.version == offer.version,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        offer_id=offer.offer_id,
                        version=offer.version,
                        product_id=offer.product_id,
                        status=offer.status.value,
                        price=contract["price"],
                        grants=contract["grants"],
                        eligibility=contract["eligibility"],
                        terms_version=offer.terms_version,
                        effective_from=offer.effective_from,
                        effective_until=offer.effective_until,
                        created_at=_now(),
                    )
                )
            uow.commit()

    def get_offer(self, *, organization_id: str, offer_id: str, version: str) -> Offer | None:
        table = self._tables.offer
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.offer_id == offer_id,
                    table.c.version == version,
                )
            ).first()
        if row is None:
            return None
        return Offer.from_dict(
            {
                "offer_id": row.offer_id,
                "version": row.version,
                "product_id": row.product_id,
                "status": row.status,
                "price": dict(row.price),
                "grants": list(row.grants),
                "eligibility": dict(row.eligibility or {}),
                "terms_version": row.terms_version,
                "effective_from": (
                    _aware(row.effective_from).isoformat() if row.effective_from else None
                ),
                "effective_until": (
                    _aware(row.effective_until).isoformat() if row.effective_until else None
                ),
            }
        )

    def add_purchase(self, *, organization_id: str, purchase: Purchase) -> None:
        self.save_purchase(organization_id=organization_id, purchase=purchase, insert_only=True)

    def save_purchase(
        self, *, organization_id: str, purchase: Purchase, insert_only: bool = False
    ) -> None:
        table = self._tables.purchase
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.purchase_id).where(
                    table.c.organization_id == organization_id,
                    table.c.purchase_id == purchase.purchase_id,
                )
            ).first()
            values = {
                "offer_id": purchase.offer_id,
                "offer_version": purchase.offer_version,
                "product_id": purchase.product_id,
                "subject_id": purchase.subject_id,
                "status": purchase.status.value,
                "grant_ids": list(purchase.grant_ids),
                "updated_at": purchase.updated_at,
            }
            if existing is None:
                from sqlalchemy import insert as _insert

                session.execute(
                    _insert(table).values(
                        organization_id=organization_id,
                        purchase_id=purchase.purchase_id,
                        created_at=_aware(purchase.created_at),
                        **values,
                    )
                )
            elif not insert_only:
                from sqlalchemy import update as _update

                session.execute(
                    _update(table)
                    .where(
                        table.c.organization_id == organization_id,
                        table.c.purchase_id == purchase.purchase_id,
                    )
                    .values(**values)
                )
            uow.commit()

    def get_purchase(self, *, organization_id: str, purchase_id: str) -> Purchase | None:
        table = self._tables.purchase
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.purchase_id == purchase_id,
                )
            ).first()
        if row is None:
            return None
        return Purchase(
            purchase_id=row.purchase_id,
            offer_id=row.offer_id,
            offer_version=row.offer_version,
            product_id=row.product_id,
            subject_id=row.subject_id,
            status=PurchaseStatus(row.status),
            created_at=_aware(row.created_at),
            grant_ids=tuple(row.grant_ids or ()),
            organization_id=row.organization_id,
            updated_at=(_aware(row.updated_at) if row.updated_at else None),
        )

    def record_payment_event(
        self, *, organization_id: str, event_id: str, event_type: str, purchase_id: str
    ) -> bool:
        table = self._tables.payment_event
        try:
            with SqlAlchemyUnitOfWork(self._session_factory) as uow:
                session = uow.session
                set_tenant_guc(session, organization_id)
                existing = session.execute(
                    select(table.c.event_id).where(
                        table.c.organization_id == organization_id,
                        table.c.event_id == event_id,
                    )
                ).first()
                if existing is not None:
                    uow.commit()
                    return False
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        event_id=event_id,
                        event_type=event_type,
                        purchase_id=purchase_id,
                        processed_at=_now(),
                    )
                )
                uow.commit()
        except IntegrityError:
            # A concurrent insert of the same event id lost the race: it was already processed.
            return False
        return True

    def add_refund(
        self, *, organization_id: str, refund_id: str, purchase_id: str, now: datetime
    ) -> None:
        table = self._tables.refund
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    refund_id=refund_id,
                    purchase_id=purchase_id,
                    created_at=_aware(now),
                )
            )
            uow.commit()

    def add_placement(self, *, organization_id: str, placement: SponsoredPlacement) -> None:
        table = self._tables.ad_placement
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.placement_id).where(
                    table.c.organization_id == organization_id,
                    table.c.placement_id == placement.placement_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        placement_id=placement.placement_id,
                        kind=placement.kind.value,
                        disclosure_label=placement.disclosure_label,
                        disclosed=placement.disclosed,
                        created_at=_now(),
                    )
                )
            uow.commit()

    def list_placements(self, *, organization_id: str) -> Sequence[SponsoredPlacement]:
        table = self._tables.ad_placement
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(table.c.organization_id == organization_id)
            ).all()
        return [
            SponsoredPlacement(
                placement_id=row.placement_id,
                kind=AdKind(row.kind),
                disclosure_label=row.disclosure_label,
                disclosed=row.disclosed,
            )
            for row in rows
        ]


__all__ = [
    "InMemoryCommerceRepository",
    "SqlAlchemyCommerceRepository",
]
