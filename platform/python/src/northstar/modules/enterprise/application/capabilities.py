"""Enterprise capabilities: one authoritative implementation per action (LAW-04).

Each handler runs through the kernel command/query bus, so every mutation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope is taken from the authenticated
:class:`RequestContext` (``tenant_scope``), **never** from the payload (rule 50).

The five authoritative capabilities:

* ``enterprise.federation.login`` — verify an external IdP assertion via a signature-verified port
  and map it DETERMINISTICALLY to a Northstar subject through the identity capability; a
  forged/unverified/expired assertion is rejected uniformly (FR-IDN-006, EVAL-IDN-005).
* ``enterprise.scim.provision`` — create/update a SCIM user or group idempotently; a user resolves
  to (or provisions) a Northstar subject via the identity-reusing gateway.
* ``enterprise.scim.deprovision`` — deactivate a provisioning record AND disable the linked
  subject's access by reusing identity session invalidation (idempotent + audited).
* ``enterprise.lti.launch`` — verify a signed LTI launch and map it to an authorized learning
  context; an invalid launch is rejected (FR-LRN-008).
* ``enterprise.xapi.emit`` — map a first-party learning progress event to an xAPI statement and
  emit it to a configured LRS, gated deny-by-default on the learner's export consent; disabling
  the adapter changes nothing about first-party learning state (FR-LRN-008 independence).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from northstar.kernel.context import Actor

from ..domain.errors import (
    ConsentRequired,
    FederationAssertionRejected,
    LtiLaunchRejected,
    ProvisioningRecordNotFound,
    TenantScopeMissing,
)
from ..domain.model import (
    FederatedIdentityMapping,
    FederationAssertion,
    LearningContextGrant,
    LtiLaunch,
    ProvisioningRecord,
    ProvisioningResourceType,
    build_progress_statement,
)
from .ports import (
    EnterpriseRepositoryPort,
    ExportConsentPort,
    FederationVerifierPort,
    LrsPort,
    LtiVerifierPort,
    ScimProvisioningPort,
)

CAP_VERSION = "1.0.0"

CAP_FEDERATION_LOGIN = "enterprise.federation.login"
CAP_SCIM_PROVISION = "enterprise.scim.provision"
CAP_SCIM_DEPROVISION = "enterprise.scim.deprovision"
CAP_LTI_LAUNCH = "enterprise.lti.launch"
CAP_XAPI_EMIT = "enterprise.xapi.emit"

ENTERPRISE_CAPABILITIES: tuple[str, ...] = (
    CAP_FEDERATION_LOGIN,
    CAP_SCIM_PROVISION,
    CAP_SCIM_DEPROVISION,
    CAP_LTI_LAUNCH,
    CAP_XAPI_EMIT,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FederationLoginCommand:
    assertion: FederationAssertion


@dataclass(frozen=True, slots=True)
class FederationLoginResult:
    subject_id: str
    user_id: str
    issuer: str
    external_subject: str
    provisioned: bool


@dataclass(frozen=True, slots=True)
class ScimProvisionCommand:
    external_id: str
    resource_type: ProvisioningResourceType = ProvisioningResourceType.USER
    active: bool = True
    email: str | None = None
    display_name: str | None = None
    members: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class ScimDeprovisionCommand:
    external_id: str


@dataclass(frozen=True, slots=True)
class ProvisioningResult:
    record_id: str
    external_id: str
    resource_type: str
    active: bool
    subject_id: str | None
    created: bool
    sessions_invalidated: int = 0


@dataclass(frozen=True, slots=True)
class LtiLaunchCommand:
    launch: LtiLaunch


@dataclass(frozen=True, slots=True)
class LtiLaunchResult:
    issuer: str
    context_id: str
    resource_link_id: str
    subject: str
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class XapiEmitCommand:
    subject_id: str
    course_id: str
    course_title: str
    completed: bool = False
    registration: str | None = None


@dataclass(frozen=True, slots=True)
class XapiEmitResult:
    statement_id: str
    stored: bool
    verb_id: str
    object_id: str


# ---------------------------------------------------------------------------
# Shared helpers
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


def _actor(invocation: object) -> Actor:
    context = getattr(invocation, "context", None)
    return context.actor


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class FederatedLogin:
    """``enterprise.federation.login`` — verify an IdP assertion, map to a subject (EVAL-IDN-005).

    A verified assertion resolves-or-provisions a Northstar subject through the identity-reusing
    gateway and records an enterprise-owned :class:`FederatedIdentityMapping`. The mapping is
    DETERMINISTIC: the same verified ``(issuer, subject)`` in a tenant always resolves to the same
    Northstar subject (idempotent). Any unverified/forged/expired assertion fails verification and
    is rejected uniformly — the identity core is never touched (FR-IDN-006).
    """

    def __init__(
        self,
        *,
        verifier: FederationVerifierPort,
        gateway: ScimProvisioningPort,
        repository: EnterpriseRepositoryPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._verifier = verifier
        self._gateway = gateway
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> FederationLoginResult:
        command = _typed(request, FederationLoginCommand)
        organization_id = _tenant(request)
        now = self._clock()

        claims = self._verifier.verify(command.assertion, now=now)
        if claims is None:
            # Fail closed + uniform: a forged/tampered/unsigned/expired/mis-issued assertion is
            # indistinguishable to the caller (anti-enumeration, EVAL-IDN-005).
            raise FederationAssertionRejected()

        existing = self._repo.get_mapping(
            organization_id=organization_id,
            issuer=claims.issuer,
            external_subject=claims.subject,
        )
        if existing is not None:
            return FederationLoginResult(
                subject_id=existing.subject_id,
                user_id=existing.user_id,
                issuer=existing.issuer,
                external_subject=existing.external_subject,
                provisioned=False,
            )

        provisioned = self._gateway.resolve_or_provision(
            issuer=claims.issuer,
            external_subject=claims.subject,
            email=claims.email,
            display_name=claims.display_name,
            tenant_scope=organization_id,
        )
        mapping = FederatedIdentityMapping(
            mapping_id=self._id_factory(),
            organization_id=organization_id,
            issuer=claims.issuer,
            external_subject=claims.subject,
            subject_id=provisioned.subject_id,
            user_id=provisioned.user_id,
            linked_at=now,
        )
        self._repo.add_mapping(mapping)
        return FederationLoginResult(
            subject_id=provisioned.subject_id,
            user_id=provisioned.user_id,
            issuer=claims.issuer,
            external_subject=claims.subject,
            provisioned=provisioned.created,
        )


class ScimProvision:
    """``enterprise.scim.provision`` — create/update a SCIM user or group idempotently.

    A user resolves-or-provisions a Northstar subject through the identity-reusing gateway; a group
    persists its membership. Re-provisioning the same ``external_id`` updates the existing record
    (idempotent) — never a duplicate. Every provisioning is audited by the command bus (LAW-14).
    """

    def __init__(
        self,
        *,
        repository: EnterpriseRepositoryPort,
        gateway: ScimProvisioningPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._gateway = gateway
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> ProvisioningResult:
        command = _typed(request, ScimProvisionCommand)
        organization_id = _tenant(request)
        now = self._clock()

        subject_id: str | None = None
        created = False
        if command.resource_type is ProvisioningResourceType.USER:
            provisioned = self._gateway.resolve_or_provision(
                issuer="scim://enterprise",
                external_subject=command.external_id,
                email=command.email,
                display_name=command.display_name,
                tenant_scope=organization_id,
            )
            subject_id = provisioned.subject_id
            created = provisioned.created

        existing = self._repo.get_provisioning_record(
            organization_id=organization_id, external_id=command.external_id
        )
        if existing is None:
            record = ProvisioningRecord(
                record_id=self._id_factory(),
                organization_id=organization_id,
                resource_type=command.resource_type,
                external_id=command.external_id,
                active=True,
                provisioned_at=now,
                updated_at=now,
                subject_id=subject_id,
                display_name=command.display_name,
                email=command.email,
                members=command.members,
            )
            self._repo.add_provisioning_record(record)
            record_created = True
        else:
            record = existing.updated(
                display_name=command.display_name,
                email=command.email,
                members=command.members,
                subject_id=subject_id,
                now=now,
            )
            self._repo.update_provisioning_record(record)
            record_created = False

        return ProvisioningResult(
            record_id=record.record_id,
            external_id=record.external_id,
            resource_type=record.resource_type.value,
            active=record.active,
            subject_id=record.subject_id,
            created=record_created or created,
        )


class ScimDeprovision:
    """``enterprise.scim.deprovision`` — deactivate a record + disable the subject's access.

    Flips the provisioning record to inactive and, when it links a subject, reuses identity's
    session invalidation to disable that subject's access. Idempotent: deprovisioning an already
    inactive record is a no-op that still reports success (and re-invalidates any live sessions).
    """

    def __init__(
        self,
        *,
        repository: EnterpriseRepositoryPort,
        gateway: ScimProvisioningPort,
        clock: Clock,
    ) -> None:
        self._repo = repository
        self._gateway = gateway
        self._clock = clock

    def handle(self, request: object) -> ProvisioningResult:
        command = _typed(request, ScimDeprovisionCommand)
        organization_id = _tenant(request)
        now = self._clock()

        record = self._repo.get_provisioning_record(
            organization_id=organization_id, external_id=command.external_id
        )
        if record is None:
            raise ProvisioningRecordNotFound()

        deactivated = record.deactivated(now=now)
        self._repo.update_provisioning_record(deactivated)

        sessions_invalidated = 0
        if deactivated.subject_id is not None:
            sessions_invalidated = self._gateway.disable_subject(deactivated.subject_id)

        return ProvisioningResult(
            record_id=deactivated.record_id,
            external_id=deactivated.external_id,
            resource_type=deactivated.resource_type.value,
            active=deactivated.active,
            subject_id=deactivated.subject_id,
            created=False,
            sessions_invalidated=sessions_invalidated,
        )


class LaunchLti:
    """``enterprise.lti.launch`` — verify a signed LTI launch and map to a learning context.

    A genuine, unexpired, correctly-signed launch maps to an authorized learning context
    (``context_id`` + ``resource_link_id``); an invalid/forged/expired/unsigned launch is rejected
    (FR-LRN-008, EVAL-INT-001).
    """

    def __init__(self, *, verifier: LtiVerifierPort, clock: Clock) -> None:
        self._verifier = verifier
        self._clock = clock

    def handle(self, request: object) -> LtiLaunchResult:
        command = _typed(request, LtiLaunchCommand)
        _tenant(request)  # LTI launches are tenant-scoped operations (deny without a scope).
        now = self._clock()
        if not self._verifier.verify(command.launch, now=now):
            raise LtiLaunchRejected()
        grant = LearningContextGrant(
            issuer=command.launch.issuer,
            context_id=command.launch.context_id,
            resource_link_id=command.launch.resource_link_id,
            subject=command.launch.subject,
            roles=command.launch.roles,
        )
        return LtiLaunchResult(
            issuer=grant.issuer,
            context_id=grant.context_id,
            resource_link_id=grant.resource_link_id,
            subject=grant.subject,
            roles=grant.roles,
        )


class EmitXapi:
    """``enterprise.xapi.emit`` — map a learning progress event to xAPI + emit to the LRS.

    Deny-by-default consent: an xAPI statement leaves the platform ONLY when the learner has export
    consent (else :class:`ConsentRequired`, nothing is emitted). The statement is a pure projection
    of a first-party progress event (:func:`build_progress_statement`) — it reads learning state and
    never writes it, so disabling this adapter changes nothing about first-party learning state
    (FR-LRN-008 independence).
    """

    def __init__(
        self,
        *,
        lrs: LrsPort,
        consent: ExportConsentPort,
        clock: Clock,
    ) -> None:
        self._lrs = lrs
        self._consent = consent
        self._clock = clock

    def handle(self, request: object) -> XapiEmitResult:
        command = _typed(request, XapiEmitCommand)
        organization_id = _tenant(request)
        if not self._consent.has_export_consent(
            organization_id=organization_id, subject_id=command.subject_id
        ):
            raise ConsentRequired()
        statement = build_progress_statement(
            subject_id=command.subject_id,
            course_id=command.course_id,
            course_title=command.course_title,
            completed=command.completed,
            timestamp=self._clock(),
            registration=command.registration,
        )
        receipt = self._lrs.emit(statement)
        return XapiEmitResult(
            statement_id=receipt.statement_id,
            stored=receipt.stored,
            verb_id=statement.verb_id,
            object_id=statement.object_id,
        )
