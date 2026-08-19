"""Pure domain for local (email + password) authentication.

Local auth is a parallel first-factor path to OIDC (docs/07 §3): a user proves control of an email
address and a password. These frozen value objects hold no infrastructure — hashing, persistence and
email delivery are ports (rule 10). Passwords never live here in plaintext; only a normalized email
and lifecycle facts do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .errors import IdentityError

# Deliberately permissive: reject only obviously invalid addresses; real deliverability is proven by
# the confirmation email, not a regex.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


class LocalAuthError(IdentityError):
    """A local-auth precondition failed (invalid email/password, taken email, bad/expired token)."""


class VerificationPurpose(StrEnum):
    """What a single-use verification token authorizes."""

    EMAIL_CONFIRM = "email_confirm"
    PASSWORD_RESET = "password_reset"


class AccountEventType(StrEnum):
    """Durable account-activity events surfaced under Activity (and admin activity)."""

    REGISTERED = "registered"
    EMAIL_CONFIRMED = "email_confirmed"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET = "password_reset"
    CONFIRMATION_RESENT = "confirmation_resent"


def normalize_email(raw: str) -> str:
    """Trim + lowercase an email for storage and lookup (case-insensitive uniqueness)."""
    return (raw or "").strip().lower()


def validate_email(raw: str) -> str:
    email = normalize_email(raw)
    if not _EMAIL_RE.match(email) or len(email) > 320:
        raise LocalAuthError("a valid email address is required")
    return email


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise LocalAuthError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 256:
        raise LocalAuthError("password is too long")


@dataclass(frozen=True, slots=True)
class PasswordCredential:
    """A user's local password credential + email-verification state.

    ``is_admin`` marks a backend/management account. Admin accounts are seeded (never self-registered
    through the public flow) and authenticate on the separate management login surface.
    """

    user_id: str
    subject_id: str
    email: str
    password_hash: str
    email_verified: bool
    created_at: datetime
    updated_at: datetime
    is_admin: bool = False


@dataclass(frozen=True, slots=True)
class VerificationToken:
    """A single-use, expiring token (only its SHA-256 is persisted)."""

    token_id: str
    token_sha256: str
    purpose: VerificationPurpose
    subject_id: str
    email: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None

    def is_valid(self, now: datetime) -> bool:
        return self.consumed_at is None and now < self.expires_at


@dataclass(frozen=True, slots=True)
class AccountEvent:
    """A durable record of an account-activity event for the Activity feed."""

    event_id: str
    subject_id: str
    organization_id: str
    event_type: AccountEventType
    created_at: datetime
    detail: str | None = None
