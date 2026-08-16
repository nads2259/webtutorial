"""Typed identity domain errors (explainable, anti-enumeration-safe).

These subclass the kernel :class:`~northstar.kernel.errors.KernelError` so failures carry
deterministic :class:`~northstar.kernel.errors.Diagnostic` values and can be mapped to RFC 9457
problem details at the API edge (rule 40) — without the domain importing any infrastructure.

Anti-enumeration (docs/07 §14, rule 50): every authentication failure — unknown subject, bad
state, replayed nonce, issuer/audience mismatch, expired transaction — surfaces as the *same*
:class:`AuthenticationFailed` with a single uniform code and message, so a caller cannot probe
which accounts or tokens exist. Precise causes are recorded in the audit trail, never returned.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError

AUTHENTICATION_FAILED_CODE = "identity.authentication.failed"
_UNIFORM_AUTH_MESSAGE = "authentication could not be completed"


class IdentityError(KernelError):
    """Base class for identity domain errors."""


class AuthenticationFailed(IdentityError):  # noqa: N818 canonical error name
    """Uniform authentication failure (anti-enumeration, docs/07 §14).

    ``reason_code`` records the true internal cause for the audit trail; the human-facing
    ``message`` and public ``code`` are always identical regardless of cause.
    """

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        diagnostic = Diagnostic(
            code=AUTHENTICATION_FAILED_CODE,
            message=_UNIFORM_AUTH_MESSAGE,
            detail=None,
        )
        super().__init__(_UNIFORM_AUTH_MESSAGE, (diagnostic,))


class SessionNotAuthenticated(IdentityError):  # noqa: N818 canonical error name
    """No active, valid session was presented for a protected action."""

    def __init__(self) -> None:
        diagnostic = Diagnostic(
            code="identity.session.unauthenticated",
            message="a valid session is required",
        )
        super().__init__("a valid session is required", (diagnostic,))


class SessionInvariantViolation(IdentityError):  # noqa: N818 canonical error name
    """A session value object was constructed with contradictory temporal bounds."""

    def __init__(self, message: str) -> None:
        diagnostic = Diagnostic(code="identity.session.invalid", message=message)
        super().__init__(message, (diagnostic,))


class MfaVerificationFailed(IdentityError):  # noqa: N818 canonical error name
    """An MFA/passkey verification attempt did not succeed.

    ``reason_code`` records the true internal cause (bad code, replayed code, sign-count
    regression, unknown credential) for the audit trail; the public message is uniform so a
    caller cannot distinguish which second-factor check failed (anti-enumeration, docs/07 §14).
    """

    _UNIFORM_MESSAGE = "the presented authentication factor could not be verified"

    def __init__(self, reason_code: str = "mfa.verification_failed") -> None:
        self.reason_code = reason_code
        diagnostic = Diagnostic(
            code="identity.mfa.verification-failed",
            message=self._UNIFORM_MESSAGE,
        )
        super().__init__(self._UNIFORM_MESSAGE, (diagnostic,))


class StepUpRequired(IdentityError):  # noqa: N818 canonical error name
    """A privileged action was attempted by a session that has not satisfied step-up MFA.

    The session is authenticated but its assurance is below the required multi-factor tier
    (docs/07 §3): the caller must complete a second factor and retry.
    """

    def __init__(self) -> None:
        diagnostic = Diagnostic(
            code="identity.mfa.step-up-required",
            message="this action requires step-up multi-factor authentication",
        )
        super().__init__("step-up authentication is required", (diagnostic,))
