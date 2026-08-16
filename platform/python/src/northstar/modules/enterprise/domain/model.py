"""Enterprise domain: pure value objects + deterministic mapping functions (rule 10, LAW-02).

No infrastructure imports (the only import is the kernel actor value object). Four aggregates and
the pure helpers that bind them to the canonical signing material:

* :class:`FederationAssertion` — a raw OIDC/SAML-shaped external IdP assertion (issuer, external
  subject, audience, validity window, signature). :func:`federation_signing_payload` produces the
  canonical bytes a verifier signs/checks; the domain never verifies the signature itself (that is
  a port/adapter concern) — it only defines the deterministic material and the validity window.
* :class:`FederatedIdentityMapping` — the enterprise-owned record linking a verified external
  identity ``(issuer, external_subject)`` to a Northstar ``subject_id``/``user_id`` in a tenant.
  The subject/user themselves live in the identity module; this is the linkage the federation
  adapter persists (FR-IDN-006).
* :class:`ProvisioningRecord` — a SCIM-shaped user OR group record (RFC 7643 subset) with an
  ``active`` flag; deprovisioning flips ``active`` to ``False`` and stamps ``deactivated_at``.
* :class:`LtiLaunch` — a signed LTI-shaped launch request mapping to an authorized learning
  context. :func:`lti_signing_payload` produces the canonical bytes the verifier checks.
* :class:`XapiStatement` — an xAPI-shaped statement (actor/verb/object) emitted to an LRS.
  :func:`build_progress_statement` deterministically maps a first-party learning progress event to
  an xAPI statement; the internal model stays authoritative and maps explicitly (FR-LRN-008).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from .errors import EnterpriseInvariantViolation

RES_ENTERPRISE_FEDERATION = "enterprise.federation"
RES_ENTERPRISE_PROVISIONING = "enterprise.provisioning"
RES_ENTERPRISE_LTI = "enterprise.lti"
RES_ENTERPRISE_XAPI = "enterprise.xapi"

SCHEMA_VERSION = "1.0"

_FIELD_SEP = "\x1f"  # unit separator — unambiguous, cannot appear in the joined field values


def _require(condition: bool, message: str, *, code: str) -> None:
    if not condition:
        raise EnterpriseInvariantViolation(message, code=code)


# ---------------------------------------------------------------------------
# Federation (OIDC/SAML-shaped external IdP assertion)  (FR-IDN-006)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FederationAssertion:
    """A signature-bearing external IdP assertion presented at the federation boundary.

    ``signature`` is verified by a :class:`FederationVerifierPort` adapter against
    :func:`federation_signing_payload`; the domain only guarantees the fields are well-formed and
    exposes the validity window. Email is intentionally NOT an identity key (docs/07 §2): the
    stable key is ``(issuer, subject)``.
    """

    issuer: str
    subject: str
    audience: str
    issued_at: datetime
    expires_at: datetime
    signature: str
    email: str | None = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.issuer), "assertion issuer must be non-empty", code="enterprise.fed.iss")
        _require(
            bool(self.subject), "assertion subject must be non-empty", code="enterprise.fed.sub"
        )
        _require(
            bool(self.audience), "assertion audience must be non-empty", code="enterprise.fed.aud"
        )
        _require(
            self.expires_at > self.issued_at,
            "assertion expiry must be after issuance",
            code="enterprise.fed.window",
        )

    def is_within_validity(self, now: datetime) -> bool:
        """True iff ``now`` is inside ``[issued_at, expires_at)`` (an expired assertion is not)."""
        return self.issued_at <= now < self.expires_at


def federation_signing_payload(assertion: FederationAssertion) -> bytes:
    """Canonical, deterministic signing material binding every trust-relevant assertion field.

    Because the material includes the issuer, external subject, audience and validity window,
    TAMPERING with any of them breaks the signature (the verifier recomputes and compares). The
    signature field itself is excluded (it signs the rest).
    """
    parts = (
        "northstar.enterprise.federation.v1",
        assertion.issuer,
        assertion.subject,
        assertion.audience,
        assertion.issued_at.isoformat(),
        assertion.expires_at.isoformat(),
        assertion.email or "",
    )
    return _FIELD_SEP.join(parts).encode("utf-8")


@dataclass(frozen=True, slots=True)
class VerifiedFederationClaims:
    """The trusted claims a :class:`FederationVerifierPort` returns after verifying an assertion."""

    issuer: str
    subject: str
    audience: str
    email: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class FederatedIdentityMapping:
    """The enterprise-owned linkage from a verified external identity to a Northstar subject.

    Deterministic: a given ``(organization_id, issuer, external_subject)`` maps to exactly one
    ``subject_id`` — re-presenting the same verified assertion resolves the same subject, never a
    duplicate (FR-IDN-006, EVAL-IDN-005).
    """

    mapping_id: str
    organization_id: str
    issuer: str
    external_subject: str
    subject_id: str
    user_id: str
    linked_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.mapping_id), "mapping id must be non-empty", code="enterprise.map.id")
        _require(
            bool(self.organization_id),
            "mapping must be tenant-scoped",
            code="enterprise.map.tenant",
        )
        _require(
            bool(self.subject_id),
            "mapping must reference a Northstar subject",
            code="enterprise.map.subject",
        )


# ---------------------------------------------------------------------------
# SCIM provisioning (user + group, RFC 7643 subset)  (FR-IDN-006)
# ---------------------------------------------------------------------------


class ProvisioningResourceType(StrEnum):
    """The SCIM resource kinds enterprise provisioning manages."""

    USER = "user"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class ProvisioningRecord:
    """A SCIM-shaped provisioning record for a user or group, with an ``active`` flag.

    Idempotent by ``(organization_id, external_id)``: re-provisioning the same external id updates
    the existing record rather than creating a duplicate. Deprovisioning is expressed by
    :meth:`deactivated` — ``active`` becomes ``False`` and ``deactivated_at`` is stamped, which the
    provisioning capability uses to disable the linked subject's access.
    """

    record_id: str
    organization_id: str
    resource_type: ProvisioningResourceType
    external_id: str
    active: bool
    provisioned_at: datetime
    updated_at: datetime
    subject_id: str | None = None
    display_name: str | None = None
    email: str | None = None
    members: tuple[str, ...] = ()
    deactivated_at: datetime | None = None

    def __post_init__(self) -> None:
        _require(
            bool(self.record_id),
            "provisioning record id must be non-empty",
            code="enterprise.pr.id",
        )
        _require(
            bool(self.organization_id),
            "provisioning record must be tenant-scoped",
            code="enterprise.pr.tenant",
        )
        _require(
            bool(self.external_id),
            "provisioning record must carry a stable external id",
            code="enterprise.pr.external",
        )

    def updated(
        self,
        *,
        display_name: str | None,
        email: str | None,
        members: tuple[str, ...],
        subject_id: str | None,
        now: datetime,
    ) -> ProvisioningRecord:
        """Return a re-activated copy carrying the latest SCIM attributes (idempotent update)."""
        return replace(
            self,
            active=True,
            display_name=display_name,
            email=email,
            members=members,
            subject_id=subject_id if subject_id is not None else self.subject_id,
            updated_at=now,
            deactivated_at=None,
        )

    def deactivated(self, *, now: datetime) -> ProvisioningRecord:
        """Return a deprovisioned copy (``active=False``); idempotent if already inactive."""
        if not self.active:
            return self
        return replace(self, active=False, updated_at=now, deactivated_at=now)


# ---------------------------------------------------------------------------
# LTI launch (signed, maps to an authorized learning context)  (FR-LRN-008)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LtiLaunch:
    """A signed LTI-shaped launch request from an external platform (LMS).

    ``signature`` is verified by an :class:`LtiVerifierPort` adapter against
    :func:`lti_signing_payload`. A verified launch maps to an authorized learning context
    (``context_id`` + ``resource_link_id``); an invalid/forged/expired launch is rejected.
    """

    issuer: str
    deployment_id: str
    context_id: str
    resource_link_id: str
    subject: str
    issued_at: datetime
    expires_at: datetime
    signature: str
    roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(bool(self.issuer), "LTI issuer must be non-empty", code="enterprise.lti.iss")
        _require(
            bool(self.deployment_id),
            "LTI deployment id must be non-empty",
            code="enterprise.lti.dep",
        )
        _require(
            bool(self.context_id), "LTI context id must be non-empty", code="enterprise.lti.ctx"
        )
        _require(
            bool(self.resource_link_id),
            "LTI resource link id must be non-empty",
            code="enterprise.lti.rlink",
        )
        _require(bool(self.subject), "LTI subject must be non-empty", code="enterprise.lti.sub")
        _require(
            self.expires_at > self.issued_at,
            "LTI launch expiry must be after issuance",
            code="enterprise.lti.window",
        )

    def is_within_validity(self, now: datetime) -> bool:
        return self.issued_at <= now < self.expires_at


def lti_signing_payload(launch: LtiLaunch) -> bytes:
    """Canonical signing material binding every trust-relevant LTI field (excludes signature)."""
    parts = (
        "northstar.enterprise.lti.v1",
        launch.issuer,
        launch.deployment_id,
        launch.context_id,
        launch.resource_link_id,
        launch.subject,
        launch.issued_at.isoformat(),
        launch.expires_at.isoformat(),
    )
    return _FIELD_SEP.join(parts).encode("utf-8")


@dataclass(frozen=True, slots=True)
class LearningContextGrant:
    """The authorized learning context a verified LTI launch maps to (FR-LRN-008)."""

    issuer: str
    context_id: str
    resource_link_id: str
    subject: str
    roles: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# xAPI statement (learning event export)  (FR-LRN-008)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class XapiStatement:
    """A minimal xAPI-shaped statement (ADL xAPI): actor / verb / object (+ optional result).

    Pure projection of a first-party learning event; :meth:`to_dict` renders the canonical shape an
    LRS accepts. Northstar's internal learning model remains authoritative — this maps out of it
    explicitly and never back into it (independence).
    """

    actor_account: str
    verb_id: str
    verb_display: str
    object_id: str
    object_name: str
    timestamp: datetime
    result_completion: bool | None = None
    context_registration: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.actor_account), "xAPI actor is required", code="enterprise.xapi.actor")
        _require(bool(self.verb_id), "xAPI verb id is required", code="enterprise.xapi.verb")
        _require(bool(self.object_id), "xAPI object id is required", code="enterprise.xapi.object")

    def to_dict(self) -> dict[str, Any]:
        statement: dict[str, Any] = {
            "actor": {"objectType": "Agent", "account": {"name": self.actor_account}},
            "verb": {"id": self.verb_id, "display": {"en-US": self.verb_display}},
            "object": {
                "id": self.object_id,
                "definition": {"name": {"en-US": self.object_name}},
            },
            "timestamp": self.timestamp.isoformat(),
        }
        if self.result_completion is not None:
            statement["result"] = {"completion": self.result_completion}
        if self.context_registration is not None:
            statement["context"] = {"registration": self.context_registration}
        return statement


# Canonical xAPI verb ids (ADL registry) used by the deterministic progress mapping.
XAPI_VERB_PROGRESSED = "http://adlnet.gov/expapi/verbs/progressed"
XAPI_VERB_COMPLETED = "http://adlnet.gov/expapi/verbs/completed"
_XAPI_OBJECT_BASE = "https://northstar.example/xapi/activities/course"


def build_progress_statement(
    *,
    subject_id: str,
    course_id: str,
    course_title: str,
    completed: bool,
    timestamp: datetime,
    registration: str | None = None,
) -> XapiStatement:
    """Deterministically map a first-party learning progress event to an xAPI statement.

    A completion maps to the ``completed`` verb (``result.completion = True``); any other advance
    maps to ``progressed``. The mapping is explicit and one-directional — it reads a learning event
    and never mutates learning state (FR-LRN-008 independence).
    """
    verb_id = XAPI_VERB_COMPLETED if completed else XAPI_VERB_PROGRESSED
    verb_display = "completed" if completed else "progressed"
    return XapiStatement(
        actor_account=subject_id,
        verb_id=verb_id,
        verb_display=verb_display,
        object_id=f"{_XAPI_OBJECT_BASE}/{course_id}",
        object_name=course_title or course_id,
        timestamp=timestamp,
        result_completion=True if completed else None,
        context_registration=registration,
    )
