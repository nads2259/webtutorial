"""Typed governance domain errors (rule 30/40): explainable, deterministic diagnostics.

The governance domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary. The kernel error base carries the diagnostics.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class GovernanceError(KernelError):
    """Base class for governance domain errors."""


class GovernanceInvariantViolation(GovernanceError):  # noqa: N818 canonical error name
    """A governance invariant was violated (missing approver/expiry, empty rationale, bad link)."""

    def __init__(self, message: str, code: str = "governance.invariant.violated") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class DecisionNotFound(GovernanceInvariantViolation):
    """A decision record is absent in the caller's tenant scope (fail closed, do not disclose)."""

    def __init__(self) -> None:
        super().__init__(
            "governance decision record is not available in this scope",
            code="governance.decision.not_found",
        )


class ControlExceptionNotFound(GovernanceInvariantViolation):
    """A control exception is absent in the caller's tenant scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "control exception is not available in this scope",
            code="governance.exception.not_found",
        )


class TenantScopeMissing(GovernanceInvariantViolation):
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "a tenant scope is required for this operation",
            code="governance.tenant.missing",
        )
