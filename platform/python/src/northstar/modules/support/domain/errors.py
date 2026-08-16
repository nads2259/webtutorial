"""Typed support domain errors (rule 30/40): explainable, deterministic diagnostics.

The support domain raises these typed errors rather than bare strings; adapters map them to RFC 9457
problem details at the trust boundary (rule 40).
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class SupportError(KernelError):
    """Base class for support domain errors."""


class IntakeValidationError(SupportError):
    """Support intake input is malformed / oversized / injection-shaped (FR-SUP-001).

    Deny-by-default: an intake submission that violates length bounds, is empty, or contains
    injection-shaped markup/script is rejected and never becomes a case.
    """

    def __init__(self, message: str, code: str = "support.intake.invalid") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class CaseNotFound(SupportError):  # noqa: N818 canonical error name
    """A referenced support case does not exist in this tenant (deny-by-default)."""

    def __init__(self, case_id: str) -> None:
        message = f"support case {case_id!r} was not found"
        super().__init__(message, (Diagnostic(code="support.case.not_found", message=message),))
        self.case_id = case_id


class InvalidLifecycleTransition(SupportError):  # noqa: N818 canonical error name
    """A requested support-case status transition is not allowed (FR-SUP-002)."""

    def __init__(self, current: str, requested: str) -> None:
        message = f"support case cannot transition from {current!r} to {requested!r}"
        super().__init__(
            message, (Diagnostic(code="support.case.invalid_transition", message=message),)
        )
        self.current = current
        self.requested = requested


class SupportAccessDenied(SupportError):  # noqa: N818 canonical error name
    """An elevated/broad support read was attempted without an active support-access grant.

    Deny-by-default (FR-SUP-003): support staff see only the minimum data; a broad/elevated read
    requires an audited, time-bounded grant. The attempt is refused AND logged.
    """

    def __init__(self, staff_id: str, case_id: str) -> None:
        message = (
            "elevated support access is denied without an active, time-bounded support-access grant"
        )
        super().__init__(message, (Diagnostic(code="support.access.denied", message=message),))
        self.staff_id = staff_id
        self.case_id = case_id


class SupportAccessInvalid(SupportError):  # noqa: N818 canonical error name
    """A support-access grant was requested with invalid (non-time-bounded) parameters."""

    def __init__(self, message: str) -> None:
        super().__init__(message, (Diagnostic(code="support.access.invalid", message=message),))


class TenantScopeMissing(SupportError):  # noqa: N818 canonical error name
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        message = "a tenant scope is required for this operation"
        super().__init__(message, (Diagnostic(code="support.tenant.missing", message=message),))
