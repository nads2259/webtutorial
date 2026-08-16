"""Typed enterprise domain errors (rule 30/40): explainable, deterministic diagnostics.

The enterprise domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary. The kernel error base carries the diagnostics so a
rejection (forged/expired assertion, unsigned LTI launch, missing consent) is explainable and
referenceable from the audit trail without leaking a raw dict.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class EnterpriseError(KernelError):
    """Base class for enterprise domain errors."""


class EnterpriseInvariantViolation(EnterpriseError):  # noqa: N818 canonical error name
    """An enterprise domain invariant was violated (empty issuer/subject/external id, …)."""

    def __init__(self, message: str, code: str = "enterprise.invariant.violated") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class FederationAssertionRejected(EnterpriseInvariantViolation):
    """A federated IdP assertion could not be trusted (unverified/forged/expired/mis-issued).

    The message is deliberately uniform so a caller cannot distinguish *why* an assertion was
    rejected (anti-enumeration): every failure path returns the same rejection (EVAL-IDN-005).
    """

    def __init__(self, reason: str = "assertion_rejected") -> None:
        self.reason = reason
        super().__init__(
            "the federated identity assertion could not be verified",
            code="enterprise.federation.rejected",
        )


class LtiLaunchRejected(EnterpriseInvariantViolation):
    """A signed LTI launch could not be verified (unsigned/forged/expired/unauthorized context)."""

    def __init__(self, reason: str = "launch_rejected") -> None:
        self.reason = reason
        super().__init__(
            "the LTI launch could not be verified",
            code="enterprise.lti.rejected",
        )


class ConsentRequired(EnterpriseInvariantViolation):
    """xAPI emission was requested without the learner's export consent (deny-by-default)."""

    def __init__(self) -> None:
        super().__init__(
            "xAPI emission requires the learner's export consent",
            code="enterprise.xapi.consent_required",
        )


class ProvisioningRecordNotFound(EnterpriseInvariantViolation):
    """A SCIM provisioning record is absent in the caller's tenant scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "provisioning record is not available in this scope",
            code="enterprise.scim.not_found",
        )


class TenantScopeMissing(EnterpriseInvariantViolation):
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "a tenant scope is required for this operation",
            code="enterprise.tenant.missing",
        )
