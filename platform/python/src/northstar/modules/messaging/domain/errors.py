"""Typed messaging domain errors (rule 30/40): explainable, deterministic diagnostics.

The messaging domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary. The kernel error base carries the diagnostics.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class MessagingError(KernelError):
    """Base class for messaging domain errors."""


class MessagingInvariantViolation(MessagingError):  # noqa: N818 canonical error name
    """A messaging invariant was violated (e.g. empty name, bad tracking config)."""

    def __init__(self, message: str, code: str = "messaging.invariant.violated") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class UnsafeSegmentError(MessagingError):
    """A segment referenced a non-approved attribute/operator or a raw-query surface (FR-MSG-003).

    Deny-by-default: audience segmentation may use ONLY approved attributes with an allowlisted
    operator, so a raw-SQL / arbitrary-DB segment can never be expressed by a campaign user.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, (Diagnostic(code="messaging.segment.unsafe", message=message),))


class TemplateVersionAlreadyPublished(MessagingError):  # noqa: N818 canonical error name
    """A published template version is immutable; republishing it is rejected (FR-MSG-002)."""

    def __init__(self, template_id: str, version: int) -> None:
        message = (
            f"template {template_id!r} version {version} is already published and immutable; "
            "publish a new version instead"
        )
        super().__init__(
            message, (Diagnostic(code="messaging.template.immutable", message=message),)
        )
        self.template_id = template_id
        self.version = version


class TemplateVersionNotFound(MessagingError):  # noqa: N818 canonical error name
    """A campaign referenced a template version that does not exist in this tenant."""

    def __init__(self, template_id: str, version: int) -> None:
        message = f"template {template_id!r} version {version} is not available in this scope"
        super().__init__(
            message, (Diagnostic(code="messaging.template.not_found", message=message),)
        )


class TemplateRenderError(MessagingError):
    """Rendering a template version failed (missing variable / malformed template) — fail closed."""

    def __init__(self, message: str) -> None:
        super().__init__(message, (Diagnostic(code="messaging.template.render", message=message),))


class CampaignNotFound(MessagingError):  # noqa: N818 canonical error name
    """A campaign is absent or belongs to another tenant: fail closed, do not disclose."""

    def __init__(self, campaign_id: str) -> None:
        message = f"campaign {campaign_id!r} is not available in this scope"
        super().__init__(
            message, (Diagnostic(code="messaging.campaign.not_found", message=message),)
        )


class TenantScopeMissing(MessagingInvariantViolation):
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "a tenant scope is required for this operation",
            code="messaging.tenant.missing",
        )
