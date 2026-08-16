"""Typed moderation domain errors (rule 30/40): explainable, deterministic diagnostics.

The moderation domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary. The kernel error base carries the diagnostics.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class ModerationError(KernelError):
    """Base class for moderation domain errors."""


class ModerationInvariantViolation(ModerationError):  # noqa: N818 canonical error name
    """A moderation invariant was violated (bad disposition, bad resolution, empty target)."""

    def __init__(self, message: str, code: str = "moderation.invariant.violated") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class IllegalCaseTransition(ModerationInvariantViolation):
    """A case transition is not permitted from the current state (deterministic lifecycle)."""

    def __init__(self, *, from_state: str, transition: str) -> None:
        self.from_state = from_state
        self.transition = transition
        super().__init__(
            f"transition {transition!r} is not permitted from state {from_state!r}",
            code="moderation.transition.illegal",
        )


class CaseNotFound(ModerationInvariantViolation):
    """A case is absent in the caller's tenant scope (fail closed, do not disclose)."""

    def __init__(self) -> None:
        super().__init__(
            "moderation case is not available in this scope", code="moderation.case.not_found"
        )


class ReportableContentNotFound(ModerationInvariantViolation):
    """The reported content does not resolve through the reportable-content port (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "the reported content is not available in this scope",
            code="moderation.content.not_found",
        )


class TenantScopeMissing(ModerationInvariantViolation):
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        super().__init__(
            "a tenant scope is required for this operation",
            code="moderation.tenant.missing",
        )
