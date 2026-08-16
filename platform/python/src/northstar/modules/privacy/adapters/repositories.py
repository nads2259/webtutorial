"""Privacy repositories (in-memory + SQLAlchemy) implementing :class:`PrivacyRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL FORCED RLS applies as
defense-in-depth. No string interpolation of values.

Consent is APPEND-ONLY: ``add_consent`` always inserts a new immutable row and never updates an
existing one (EVAL-PRIV-002), so the ordered ``version`` history is a complete audit trail. Rights
requests are keyed by ``(organization_id, request_id)`` and only their lifecycle status/completion
timestamp transition (EVAL-PRIV-003).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..application.ports import PrivacyRepositoryPort
from ..domain.model import (
    ConsentRecord,
    ConsentState,
    DataClass,
    LawfulBasis,
    PersonalDataField,
    RetentionPolicy,
    RightsRequest,
    RightsStatus,
    RightsType,
)
from .tables import PrivacyTables


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# In-memory repository (fast, deterministic unit/security tests)
# ---------------------------------------------------------------------------


class InMemoryPrivacyRepository(PrivacyRepositoryPort):
    """In-memory, tenant-scoped privacy repository for fast, deterministic tests."""

    def __init__(self) -> None:
        self._fields: dict[tuple[str, str], PersonalDataField] = {}
        self._consent: list[tuple[str, ConsentRecord]] = []
        self._requests: dict[tuple[str, str], RightsRequest] = {}

    # Catalog ------------------------------------------------------------
    def add_field(self, *, organization_id: str, field: PersonalDataField) -> None:
        self._fields[(organization_id, field.field_id)] = field

    def get_field(self, *, organization_id: str, field_id: str) -> PersonalDataField | None:
        return self._fields.get((organization_id, field_id))

    def list_fields(self, *, organization_id: str) -> Sequence[PersonalDataField]:
        return [f for (org, _fid), f in self._fields.items() if org == organization_id]

    # Consent (append-only, versioned) -----------------------------------
    def latest_consent(
        self, *, organization_id: str, subject_id: str, purpose: str
    ) -> ConsentRecord | None:
        matches = [
            record
            for org, record in self._consent
            if org == organization_id
            and record.subject_id == subject_id
            and record.purpose == purpose
        ]
        return max(matches, key=lambda r: r.version) if matches else None

    def add_consent(self, *, organization_id: str, record: ConsentRecord) -> None:
        self._consent.append((organization_id, record))

    def consent_history(
        self, *, organization_id: str, subject_id: str, purpose: str
    ) -> Sequence[ConsentRecord]:
        matches = [
            record
            for org, record in self._consent
            if org == organization_id
            and record.subject_id == subject_id
            and record.purpose == purpose
        ]
        return sorted(matches, key=lambda r: r.version)

    # Rights requests ----------------------------------------------------
    def add_request(self, *, organization_id: str, request: RightsRequest) -> None:
        self._requests[(organization_id, request.request_id)] = request

    def get_request(self, *, organization_id: str, request_id: str) -> RightsRequest | None:
        return self._requests.get((organization_id, request_id))

    def update_request(self, *, organization_id: str, request: RightsRequest) -> None:
        self._requests[(organization_id, request.request_id)] = request


# ---------------------------------------------------------------------------
# SQLAlchemy repository (tenant-scoped, RLS defense-in-depth)
# ---------------------------------------------------------------------------


def _field_from_row(row: object) -> PersonalDataField:
    return PersonalDataField(
        field_id=row.field_id,
        module_id=row.module_id,
        name=row.name,
        purpose=row.purpose,
        lawful_basis=LawfulBasis(row.lawful_basis),
        retention=RetentionPolicy(
            data_class=DataClass(row.data_class), retention_days=int(row.retention_days)
        ),
        description=row.description,
    )


def _consent_from_row(row: object) -> ConsentRecord:
    return ConsentRecord(
        record_id=row.record_id,
        organization_id=row.organization_id,
        subject_id=row.subject_id,
        purpose=row.purpose,
        category=row.category,
        state=ConsentState(row.state),
        lawful_basis=LawfulBasis(row.lawful_basis),
        version=int(row.version),
        created_at=_aware(row.created_at),
    )


def _request_from_row(row: object) -> RightsRequest:
    return RightsRequest(
        request_id=row.request_id,
        organization_id=row.organization_id,
        subject_id=row.subject_id,
        requested_by=row.requested_by,
        rights_type=RightsType(row.rights_type),
        status=RightsStatus(row.status),
        created_at=_aware(row.created_at),
        completed_at=_aware(row.completed_at) if row.completed_at is not None else None,
    )


class SqlAlchemyPrivacyRepository(PrivacyRepositoryPort):
    """Tenant-scoped privacy repository over PostgreSQL (RLS forced, rule 50, LAW-13)."""

    def __init__(self, *, session_factory: sessionmaker[SaSession], tables: PrivacyTables) -> None:
        self._session_factory = session_factory
        self._tables = tables

    # Catalog ------------------------------------------------------------
    def add_field(self, *, organization_id: str, field: PersonalDataField) -> None:
        table = self._tables.data_field
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    field_id=field.field_id,
                    module_id=field.module_id,
                    name=field.name,
                    purpose=field.purpose,
                    lawful_basis=field.lawful_basis.value,
                    data_class=field.data_class.value,
                    retention_days=field.retention.retention_days,
                    description=field.description,
                    created_at=datetime.now(UTC),
                )
            )
            uow.commit()

    def get_field(self, *, organization_id: str, field_id: str) -> PersonalDataField | None:
        table = self._tables.data_field
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.field_id == field_id,
                )
            ).first()
        return _field_from_row(row) if row is not None else None

    def list_fields(self, *, organization_id: str) -> Sequence[PersonalDataField]:
        table = self._tables.data_field
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(table.c.organization_id == organization_id)
            ).all()
        return [_field_from_row(row) for row in rows]

    # Consent (append-only, versioned) -----------------------------------
    def latest_consent(
        self, *, organization_id: str, subject_id: str, purpose: str
    ) -> ConsentRecord | None:
        table = self._tables.consent_record
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                    table.c.purpose == purpose,
                )
                .order_by(table.c.version.desc())
                .limit(1)
            ).first()
        return _consent_from_row(row) if row is not None else None

    def add_consent(self, *, organization_id: str, record: ConsentRecord) -> None:
        table = self._tables.consent_record
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    record_id=record.record_id,
                    subject_id=record.subject_id,
                    purpose=record.purpose,
                    category=record.category,
                    state=record.state.value,
                    lawful_basis=record.lawful_basis.value,
                    version=record.version,
                    created_at=record.created_at,
                )
            )
            uow.commit()

    def consent_history(
        self, *, organization_id: str, subject_id: str, purpose: str
    ) -> Sequence[ConsentRecord]:
        table = self._tables.consent_record
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                    table.c.purpose == purpose,
                )
                .order_by(table.c.version.asc())
            ).all()
        return [_consent_from_row(row) for row in rows]

    # Rights requests ----------------------------------------------------
    def add_request(self, *, organization_id: str, request: RightsRequest) -> None:
        table = self._tables.rights_request
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    request_id=request.request_id,
                    subject_id=request.subject_id,
                    requested_by=request.requested_by,
                    rights_type=request.rights_type.value,
                    status=request.status.value,
                    created_at=request.created_at,
                    completed_at=request.completed_at,
                )
            )
            uow.commit()

    def get_request(self, *, organization_id: str, request_id: str) -> RightsRequest | None:
        table = self._tables.rights_request
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.request_id == request_id,
                )
            ).first()
        return _request_from_row(row) if row is not None else None

    def update_request(self, *, organization_id: str, request: RightsRequest) -> None:
        table = self._tables.rights_request
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                update(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.request_id == request.request_id,
                )
                .values(
                    status=request.status.value,
                    completed_at=request.completed_at,
                )
            )
            uow.commit()


__all__ = [
    "InMemoryPrivacyRepository",
    "SqlAlchemyPrivacyRepository",
]
