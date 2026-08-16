"""Messaging repositories (in-memory + SQLAlchemy) implementing :class:`MessagingRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL FORCED RLS applies as
defense-in-depth. No string interpolation of values.

Two idempotency/immutability guarantees are enforced at the persistence boundary:

* :meth:`save_template_version` rejects an already-published ``(template_id, version)`` — a
  published template version is IMMUTABLE (FR-MSG-002).
* :meth:`record_delivery` is idempotent on ``(organization_id, campaign_id, recipient_id,
  idempotency_key)``: a colliding re-submission returns ``False`` (already existed) instead of
  inserting a duplicate delivery (FR-MSG-006).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.errors import TemplateVersionAlreadyPublished
from ..domain.model import (
    Campaign,
    CampaignStatus,
    ConsentRecord,
    DeliveryReceipt,
    DeliveryStatus,
    MessageClass,
    Schedule,
    Segment,
    SuppressionEntry,
    TemplateVersion,
    TrackingConfig,
)
from .tables import MessagingTables


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# In-memory repository (fast, deterministic unit/security tests)
# ---------------------------------------------------------------------------


class InMemoryMessagingRepository:
    """In-memory, tenant-scoped repository for fast, deterministic tests."""

    def __init__(self) -> None:
        self._templates: dict[tuple[str, str, int], TemplateVersion] = {}
        self._campaigns: dict[tuple[str, str], Campaign] = {}
        self._consent: dict[tuple[str, str, str, str], ConsentRecord] = {}
        self._suppression: dict[tuple[str, str], SuppressionEntry] = {}
        self._deliveries: dict[tuple[str, str, str, str], DeliveryReceipt] = {}

    def save_template_version(self, *, organization_id: str, template: TemplateVersion) -> None:
        key = (organization_id, template.template_id, template.version)
        if key in self._templates:
            raise TemplateVersionAlreadyPublished(template.template_id, template.version)
        self._templates[key] = template

    def get_template_version(
        self, *, organization_id: str, template_id: str, version: int
    ) -> TemplateVersion | None:
        return self._templates.get((organization_id, template_id, version))

    def add_campaign(self, *, organization_id: str, campaign: Campaign) -> None:
        self._campaigns[(organization_id, campaign.campaign_id)] = campaign

    def get_campaign(self, *, organization_id: str, campaign_id: str) -> Campaign | None:
        return self._campaigns.get((organization_id, campaign_id))

    def update_campaign(self, *, organization_id: str, campaign: Campaign) -> None:
        self._campaigns[(organization_id, campaign.campaign_id)] = campaign

    def set_consent(self, *, organization_id: str, consent: ConsentRecord) -> None:
        self._consent[(organization_id, consent.recipient_id, consent.channel, consent.purpose)] = (
            consent
        )

    def get_consent(
        self, *, organization_id: str, recipient_id: str, channel: str, purpose: str
    ) -> ConsentRecord | None:
        return self._consent.get((organization_id, recipient_id, channel, purpose))

    def add_suppression(self, *, organization_id: str, entry: SuppressionEntry) -> None:
        # Idempotent: re-suppressing keeps a single entry keyed by recipient (FR-MSG-005).
        self._suppression[(organization_id, entry.recipient_id)] = entry

    def is_suppressed(self, *, organization_id: str, recipient_id: str) -> bool:
        return (organization_id, recipient_id) in self._suppression

    def get_delivery(
        self,
        *,
        organization_id: str,
        campaign_id: str,
        recipient_id: str,
        idempotency_key: str,
    ) -> DeliveryReceipt | None:
        return self._deliveries.get((organization_id, campaign_id, recipient_id, idempotency_key))

    def record_delivery(self, *, organization_id: str, receipt: DeliveryReceipt) -> bool:
        key = (
            organization_id,
            receipt.campaign_id,
            receipt.recipient_id,
            receipt.idempotency_key,
        )
        if key in self._deliveries:
            return False
        self._deliveries[key] = receipt
        return True

    def list_deliveries(
        self, *, organization_id: str, campaign_id: str
    ) -> Sequence[DeliveryReceipt]:
        return [
            r
            for (org, camp, _rid, _k), r in self._deliveries.items()
            if org == organization_id and camp == campaign_id
        ]


# ---------------------------------------------------------------------------
# SQLAlchemy repository (PostgreSQL; RLS-forced schema)
# ---------------------------------------------------------------------------


class SqlAlchemyMessagingRepository:
    """PostgreSQL repository; every query filters by ``organization_id`` and sets the tenant GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: MessagingTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    # Templates ----------------------------------------------------------
    def save_template_version(self, *, organization_id: str, template: TemplateVersion) -> None:
        table = self._tables.template_version
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.version).where(
                    table.c.organization_id == organization_id,
                    table.c.template_id == template.template_id,
                    table.c.version == template.version,
                )
            ).first()
            if existing is not None:
                raise TemplateVersionAlreadyPublished(template.template_id, template.version)
            session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    template_id=template.template_id,
                    version=template.version,
                    subject=template.subject,
                    html_body=template.html_body,
                    text_body=template.text_body,
                    required_variables=list(template.required_variables),
                    content_hash=template.content_hash,
                    created_at=_now(),
                )
            )
            uow.commit()

    def get_template_version(
        self, *, organization_id: str, template_id: str, version: int
    ) -> TemplateVersion | None:
        table = self._tables.template_version
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.template_id == template_id,
                    table.c.version == version,
                )
            ).first()
        if row is None:
            return None
        return TemplateVersion(
            template_id=row.template_id,
            version=row.version,
            subject=row.subject,
            html_body=row.html_body,
            text_body=row.text_body,
            required_variables=tuple(row.required_variables or ()),
        )

    # Campaigns ----------------------------------------------------------
    def add_campaign(self, *, organization_id: str, campaign: Campaign) -> None:
        table = self._tables.campaign
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(insert(table).values(**self._campaign_values(campaign)))
            uow.commit()

    def get_campaign(self, *, organization_id: str, campaign_id: str) -> Campaign | None:
        table = self._tables.campaign
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.campaign_id == campaign_id,
                )
            ).first()
        if row is None:
            return None
        return Campaign(
            organization_id=row.organization_id,
            campaign_id=row.campaign_id,
            name=row.name,
            message_class=MessageClass(row.message_class),
            template_id=row.template_id,
            template_version=row.template_version,
            channel=row.channel,
            purpose=row.purpose,
            segment=Segment.from_specs(tuple(row.segment or ())),
            schedule=Schedule.from_dict(dict(row.schedule or {})),
            tracking=TrackingConfig(
                open_tracking=bool(row.open_tracking),
                click_tracking=bool(row.click_tracking),
            ),
            status=CampaignStatus(row.status),
        )

    def update_campaign(self, *, organization_id: str, campaign: Campaign) -> None:
        table = self._tables.campaign
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            values = self._campaign_values(campaign)
            values.pop("organization_id")
            values.pop("campaign_id")
            values.pop("created_at")
            uow.session.execute(
                update(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.campaign_id == campaign.campaign_id,
                )
                .values(**values)
            )
            uow.commit()

    def _campaign_values(self, campaign: Campaign) -> dict[str, object]:
        return {
            "organization_id": campaign.organization_id,
            "campaign_id": campaign.campaign_id,
            "name": campaign.name,
            "message_class": campaign.message_class.value,
            "template_id": campaign.template_id,
            "template_version": campaign.template_version,
            "channel": campaign.channel,
            "purpose": campaign.purpose,
            "segment": campaign.segment.to_specs(),
            "schedule": campaign.schedule.to_dict(),
            "open_tracking": campaign.tracking.open_tracking,
            "click_tracking": campaign.tracking.click_tracking,
            "status": campaign.status.value,
            "created_at": _now(),
            "updated_at": _now(),
        }

    # Consent + suppression ---------------------------------------------
    def set_consent(self, *, organization_id: str, consent: ConsentRecord) -> None:
        table = self._tables.consent_record
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.recipient_id).where(
                    table.c.organization_id == organization_id,
                    table.c.recipient_id == consent.recipient_id,
                    table.c.channel == consent.channel,
                    table.c.purpose == consent.purpose,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        recipient_id=consent.recipient_id,
                        channel=consent.channel,
                        purpose=consent.purpose,
                        consented=consent.consented,
                        updated_at=_now(),
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == organization_id,
                        table.c.recipient_id == consent.recipient_id,
                        table.c.channel == consent.channel,
                        table.c.purpose == consent.purpose,
                    )
                    .values(consented=consent.consented, updated_at=_now())
                )
            uow.commit()

    def get_consent(
        self, *, organization_id: str, recipient_id: str, channel: str, purpose: str
    ) -> ConsentRecord | None:
        table = self._tables.consent_record
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.recipient_id == recipient_id,
                    table.c.channel == channel,
                    table.c.purpose == purpose,
                )
            ).first()
        if row is None:
            return None
        return ConsentRecord(
            organization_id=row.organization_id,
            recipient_id=row.recipient_id,
            channel=row.channel,
            purpose=row.purpose,
            consented=bool(row.consented),
        )

    def add_suppression(self, *, organization_id: str, entry: SuppressionEntry) -> None:
        table = self._tables.suppression_entry
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.recipient_id).where(
                    table.c.organization_id == organization_id,
                    table.c.recipient_id == entry.recipient_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        recipient_id=entry.recipient_id,
                        reason=entry.reason.value,
                        created_at=_now(),
                    )
                )
            else:
                # Idempotent: keep a single suppression entry per recipient (update reason).
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == organization_id,
                        table.c.recipient_id == entry.recipient_id,
                    )
                    .values(reason=entry.reason.value)
                )
            uow.commit()

    def is_suppressed(self, *, organization_id: str, recipient_id: str) -> bool:
        table = self._tables.suppression_entry
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table.c.recipient_id).where(
                    table.c.organization_id == organization_id,
                    table.c.recipient_id == recipient_id,
                )
            ).first()
        return row is not None

    # Delivery receipts (idempotent submission evidence) ----------------
    def get_delivery(
        self,
        *,
        organization_id: str,
        campaign_id: str,
        recipient_id: str,
        idempotency_key: str,
    ) -> DeliveryReceipt | None:
        table = self._tables.delivery_receipt
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.campaign_id == campaign_id,
                    table.c.recipient_id == recipient_id,
                    table.c.idempotency_key == idempotency_key,
                )
            ).first()
        if row is None:
            return None
        return self._receipt_from_row(row)

    def record_delivery(self, *, organization_id: str, receipt: DeliveryReceipt) -> bool:
        table = self._tables.delivery_receipt
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.idempotency_key).where(
                    table.c.organization_id == organization_id,
                    table.c.campaign_id == receipt.campaign_id,
                    table.c.recipient_id == receipt.recipient_id,
                    table.c.idempotency_key == receipt.idempotency_key,
                )
            ).first()
            if existing is not None:
                return False
            session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    campaign_id=receipt.campaign_id,
                    recipient_id=receipt.recipient_id,
                    idempotency_key=receipt.idempotency_key,
                    provider_message_id=receipt.provider_message_id,
                    status=receipt.status.value,
                    send_at=receipt.send_at,
                    created_at=_now(),
                )
            )
            uow.commit()
        return True

    def list_deliveries(
        self, *, organization_id: str, campaign_id: str
    ) -> Sequence[DeliveryReceipt]:
        table = self._tables.delivery_receipt
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.campaign_id == campaign_id,
                )
            ).all()
        return [self._receipt_from_row(row) for row in rows]

    def _receipt_from_row(self, row: object) -> DeliveryReceipt:
        return DeliveryReceipt(
            organization_id=row.organization_id,  # type: ignore[attr-defined]
            campaign_id=row.campaign_id,  # type: ignore[attr-defined]
            recipient_id=row.recipient_id,  # type: ignore[attr-defined]
            idempotency_key=row.idempotency_key,  # type: ignore[attr-defined]
            provider_message_id=row.provider_message_id,  # type: ignore[attr-defined]
            status=DeliveryStatus(row.status),  # type: ignore[attr-defined]
            send_at=_aware(row.send_at),  # type: ignore[attr-defined]
        )


__all__ = [
    "InMemoryMessagingRepository",
    "SqlAlchemyMessagingRepository",
]
