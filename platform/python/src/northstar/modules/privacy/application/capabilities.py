"""Privacy capabilities: one authoritative implementation per action (LAW-04, GATE-PRIVACY).

Every handler runs through the kernel command/query bus, so each invocation is authorized
deny-by-default and (for commands) audited (rule 50, LAW-14). Tenant scope + acting subject come
from the authenticated :class:`~northstar.kernel.context.RequestContext`, NEVER from the payload
(rule 50). Handlers depend only on :mod:`.ports`, :mod:`.registry` and the pure :mod:`..domain`.

The privacy invariants are enforced here by construction and are never weakened:

* ``privacy.catalog.register`` rejects a personal-data field missing a purpose or a positive
  retention, and honors the stricter-class retention cap (EVAL-PRIV-001, NFR-PRV-001/005).
* ``privacy.consent.record`` appends a new IMMUTABLE consent version that supersedes the prior one;
  ``privacy.consent.history`` returns the auditable ordered history (EVAL-PRIV-002, NFR-PRV-002).
* ``privacy.rights.access/export/erase`` require the authenticated subject (or an authorized
  delegate) — any other caller is rejected before any data is read/exported/deleted; each request is
  recorded with a validated lifecycle (EVAL-PRIV-003, NFR-PRV-003).
* ``privacy.rights.erase`` PROPAGATES the erase across EVERY registered store and asserts the
  deletion residue is zero, failing hard otherwise (EVAL-DATA-009, NFR-PRV-004).
* ``privacy.retention.sweep`` purges expired records deterministically against an INJECTED clock,
  honoring stricter classes (NFR-PRV-005).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from ..domain.errors import (
    DeletionResidueError,
    DuplicateDataField,
    TenantScopeMissing,
    UnauthorizedDataSubject,
)
from ..domain.model import (
    RES_PRIVACY,
    ConsentRecord,
    ConsentState,
    ExportBundle,
    LawfulBasis,
    PersonalDataField,
    RetentionPolicy,
    RightsRequest,
    RightsStatus,
    RightsType,
    parse_consent_state,
    parse_data_class,
    parse_lawful_basis,
)
from .ports import PrivacyRepositoryPort
from .registry import DataSubjectRightsRegistry

CAP_VERSION = "1.0.0"

CAP_CATALOG_REGISTER = "privacy.catalog.register"
CAP_CATALOG_INSPECT = "privacy.catalog.inspect"
CAP_CONSENT_RECORD = "privacy.consent.record"
CAP_CONSENT_HISTORY = "privacy.consent.history"
CAP_RIGHTS_ACCESS = "privacy.rights.access"
CAP_RIGHTS_EXPORT = "privacy.rights.export"
CAP_RIGHTS_ERASE = "privacy.rights.erase"
CAP_RETENTION_SWEEP = "privacy.retention.sweep"

# Commands (state changes / audited) and queries (reads), routed on the matching kernel bus.
PRIVACY_COMMANDS: tuple[str, ...] = (
    CAP_CATALOG_REGISTER,
    CAP_CONSENT_RECORD,
    CAP_RIGHTS_ACCESS,
    CAP_RIGHTS_EXPORT,
    CAP_RIGHTS_ERASE,
    CAP_RETENTION_SWEEP,
)
PRIVACY_QUERIES: tuple[str, ...] = (
    CAP_CATALOG_INSPECT,
    CAP_CONSENT_HISTORY,
)
PRIVACY_CAPABILITIES: tuple[str, ...] = PRIVACY_COMMANDS + PRIVACY_QUERIES

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Invocation helpers (context is authoritative, never the payload — rule 50)
# ---------------------------------------------------------------------------


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    scope = getattr(context, "tenant_scope", None)
    if not scope:
        raise TenantScopeMissing()
    return scope


def _actor_id(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    actor = getattr(context, "actor", None)
    subject = getattr(actor, "id", None)
    if not subject:
        raise TenantScopeMissing()
    return str(subject)


def _delegated_by(invocation: object) -> str | None:
    context = getattr(invocation, "context", None)
    actor = getattr(context, "actor", None)
    delegated = getattr(actor, "delegated_by", None)
    return str(delegated) if delegated else None


def _authorized_subject(invocation: object, requested_subject: str | None) -> str:
    """Resolve + authorize the target data subject (EVAL-PRIV-003, deny-by-default).

    The authenticated actor may exercise rights only for itself or for a subject that delegated to
    it (``actor.delegated_by``). Any other target is rejected before any personal data is touched.
    """
    actor_id = _actor_id(invocation)
    target = requested_subject or actor_id
    if target == actor_id or target == _delegated_by(invocation):
        return target
    raise UnauthorizedDataSubject(actor_id, target)


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegisterFieldCommand:
    field_id: str
    module_id: str
    name: str
    purpose: str
    lawful_basis: str
    data_class: str
    retention_days: int
    description: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterFieldResult:
    field_id: str
    module_id: str
    data_class: str
    retention_days: int
    purpose: str
    lawful_basis: str


@dataclass(frozen=True, slots=True)
class InspectCatalogQuery:
    pass


@dataclass(frozen=True, slots=True)
class FieldView:
    field_id: str
    module_id: str
    name: str
    purpose: str
    lawful_basis: str
    data_class: str
    retention_days: int


@dataclass(frozen=True, slots=True)
class InspectCatalogResult:
    fields: tuple[FieldView, ...]


@dataclass(frozen=True, slots=True)
class RecordConsentCommand:
    purpose: str
    category: str
    state: str = ConsentState.GRANTED.value
    lawful_basis: str = LawfulBasis.CONSENT.value
    subject_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConsentResult:
    record_id: str
    subject_id: str
    purpose: str
    category: str
    state: str
    granted: bool
    version: int


@dataclass(frozen=True, slots=True)
class ConsentHistoryQuery:
    purpose: str
    subject_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConsentHistoryResult:
    subject_id: str
    purpose: str
    versions: tuple[ConsentResult, ...]


@dataclass(frozen=True, slots=True)
class AccessCommand:
    subject_id: str | None = None


@dataclass(frozen=True, slots=True)
class StoreInventory:
    store_id: str
    item_count: int


@dataclass(frozen=True, slots=True)
class AccessResult:
    request_id: str
    subject_id: str
    inventory: tuple[StoreInventory, ...]
    fields: tuple[FieldView, ...]


@dataclass(frozen=True, slots=True)
class ExportCommand:
    subject_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExportResult:
    request_id: str
    subject_id: str
    store_ids: tuple[str, ...]
    bundle: dict[str, object]


@dataclass(frozen=True, slots=True)
class EraseCommand:
    subject_id: str | None = None


@dataclass(frozen=True, slots=True)
class EraseResult:
    request_id: str
    subject_id: str
    erased_by_store: dict[str, int]
    deletion_residue: int


@dataclass(frozen=True, slots=True)
class SweepCommand:
    pass


@dataclass(frozen=True, slots=True)
class SweepResult:
    swept_at: str
    purged_by_store: dict[str, int]
    total_purged: int


# ---------------------------------------------------------------------------
# Handlers (one authoritative implementation per capability)
# ---------------------------------------------------------------------------


def _field_view(personal_field: PersonalDataField) -> FieldView:
    return FieldView(
        field_id=personal_field.field_id,
        module_id=personal_field.module_id,
        name=personal_field.name,
        purpose=personal_field.purpose,
        lawful_basis=personal_field.lawful_basis.value,
        data_class=personal_field.data_class.value,
        retention_days=personal_field.retention.retention_days,
    )


class RegisterDataField:
    """``privacy.catalog.register`` — validate + register a personal-data field (EVAL-PRIV-001)."""

    def __init__(self, *, repository: PrivacyRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> RegisterFieldResult:
        cmd = _typed(request, RegisterFieldCommand)
        organization_id = _tenant(request)
        # Domain construction rejects a missing purpose / non-positive or class-exceeding retention.
        retention = RetentionPolicy(
            data_class=parse_data_class(cmd.data_class),
            retention_days=cmd.retention_days,
        )
        personal_field = PersonalDataField(
            field_id=cmd.field_id,
            module_id=cmd.module_id,
            name=cmd.name,
            purpose=cmd.purpose,
            lawful_basis=parse_lawful_basis(cmd.lawful_basis),
            retention=retention,
            description=cmd.description,
        )
        if self._repo.get_field(organization_id=organization_id, field_id=cmd.field_id) is not None:
            raise DuplicateDataField(cmd.field_id)
        self._repo.add_field(organization_id=organization_id, field=personal_field)
        return RegisterFieldResult(
            field_id=personal_field.field_id,
            module_id=personal_field.module_id,
            data_class=personal_field.data_class.value,
            retention_days=personal_field.retention.retention_days,
            purpose=personal_field.purpose,
            lawful_basis=personal_field.lawful_basis.value,
        )


class InspectCatalog:
    """``privacy.catalog.inspect`` — list the registered personal-data catalog for the tenant."""

    def __init__(self, *, repository: PrivacyRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> InspectCatalogResult:
        _typed(request, InspectCatalogQuery)
        organization_id = _tenant(request)
        fields = self._repo.list_fields(organization_id=organization_id)
        return InspectCatalogResult(fields=tuple(_field_view(f) for f in fields))


class RecordConsent:
    """``privacy.consent.record`` — append an immutable consent version (EVAL-PRIV-002)."""

    def __init__(
        self, *, repository: PrivacyRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id = id_factory

    def handle(self, request: object) -> ConsentResult:
        cmd = _typed(request, RecordConsentCommand)
        organization_id = _tenant(request)
        subject_id = _authorized_subject(request, cmd.subject_id)
        state = parse_consent_state(cmd.state)
        basis = parse_lawful_basis(cmd.lawful_basis)
        now = self._clock()
        latest = self._repo.latest_consent(
            organization_id=organization_id, subject_id=subject_id, purpose=cmd.purpose
        )
        if latest is None:
            record = ConsentRecord(
                record_id=self._id(),
                organization_id=organization_id,
                subject_id=subject_id,
                purpose=cmd.purpose,
                category=cmd.category,
                state=state,
                lawful_basis=basis,
                version=1,
                created_at=now,
            )
        else:
            record = latest.supersede(
                record_id=self._id(), state=state, lawful_basis=basis, created_at=now
            )
        self._repo.add_consent(organization_id=organization_id, record=record)
        return _consent_result(record)


class ConsentHistory:
    """``privacy.consent.history`` — the auditable, ordered consent history (EVAL-PRIV-002)."""

    def __init__(self, *, repository: PrivacyRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ConsentHistoryResult:
        params = _typed(request, ConsentHistoryQuery)
        organization_id = _tenant(request)
        subject_id = _authorized_subject(request, params.subject_id)
        history = self._repo.consent_history(
            organization_id=organization_id, subject_id=subject_id, purpose=params.purpose
        )
        return ConsentHistoryResult(
            subject_id=subject_id,
            purpose=params.purpose,
            versions=tuple(_consent_result(record) for record in history),
        )


class _RightsHandlerBase:
    def __init__(
        self,
        *,
        repository: PrivacyRepositoryPort,
        registry: DataSubjectRightsRegistry,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._registry = registry
        self._clock = clock
        self._id = id_factory

    def _open_request(
        self, *, organization_id: str, subject_id: str, requested_by: str, rights_type: RightsType
    ) -> RightsRequest:
        request_record = RightsRequest(
            request_id=self._id(),
            organization_id=organization_id,
            subject_id=subject_id,
            requested_by=requested_by,
            rights_type=rights_type,
            status=RightsStatus.PENDING,
            created_at=self._clock(),
        )
        self._repo.add_request(organization_id=organization_id, request=request_record)
        return request_record


class AccessRights(_RightsHandlerBase):
    """``privacy.rights.access`` — show what personal data is held for a subject (EVAL-PRIV-003)."""

    def handle(self, request: object) -> AccessResult:
        cmd = _typed(request, AccessCommand)
        organization_id = _tenant(request)
        requested_by = _actor_id(request)
        subject_id = _authorized_subject(request, cmd.subject_id)
        request_record = self._open_request(
            organization_id=organization_id,
            subject_id=subject_id,
            requested_by=requested_by,
            rights_type=RightsType.ACCESS,
        )
        inventory = tuple(
            StoreInventory(
                store_id=handler.store_id,
                item_count=handler.count_subject(
                    organization_id=organization_id, subject_id=subject_id
                ),
            )
            for handler in self._registry.erasers()
        )
        fields = tuple(
            _field_view(f) for f in self._repo.list_fields(organization_id=organization_id)
        )
        self._repo.update_request(
            organization_id=organization_id, request=request_record.complete(self._clock())
        )
        return AccessResult(
            request_id=request_record.request_id,
            subject_id=subject_id,
            inventory=inventory,
            fields=fields,
        )


class ExportRights(_RightsHandlerBase):
    """``privacy.rights.export`` — a portable bundle across registered stores (EVAL-PRIV-002)."""

    def handle(self, request: object) -> ExportResult:
        cmd = _typed(request, ExportCommand)
        organization_id = _tenant(request)
        requested_by = _actor_id(request)
        subject_id = _authorized_subject(request, cmd.subject_id)
        request_record = self._open_request(
            organization_id=organization_id,
            subject_id=subject_id,
            requested_by=requested_by,
            rights_type=RightsType.EXPORT,
        )
        sections = {
            handler.store_id: handler.export_subject(
                organization_id=organization_id, subject_id=subject_id
            )
            for handler in self._registry.exporters()
        }
        bundle = ExportBundle(subject_id=subject_id, generated_at=self._clock(), sections=sections)
        self._repo.update_request(
            organization_id=organization_id, request=request_record.complete(self._clock())
        )
        return ExportResult(
            request_id=request_record.request_id,
            subject_id=subject_id,
            store_ids=tuple(sorted(sections)),
            bundle=bundle.to_dict(),
        )


class EraseRights(_RightsHandlerBase):
    """``privacy.rights.erase`` — propagate erasure until deletion_residue == 0 (EVAL-DATA-009)."""

    def handle(self, request: object) -> EraseResult:
        cmd = _typed(request, EraseCommand)
        organization_id = _tenant(request)
        requested_by = _actor_id(request)
        subject_id = _authorized_subject(request, cmd.subject_id)
        request_record = self._open_request(
            organization_id=organization_id,
            subject_id=subject_id,
            requested_by=requested_by,
            rights_type=RightsType.ERASE,
        )
        erased_by_store: dict[str, int] = {}
        for handler in self._registry.erasers():
            erased_by_store[handler.store_id] = handler.erase_subject(
                organization_id=organization_id, subject_id=subject_id
            )
        residue = 0
        residual_stores: list[str] = []
        for handler in self._registry.erasers():
            remaining = handler.count_subject(
                organization_id=organization_id, subject_id=subject_id
            )
            if remaining > 0:
                residue += remaining
                residual_stores.append(handler.store_id)
        if residue > 0:
            self._repo.update_request(
                organization_id=organization_id, request=request_record.fail(self._clock())
            )
            raise DeletionResidueError(subject_id, residue, tuple(residual_stores))
        self._repo.update_request(
            organization_id=organization_id, request=request_record.complete(self._clock())
        )
        return EraseResult(
            request_id=request_record.request_id,
            subject_id=subject_id,
            erased_by_store=erased_by_store,
            deletion_residue=0,
        )


class SweepRetention:
    """``privacy.retention.sweep`` — clock-driven deterministic retention purge (NFR-PRV-005)."""

    def __init__(self, *, registry: DataSubjectRightsRegistry, clock: Clock) -> None:
        self._registry = registry
        self._clock = clock

    def handle(self, request: object) -> SweepResult:
        _typed(request, SweepCommand)
        organization_id = _tenant(request)
        now = self._clock()
        purged_by_store: dict[str, int] = {}
        for handler in self._registry.erasers():
            purged_by_store[handler.store_id] = handler.purge_expired(
                organization_id=organization_id, now=now
            )
        return SweepResult(
            swept_at=now.isoformat(),
            purged_by_store=purged_by_store,
            total_purged=sum(purged_by_store.values()),
        )


def _consent_result(record: ConsentRecord) -> ConsentResult:
    return ConsentResult(
        record_id=record.record_id,
        subject_id=record.subject_id,
        purpose=record.purpose,
        category=record.category,
        state=record.state.value,
        granted=record.granted,
        version=record.version,
    )


def registered_capabilities() -> Sequence[str]:
    """The privacy capabilities registered on the kernel (LAW-04)."""
    return PRIVACY_CAPABILITIES


__all__ = [
    "CAP_CATALOG_INSPECT",
    "CAP_CATALOG_REGISTER",
    "CAP_CONSENT_HISTORY",
    "CAP_CONSENT_RECORD",
    "CAP_RETENTION_SWEEP",
    "CAP_RIGHTS_ACCESS",
    "CAP_RIGHTS_ERASE",
    "CAP_RIGHTS_EXPORT",
    "CAP_VERSION",
    "PRIVACY_CAPABILITIES",
    "PRIVACY_COMMANDS",
    "PRIVACY_QUERIES",
    "RES_PRIVACY",
    "AccessCommand",
    "AccessResult",
    "AccessRights",
    "ConsentHistory",
    "ConsentHistoryQuery",
    "ConsentHistoryResult",
    "ConsentResult",
    "EraseCommand",
    "EraseResult",
    "EraseRights",
    "ExportCommand",
    "ExportResult",
    "ExportRights",
    "FieldView",
    "InspectCatalog",
    "InspectCatalogQuery",
    "InspectCatalogResult",
    "RecordConsent",
    "RecordConsentCommand",
    "RegisterDataField",
    "RegisterFieldCommand",
    "RegisterFieldResult",
    "StoreInventory",
    "SweepCommand",
    "SweepResult",
    "SweepRetention",
    "registered_capabilities",
]
