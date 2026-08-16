"""Privacy & data-subject-rights domain model (pure, infra-free — rule 10, LAW-08).

This module encodes the privacy invariants of GATE-PRIVACY as deterministic, explainable value
objects with no infrastructure dependencies:

* :class:`PersonalDataField` + :class:`DataCatalog` — every registered personal-data field declares
  a ``purpose`` + ``lawful_basis`` + ``retention`` (EVAL-PRIV-001). A field missing a purpose or a
  positive retention is rejected at build; a stricter data class caps retention (NFR-PRV-005).
* :class:`ConsentRecord` — consent/preference decisions are VERSIONED and IMMUTABLE. A new decision
  ``supersede``s the prior one (creating ``version + 1``); an existing record is never mutated, so
  the full history is auditable (EVAL-PRIV-002).
* :class:`RightsRequest` — an access/export/erase request with an explicit lifecycle
  (pending → completed | failed | rejected). Transitions are validated (EVAL-PRIV-003).
* :class:`RetentionPolicy` + :class:`RetainedRecord` — clock-driven expiry: given an injectable
  ``now`` a record past its retention is expired and purged deterministically (NFR-PRV-005).
* :class:`ExportBundle` — a portable data-subject export bundle (EVAL-PRIV-002).

The identifiers here are the single authoritative vocabulary for privacy; capabilities and adapters
reuse them unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from .errors import (
    DuplicateDataField,
    InvalidRequestTransition,
    PurposeRequired,
    RetentionExceedsClassLimit,
    RetentionRequired,
)

# Policy resource type the kernel authorizes privacy actions against (deny-by-default).
RES_PRIVACY = "privacy.registry"

# Minimum characters for a declared processing purpose (mirrors analytics' purpose discipline).
_MIN_PURPOSE_LEN = 8


class LawfulBasis(StrEnum):
    """The lawful bases for processing personal data (GDPR Art. 6), a fixed enum (EVAL-PRIV-001)."""

    CONSENT = "consent"
    CONTRACT = "contract"
    LEGAL_OBLIGATION = "legal_obligation"
    VITAL_INTERESTS = "vital_interests"
    PUBLIC_TASK = "public_task"
    LEGITIMATE_INTERESTS = "legitimate_interests"


class DataClass(StrEnum):
    """Data classifications that drive retention strictness (data_classification, NFR-PRV-005).

    ``PRIVATE_NOTES_AI`` is the strictest class (short retention cap); ``PUBLIC_CONTENT`` has no
    retention cap. The caps are the stricter-class guarantee the retention sweep honors.
    """

    PRIVATE_NOTES_AI = "private_notes_ai"
    ORG_RESEARCH_CONFIDENTIAL = "org_research_confidential"
    PUBLIC_CONTENT = "public_content"


# Maximum retention (days) a data class permits. ``None`` means no cap (retain per field policy).
_CLASS_RETENTION_LIMIT_DAYS: Mapping[DataClass, int | None] = {
    DataClass.PRIVATE_NOTES_AI: 365,
    DataClass.ORG_RESEARCH_CONFIDENTIAL: 3650,
    DataClass.PUBLIC_CONTENT: None,
}


def class_retention_limit_days(data_class: DataClass) -> int | None:
    """The stricter-class retention cap in days for ``data_class`` (``None`` ⇒ no cap)."""
    return _CLASS_RETENTION_LIMIT_DAYS[data_class]


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """A retention rule: keep data of ``data_class`` for ``retention_days`` then purge it.

    ``retention_days`` must be positive (EVAL-PRIV-001) and must not exceed the stricter cap the
    data class enforces (NFR-PRV-005). Expiry is computed against an injectable clock so the
    retention sweep is deterministic under test.
    """

    data_class: DataClass
    retention_days: int

    def __post_init__(self) -> None:
        if self.retention_days <= 0:
            raise RetentionRequired()
        limit = class_retention_limit_days(self.data_class)
        if limit is not None and self.retention_days > limit:
            raise RetentionExceedsClassLimit(str(self.data_class), self.retention_days, limit)

    def expires_at(self, created_at: datetime) -> datetime:
        return created_at + timedelta(days=self.retention_days)

    def is_expired(self, created_at: datetime, now: datetime) -> bool:
        """``True`` when ``now`` is at or past the record's expiry (deterministic)."""
        return now >= self.expires_at(created_at)


