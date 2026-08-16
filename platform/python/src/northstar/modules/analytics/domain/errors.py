"""Typed analytics domain errors (rule 30/40): explainable, deterministic diagnostics.

The analytics domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary. The kernel error base carries the diagnostics.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class AnalyticsError(KernelError):
    """Base class for analytics domain errors."""


class CatalogValidationError(AnalyticsError):
    """An analytics event DEFINITION violates the catalog schema/invariants (FR-ANL-003).

    Deny-by-default: a definition that does not validate against
    ``analytics-event-definition.schema.json`` (bad name/version/classification/consent category,
    unsafe property, etc.) is rejected at registration and never becomes part of the catalog.
    """

    def __init__(self, message: str, code: str = "analytics.catalog.invalid") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class PurposeRequired(CatalogValidationError):  # noqa: N818 canonical error name
    """An event type without a declared purpose is rejected at registration (FR-ANL-003)."""

    def __init__(self) -> None:
        super().__init__(
            "an analytics event type MUST declare a purpose (>= 10 characters); "
            "a purpose-less event type is rejected",
            code="analytics.catalog.purpose_required",
        )


class DefinitionAlreadyRegistered(AnalyticsError):  # noqa: N818 canonical error name
    """A catalog event definition ``(event_name, version)`` is already registered (immutable)."""

    def __init__(self, event_name: str, version: int) -> None:
        message = (
            f"event definition {event_name!r} version {version} is already registered; "
            "register a new version instead"
        )
        super().__init__(
            message, (Diagnostic(code="analytics.catalog.already_registered", message=message),)
        )
        self.event_name = event_name
        self.version = version


class UnknownEventType(AnalyticsError):  # noqa: N818 canonical error name
    """An ingested event references an event type that is not in the catalog (FR-ANL-007)."""

    def __init__(self, event_name: str) -> None:
        message = f"event type {event_name!r} is not registered in the catalog"
        super().__init__(
            message, (Diagnostic(code="analytics.pipeline.unknown_type", message=message),)
        )
        self.event_name = event_name


class PipelineValidationError(AnalyticsError):
    """An emitted event failed schema validation against its catalog definition (FR-ANL-007).

    Malformed / unknown-property / wrong-type / missing-required events are REJECTED (quarantined),
    never silently accepted (EVAL-ANL-001/003/007).
    """

    def __init__(self, message: str) -> None:
        super().__init__(
            message, (Diagnostic(code="analytics.pipeline.invalid_event", message=message),)
        )


class ConsentNotGranted(AnalyticsError):  # noqa: N818 canonical error name
    """Identity stitching was requested without the required consent (FR-ANL-004).

    Without consent no linkage is created: the stitch is refused (deny-by-default).
    """

    def __init__(self, required_category: str) -> None:
        message = (
            f"identity stitching requires {required_category!r} consent; "
            "without consent no linkage is created"
        )
        super().__init__(
            message, (Diagnostic(code="analytics.stitch.consent_required", message=message),)
        )
        self.required_category = required_category


class StitchInvariantViolation(AnalyticsError):  # noqa: N818 canonical error name
    """An identity stitch violated an invariant (missing/degenerate identifiers, FR-ANL-004)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, (Diagnostic(code="analytics.stitch.invalid", message=message),))


class Ga4AuthorityViolation(AnalyticsError):  # noqa: N818 canonical error name
    """A GA4-derived value was marked authoritative — forbidden (FR-ANL-006, EVAL-ANL-006)."""

    def __init__(self) -> None:
        message = "GA4-derived values are never authoritative learner state"
        super().__init__(message, (Diagnostic(code="analytics.ga4.authority", message=message),))


class TenantScopeMissing(AnalyticsError):  # noqa: N818 canonical error name
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        message = "a tenant scope is required for this operation"
        super().__init__(message, (Diagnostic(code="analytics.tenant.missing", message=message),))
