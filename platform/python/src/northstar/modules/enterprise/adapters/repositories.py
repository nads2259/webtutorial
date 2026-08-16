"""Enterprise repositories (in-memory + SQLAlchemy) implementing :class:`EnterpriseRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL RLS applies as defense-in-depth
(FR-POL-004). Federation mappings are immutable once written (a re-presented assertion resolves the
existing mapping); provisioning records are updated in place only to carry the latest SCIM
attributes or a deactivation. No string interpolation of values.
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

from ..domain.model import (
    FederatedIdentityMapping,
    ProvisioningRecord,
    ProvisioningResourceType,
)
from .tables import EnterpriseTables


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _mapping_from_row(row: Any) -> FederatedIdentityMapping:  # noqa: ANN401 dynamic Row
    return FederatedIdentityMapping(
        mapping_id=row.mapping_id,
        organization_id=row.organization_id,
        issuer=row.issuer,
        external_subject=row.external_subject,
        subject_id=row.subject_id,
        user_id=row.user_id,
        linked_at=_aware(row.linked_at),  # type: ignore[arg-type]
    )


def _record_from_row(row: Any) -> ProvisioningRecord:  # noqa: ANN401 dynamic Row
    return ProvisioningRecord(
        record_id=row.record_id,
        organization_id=row.organization_id,
        resource_type=ProvisioningResourceType(row.resource_type),
        external_id=row.external_id,
        active=bool(row.active),
        provisioned_at=_aware(row.provisioned_at),  # type: ignore[arg-type]
        updated_at=_aware(row.updated_at),  # type: ignore[arg-type]
        subject_id=row.subject_id,
        display_name=row.display_name,
        email=row.email,
        members=tuple(row.members or ()),
        deactivated_at=_aware(row.deactivated_at),
    )


def _record_values(record: ProvisioningRecord) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "organization_id": record.organization_id,
        "resource_type": record.resource_type.value,
        "external_id": record.external_id,
        "active": record.active,
        "subject_id": record.subject_id,
        "display_name": record.display_name,
        "email": record.email,
        "members": list(record.members),
        "provisioned_at": record.provisioned_at,
        "updated_at": record.updated_at,
        "deactivated_at": record.deactivated_at,
    }


class InMemoryEnterpriseRepository:
    """In-memory repository for fast, deterministic unit tests (tenant-scoped)."""

    def __init__(self) -> None:
        self._mappings: dict[tuple[str, str, str], FederatedIdentityMapping] = {}
        self._records: dict[tuple[str, str], ProvisioningRecord] = {}

    def get_mapping(
        self, *, organization_id: str, issuer: str, external_subject: str
    ) -> FederatedIdentityMapping | None:
        return self._mappings.get((organization_id, issuer, external_subject))

    def add_mapping(self, mapping: FederatedIdentityMapping) -> None:
        self._mappings[(mapping.organization_id, mapping.issuer, mapping.external_subject)] = (
            mapping
        )

    def get_provisioning_record(
        self, *, organization_id: str, external_id: str
    ) -> ProvisioningRecord | None:
        return self._records.get((organization_id, external_id))

    def add_provisioning_record(self, record: ProvisioningRecord) -> None:
        self._records[(record.organization_id, record.external_id)] = record

    def update_provisioning_record(self, record: ProvisioningRecord) -> None:
        key = (record.organization_id, record.external_id)
        if key in self._records:
            self._records[key] = record

    def list_provisioning_records(self, *, organization_id: str) -> Sequence[ProvisioningRecord]:
        return [r for (org, _), r in self._records.items() if org == organization_id]


class SqlAlchemyEnterpriseRepository:
    """PostgreSQL repository; scoped queries filter by ``organization_id`` and set the GUC."""

    def __init__(
        self, *, session_factory: sessionmaker[SaSession], tables: EnterpriseTables
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables

    def get_mapping(
        self, *, organization_id: str, issuer: str, external_subject: str
    ) -> FederatedIdentityMapping | None:
        table = self._tables.federation_mapping
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.issuer == issuer,
                    table.c.external_subject == external_subject,
                )
            ).first()
        return None if row is None else _mapping_from_row(row)

    def add_mapping(self, mapping: FederatedIdentityMapping) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, mapping.organization_id)
            uow.session.execute(
                insert(self._tables.federation_mapping).values(
                    mapping_id=mapping.mapping_id,
                    organization_id=mapping.organization_id,
                    issuer=mapping.issuer,
                    external_subject=mapping.external_subject,
                    subject_id=mapping.subject_id,
                    user_id=mapping.user_id,
                    linked_at=mapping.linked_at,
                )
            )
            uow.commit()

    def get_provisioning_record(
        self, *, organization_id: str, external_id: str
    ) -> ProvisioningRecord | None:
        table = self._tables.provisioning_record
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.external_id == external_id,
                )
            ).first()
        return None if row is None else _record_from_row(row)

    def add_provisioning_record(self, record: ProvisioningRecord) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, record.organization_id)
            uow.session.execute(
                insert(self._tables.provisioning_record).values(**_record_values(record))
            )
            uow.commit()

    def update_provisioning_record(self, record: ProvisioningRecord) -> None:
        table = self._tables.provisioning_record
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, record.organization_id)
            uow.session.execute(
                update(table)
                .where(
                    table.c.record_id == record.record_id,
                    table.c.organization_id == record.organization_id,
                )
                .values(
                    active=record.active,
                    subject_id=record.subject_id,
                    display_name=record.display_name,
                    email=record.email,
                    members=list(record.members),
                    updated_at=record.updated_at,
                    deactivated_at=record.deactivated_at,
                )
            )
            uow.commit()

    def list_provisioning_records(self, *, organization_id: str) -> Sequence[ProvisioningRecord]:
        table = self._tables.provisioning_record
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(table.c.organization_id == organization_id)
                .order_by(table.c.provisioned_at.asc())
            ).all()
        return [_record_from_row(row) for row in rows]