@dataclass(frozen=True, slots=True)
class PersonalDataField:
    """A registered personal-data field/event with its purpose, lawful basis and retention.

    Every field MUST declare a non-trivial ``purpose`` and a ``retention`` policy (EVAL-PRIV-001);
    construction fails otherwise. ``module_id`` records the owning module so the catalog spans the
    whole system.
    """

    field_id: str
    module_id: str
    name: str
    purpose: str
    lawful_basis: LawfulBasis
    retention: RetentionPolicy
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.field_id:
            raise PurposeRequired()
        if not self.purpose or len(self.purpose.strip()) < _MIN_PURPOSE_LEN:
            raise PurposeRequired(self.field_id)
        if not isinstance(self.lawful_basis, LawfulBasis):  # defensive (deny-by-default)
            raise PurposeRequired(self.field_id)

    @property
    def data_class(self) -> DataClass:
        return self.retention.data_class


@dataclass(frozen=True, slots=True)
class DataCatalog:
    """An immutable catalog of personal-data fields keyed by ``field_id`` (EVAL-PRIV-001)."""

    fields: tuple[PersonalDataField, ...] = ()

    def register(self, new_field: PersonalDataField) -> DataCatalog:
        """Return a new catalog with ``new_field`` added; rejects a duplicate id."""
        if any(existing.field_id == new_field.field_id for existing in self.fields):
            raise DuplicateDataField(new_field.field_id)
        return DataCatalog(fields=(*self.fields, new_field))

    def get(self, field_id: str) -> PersonalDataField | None:
        for existing in self.fields:
            if existing.field_id == field_id:
                return existing
        return None


class ConsentState(StrEnum):
    """Whether a consent decision granted or withdrew consent."""

    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    """A versioned, IMMUTABLE consent/preference decision (EVAL-PRIV-002).

    A new decision ``supersede``s the previous one, producing ``version + 1``; the record itself is
    never mutated, so the ordered history is a complete audit trail. ``category`` aligns with the
    analytics consent categories (e.g. ``analytics``/``personalization``) without importing that
    module (rule 21 — reuse via a stable value, not internals).
    """

    record_id: str
    organization_id: str
    subject_id: str
    purpose: str
    category: str
    state: ConsentState
    lawful_basis: LawfulBasis
    version: int
    created_at: datetime

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvalidRequestTransition("v<1", "consent")

    @property
    def granted(self) -> bool:
        return self.state is ConsentState.GRANTED

    def supersede(
        self,
        *,
        record_id: str,
        state: ConsentState,
        lawful_basis: LawfulBasis,
        created_at: datetime,
    ) -> ConsentRecord:
        """Return the next immutable version of this consent decision (never mutates self)."""
        return ConsentRecord(
            record_id=record_id,
            organization_id=self.organization_id,
            subject_id=self.subject_id,
            purpose=self.purpose,
            category=self.category,
            state=state,
            lawful_basis=lawful_basis,
            version=self.version + 1,
            created_at=created_at,
        )


class RightsType(StrEnum):
    """The data-subject rights a request can exercise (EVAL-PRIV-003)."""

    ACCESS = "access"
    EXPORT = "export"
    ERASE = "erase"


