"""Typed privacy domain errors (rule 30/40): explainable, deterministic diagnostics.

The privacy domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary (rule 40). The kernel error base carries the
structured diagnostics. Two errors are trust-boundary critical and must never be softened:
:class:`UnauthorizedDataSubject` (a DSAR was attempted by someone other than the authenticated
subject or an authorized delegate) and :class:`DeletionResidueError` (an erase left personal
data behind in a registered store — a hard privacy failure, EVAL-DATA-009).
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class PrivacyError(KernelError):
    """Base class for privacy & data-subject-rights domain errors."""


class PrivacyValidationError(PrivacyError):
    """A privacy aggregate violates a structural invariant (deny-by-default)."""

    def __init__(self, message: str, code: str = "privacy.invalid") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class PurposeRequired(PrivacyError):  # noqa: N818 canonical error name
    """A personal-data field was registered without a declared purpose (EVAL-PRIV-001)."""

    def __init__(self, field_id: str = "") -> None:
        message = (
            f"personal-data field {field_id!r} must declare a processing purpose"
            if field_id
            else "a personal-data field must declare a processing purpose"
        )
        super().__init__(message, (Diagnostic(code="privacy.purpose.required", message=message),))
        self.field_id = field_id


class RetentionRequired(PrivacyError):  # noqa: N818 canonical error name
    """A personal-data field was registered without a positive retention period (EVAL-PRIV-001)."""

    def __init__(self, field_id: str = "") -> None:
        message = (
            f"personal-data field {field_id!r} must declare a positive retention period"
            if field_id
            else "a personal-data field must declare a positive retention period"
        )
        super().__init__(message, (Diagnostic(code="privacy.retention.required", message=message),))
        self.field_id = field_id


class LawfulBasisRequired(PrivacyError):  # noqa: N818 canonical error name
    """A personal-data field or consent record lacks a recognized lawful basis (EVAL-PRIV-001)."""

    def __init__(self, value: str = "") -> None:
        message = f"{value!r} is not a recognized lawful basis for processing personal data"
        super().__init__(message, (Diagnostic(code="privacy.basis.required", message=message),))
        self.value = value


class RetentionExceedsClassLimit(PrivacyError):  # noqa: N818 canonical error name
    """A field's retention exceeds the stricter cap its data class enforces (NFR-PRV-005)."""

    def __init__(self, data_class: str, retention_days: int, limit_days: int) -> None:
        message = (
            f"retention of {retention_days}d exceeds the {limit_days}d limit for data class "
            f"{data_class!r}; a stricter class caps retention"
        )
        super().__init__(
            message, (Diagnostic(code="privacy.retention.exceeds_class", message=message),)
        )
        self.data_class = data_class
        self.retention_days = retention_days
        self.limit_days = limit_days


class DuplicateDataField(PrivacyError):  # noqa: N818 canonical error name
    """A personal-data field id was registered more than once (one authoritative declaration)."""

    def __init__(self, field_id: str) -> None:
        message = f"personal-data field {field_id!r} is already registered"
        super().__init__(message, (Diagnostic(code="privacy.field.duplicate", message=message),))
        self.field_id = field_id


class DataFieldNotFound(PrivacyError):  # noqa: N818 canonical error name
    """A referenced personal-data field does not exist in this tenant (deny-by-default)."""

    def __init__(self, field_id: str) -> None:
        message = f"personal-data field {field_id!r} was not found"
        super().__init__(message, (Diagnostic(code="privacy.field.not_found", message=message),))
        self.field_id = field_id


class RightsRequestNotFound(PrivacyError):  # noqa: N818 canonical error name
    """A referenced data-subject-rights request does not exist in this tenant."""

    def __init__(self, request_id: str) -> None:
        message = f"rights request {request_id!r} was not found"
        super().__init__(message, (Diagnostic(code="privacy.request.not_found", message=message),))
        self.request_id = request_id


class InvalidRequestTransition(PrivacyError):  # noqa: N818 canonical error name
    """A rights request lifecycle transition is not permitted from the current status."""

    def __init__(self, from_status: str, to_status: str) -> None:
        message = f"cannot transition a rights request from {from_status!r} to {to_status!r}"
        super().__init__(message, (Diagnostic(code="privacy.request.transition", message=message),))
        self.from_status = from_status
        self.to_status = to_status


class UnauthorizedDataSubject(PrivacyError):  # noqa: N818 canonical error name
    """A DSAR targeted a subject the caller is neither (EVAL-PRIV-003, deny-by-default).

    Access/export/erase require the authenticated data subject or an authorized delegate; any
    other caller is rejected before any personal data is read, exported or deleted.
    """

    def __init__(self, requested_by: str, subject_id: str) -> None:
        message = (
            f"actor {requested_by!r} is not authorized to exercise data-subject rights for "
            f"subject {subject_id!r}"
        )
        super().__init__(
            message, (Diagnostic(code="privacy.rights.unauthorized", message=message),)
        )
        self.requested_by = requested_by
        self.subject_id = subject_id


class DeletionResidueError(PrivacyError):
    """An erase left personal data behind in a registered store (EVAL-DATA-009, hard failure).

    After ``privacy.rights.erase`` the deletion residue across every registered store MUST be
    zero. A non-zero residue is a privacy incident, never silently tolerated.
    """

    def __init__(self, subject_id: str, residue: int, stores: tuple[str, ...]) -> None:
        rendered = ", ".join(stores) if stores else "-"
        message = (
            f"deletion residue for subject {subject_id!r} is {residue} (> 0) in stores: "
            f"{rendered}; erasure did not fully propagate"
        )
        super().__init__(message, (Diagnostic(code="privacy.deletion.residue", message=message),))
        self.subject_id = subject_id
        self.residue = residue
        self.stores = stores


class TenantScopeMissing(PrivacyError):  # noqa: N818 canonical error name
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        message = "a tenant scope is required for this operation"
        super().__init__(message, (Diagnostic(code="privacy.tenant.missing", message=message),))