class RightsStatus(StrEnum):
    """The lifecycle state of a rights request."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RightsRequest:
    """An access/export/erase request with a validated lifecycle (EVAL-PRIV-003).

    ``subject_id`` is the data subject; ``requested_by`` is the authenticated actor exercising the
    right (the subject or an authorized delegate). Transitions out of a terminal state are rejected.
    """

    request_id: str
    organization_id: str
    subject_id: str
    requested_by: str
    rights_type: RightsType
    status: RightsStatus
    created_at: datetime
    completed_at: datetime | None = None

    def _transition(self, to_status: RightsStatus, at: datetime | None) -> RightsRequest:
        if self.status is not RightsStatus.PENDING:
            raise InvalidRequestTransition(str(self.status), str(to_status))
        return RightsRequest(
            request_id=self.request_id,
            organization_id=self.organization_id,
            subject_id=self.subject_id,
            requested_by=self.requested_by,
            rights_type=self.rights_type,
            status=to_status,
            created_at=self.created_at,
            completed_at=at,
        )

    def complete(self, at: datetime) -> RightsRequest:
        return self._transition(RightsStatus.COMPLETED, at)

    def fail(self, at: datetime) -> RightsRequest:
        return self._transition(RightsStatus.FAILED, at)

    def reject(self, at: datetime) -> RightsRequest:
        return self._transition(RightsStatus.REJECTED, at)


@dataclass(frozen=True, slots=True)
class RetainedRecord:
    """A single personal-data item held under a retention policy (NFR-PRV-005).

    Reference stores hold these so the retention sweep can purge expired items deterministically
    against an injected clock, honoring stricter classes through the embedded ``RetentionPolicy``.
    """

    subject_id: str
    created_at: datetime
    policy: RetentionPolicy
    payload: Mapping[str, object] = field(default_factory=dict)

    def is_expired(self, now: datetime) -> bool:
        return self.policy.is_expired(self.created_at, now)


@dataclass(frozen=True, slots=True)
class ExportBundle:
    """A portable data-subject export bundle: one section per registered store (EVAL-PRIV-002)."""

    subject_id: str
    generated_at: datetime
    sections: Mapping[str, Mapping[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {
            "subject_id": self.subject_id,
            "generated_at": self.generated_at.isoformat(),
            "sections": {store: dict(data) for store, data in self.sections.items()},
        }


def parse_lawful_basis(value: str) -> LawfulBasis:
    """Parse a lawful-basis string, raising a typed error for an unknown value (deny-by-default)."""
    from .errors import LawfulBasisRequired

    try:
        return LawfulBasis(value)
    except ValueError as exc:
        raise LawfulBasisRequired(value) from exc


def parse_data_class(value: str) -> DataClass:
    """Parse a data-class string, raising a typed validation error for an unknown value."""
    from .errors import PrivacyValidationError

    try:
        return DataClass(value)
    except ValueError as exc:
        raise PrivacyValidationError(
            f"{value!r} is not a recognized data classification", code="privacy.class.invalid"
        ) from exc


def parse_rights_type(value: str) -> RightsType:
    """Parse a rights-type string, raising a typed validation error for an unknown value."""
    from .errors import PrivacyValidationError

    try:
        return RightsType(value)
    except ValueError as exc:
        raise PrivacyValidationError(
            f"{value!r} is not a recognized data-subject right", code="privacy.rights.invalid"
        ) from exc


def parse_consent_state(value: str) -> ConsentState:
    """Parse a consent-state string, raising a typed validation error for an unknown value."""
    from .errors import PrivacyValidationError

    try:
        return ConsentState(value)
    except ValueError as exc:
        raise PrivacyValidationError(
            f"{value!r} is not a recognized consent state", code="privacy.consent.invalid"
        ) from exc


__all__: Sequence[str] = [
    "RES_PRIVACY",
    "ConsentRecord",
    "ConsentState",
    "DataCatalog",
    "DataClass",
    "ExportBundle",
    "LawfulBasis",
    "PersonalDataField",
    "RetainedRecord",
    "RetentionPolicy",
    "RightsRequest",
    "RightsStatus",
    "RightsType",
    "class_retention_limit_days",
    "parse_consent_state",
    "parse_data_class",
    "parse_lawful_basis",
    "parse_rights_type",
]
