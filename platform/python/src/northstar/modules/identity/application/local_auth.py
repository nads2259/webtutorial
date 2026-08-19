"""Local email/password authentication capabilities (one authoritative impl per action, LAW-04).

A parallel first-factor path to OIDC that converges on the SAME server-managed session + cookie
contract (docs/07 §4): local login mints a :class:`Session` via the shared :class:`SessionStorePort`,
so every other module authenticates it identically. Confirmation + reset use single-use, expiring
tokens; every step writes a durable :class:`AccountEvent` for the Activity feed. Anonymous flows
(register/login/confirm/forgot/reset) are scoped to a configured default tenant, mirroring OIDC JIT
provisioning.

Capabilities:

* ``identity.local.register`` — create an unverified account, email a confirmation link.
* ``identity.local.confirm-email`` — consume a confirmation token, mark the email verified.
* ``identity.local.login`` — verify password + confirmed email, mint a session.
* ``identity.local.request-password-reset`` — email a reset link (uniform result, anti-enumeration).
* ``identity.local.reset-password`` — consume a reset token, set a new password.
* ``identity.local.resend-confirmation`` — re-email a confirmation link (uniform result).
* ``identity.activity.list`` (query) — the signed-in user's account events.
* ``identity.activity.admin-list`` (query) — tenant-wide account events (admin).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ..domain.local import (
    AccountEvent,
    AccountEventType,
    LocalAuthError,
    VerificationPurpose,
    VerificationToken,
    validate_email,
    validate_password,
)
from ..domain.model import AssuranceLevel, Session
from .capabilities import CAP_VERSION, SessionPolicy, _DEFAULT_SESSION_POLICY
from .local_ports import (
    AccountEventStorePort,
    LocalAccountStorePort,
    PasswordHasherPort,
    TransactionalEmailPort,
    VerificationTokenStorePort,
)
from .ports import SessionStorePort


def _hash_token(raw: str) -> str:
    """SHA-256 (hex) of an opaque verification token; only the hash is persisted."""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

CAP_LOCAL_REGISTER = "identity.local.register"
CAP_LOCAL_CONFIRM_EMAIL = "identity.local.confirm-email"
CAP_LOCAL_LOGIN = "identity.local.login"
CAP_LOCAL_REQUEST_RESET = "identity.local.request-password-reset"
CAP_LOCAL_RESET_PASSWORD = "identity.local.reset-password"
CAP_LOCAL_RESEND_CONFIRMATION = "identity.local.resend-confirmation"
CAP_ACTIVITY_LIST = "identity.activity.list"
CAP_ACTIVITY_ADMIN_LIST = "identity.activity.admin-list"

LOCAL_AUTH_ANONYMOUS_CAPABILITIES: tuple[str, ...] = (
    CAP_LOCAL_REGISTER,
    CAP_LOCAL_CONFIRM_EMAIL,
    CAP_LOCAL_LOGIN,
    CAP_LOCAL_REQUEST_RESET,
    CAP_LOCAL_RESET_PASSWORD,
    CAP_LOCAL_RESEND_CONFIRMATION,
)

TEMPLATE_CONFIRMATION = "account-confirmation"
TEMPLATE_PASSWORD_RESET = "password-reset"

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
TokenFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class TokenPolicy:
    confirm_ttl: timedelta = timedelta(hours=24)
    reset_ttl: timedelta = timedelta(hours=1)


_DEFAULT_TOKEN_POLICY = TokenPolicy()


# --------------------------------------------------------------------------- commands / results


@dataclass(frozen=True, slots=True)
class RegisterCommand:
    email: str
    password: str


@dataclass(frozen=True, slots=True)
class RegisterResult:
    subject_id: str
    email: str


@dataclass(frozen=True, slots=True)
class ConfirmEmailCommand:
    token: str


@dataclass(frozen=True, slots=True)
class ConfirmEmailResult:
    confirmed: bool
    email: str


@dataclass(frozen=True, slots=True)
class LoginCommand:
    email: str
    password: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    raw_session_token: str
    session_id: str
    subject_id: str
    absolute_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RequestPasswordResetCommand:
    email: str


@dataclass(frozen=True, slots=True)
class ResetPasswordCommand:
    token: str
    password: str


@dataclass(frozen=True, slots=True)
class ResendConfirmationCommand:
    email: str


@dataclass(frozen=True, slots=True)
class GenericResult:
    ok: bool = True


@dataclass(frozen=True, slots=True)
class ActivityListQuery:
    limit: int = 50


@dataclass(frozen=True, slots=True)
class AdminActivityListQuery:
    limit: int = 25
    offset: int = 0
    event_type: str | None = None
    q: str | None = None
    created_after: str | None = None
    created_before: str | None = None


@dataclass(frozen=True, slots=True)
class AccountEventView:
    event_type: str
    created_at: datetime
    subject_id: str
    detail: str | None


@dataclass(frozen=True, slots=True)
class ActivityView:
    events: tuple[AccountEventView, ...] = field(default_factory=tuple)
    total: int | None = None


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _parse_dt(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _to_view(event: AccountEvent) -> AccountEventView:
    return AccountEventView(
        event_type=event.event_type.value,
        created_at=event.created_at,
        subject_id=event.subject_id,
        detail=event.detail,
    )


# --------------------------------------------------------------------------- handlers


class _LocalBase:
    """Shared collaborators for the anonymous local-auth capabilities."""

    def __init__(
        self,
        *,
        accounts: LocalAccountStorePort,
        hasher: PasswordHasherPort,
        tokens: VerificationTokenStorePort,
        events: AccountEventStorePort,
        email: TransactionalEmailPort,
        clock: Clock,
        id_factory: IdFactory,
        token_factory: TokenFactory,
        tenant_scope: str,
        app_base_url: str,
        token_policy: TokenPolicy = _DEFAULT_TOKEN_POLICY,
    ) -> None:
        self._accounts = accounts
        self._hasher = hasher
        self._tokens = tokens
        self._events = events
        self._email = email
        self._clock = clock
        self._id_factory = id_factory
        self._token_factory = token_factory
        self._tenant = tenant_scope
        self._base = app_base_url.rstrip("/")
        self._token_policy = token_policy

    def _issue_token(self, *, subject_id: str, email: str, purpose: VerificationPurpose) -> str:
        now = self._clock()
        raw = self._token_factory()
        ttl = (
            self._token_policy.confirm_ttl
            if purpose is VerificationPurpose.EMAIL_CONFIRM
            else self._token_policy.reset_ttl
        )
        self._tokens.save(
            organization_id=self._tenant,
            token=VerificationToken(
                token_id=self._id_factory(),
                token_sha256=_hash_token(raw),
                purpose=purpose,
                subject_id=subject_id,
                email=email,
                created_at=now,
                expires_at=now + ttl,
            ),
        )
        return raw

    def _record(self, *, subject_id: str, event_type: AccountEventType, detail: str | None) -> None:
        self._events.record(
            event=AccountEvent(
                event_id=self._id_factory(),
                subject_id=subject_id,
                organization_id=self._tenant,
                event_type=event_type,
                created_at=self._clock(),
                detail=detail,
            )
        )

    def _send_confirmation(self, *, subject_id: str, email: str) -> None:
        raw = self._issue_token(
            subject_id=subject_id, email=email, purpose=VerificationPurpose.EMAIL_CONFIRM
        )
        self._email.send(
            organization_id=self._tenant,
            template_id=TEMPLATE_CONFIRMATION,
            to_email=email,
            variables={"email": email, "link": f"{self._base}/confirm?token={raw}"},
        )


class RegisterLocalUser(_LocalBase):
    """``identity.local.register`` — create an unverified account and email a confirmation link."""

    def handle(self, request: object) -> RegisterResult:
        command = _typed(request, RegisterCommand)
        email = validate_email(command.email)
        validate_password(command.password)
        if self._accounts.find_by_email(organization_id=self._tenant, email=email) is not None:
            raise LocalAuthError("email already registered")
        credential = self._accounts.create_account(
            organization_id=self._tenant,
            email=email,
            password_hash=self._hasher.hash(command.password),
        )
        self._send_confirmation(subject_id=credential.subject_id, email=email)
        self._record(
            subject_id=credential.subject_id,
            event_type=AccountEventType.REGISTERED,
            detail=email,
        )
        return RegisterResult(subject_id=credential.subject_id, email=email)


class ConfirmEmail(_LocalBase):
    """``identity.local.confirm-email`` — consume a confirmation token, mark the email verified."""

    def handle(self, request: object) -> ConfirmEmailResult:
        command = _typed(request, ConfirmEmailCommand)
        token = self._tokens.find_by_hash(
            organization_id=self._tenant, token_sha256=_hash_token(command.token or "")
        )
        now = self._clock()
        if (
            token is None
            or token.purpose is not VerificationPurpose.EMAIL_CONFIRM
            or not token.is_valid(now)
        ):
            raise LocalAuthError("this confirmation link is invalid or has expired")
        self._accounts.set_verified(organization_id=self._tenant, subject_id=token.subject_id)
        self._tokens.consume(organization_id=self._tenant, token_id=token.token_id, now=now)
        self._record(
            subject_id=token.subject_id,
            event_type=AccountEventType.EMAIL_CONFIRMED,
            detail=token.email,
        )
        return ConfirmEmailResult(confirmed=True, email=token.email)


class LoginLocalUser(_LocalBase):
    """``identity.local.login`` — verify password + confirmed email, mint a session."""

    def __init__(
        self,
        *,
        sessions: SessionStorePort,
        session_policy: SessionPolicy = _DEFAULT_SESSION_POLICY,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._sessions = sessions
        self._session_policy = session_policy

    def handle(self, request: object) -> LoginResult:
        command = _typed(request, LoginCommand)
        email = validate_email(command.email)
        credential = self._accounts.find_by_email(organization_id=self._tenant, email=email)
        if credential is None or not self._hasher.verify(
            password=command.password, encoded=credential.password_hash
        ):
            raise LocalAuthError("invalid email or password")
        if not credential.email_verified:
            raise LocalAuthError("email_not_confirmed")

        now = self._clock()
        raw_token = self._token_factory()
        session = Session(
            session_id=self._id_factory(),
            subject_id=credential.subject_id,
            created_at=now,
            idle_expires_at=now + self._session_policy.idle_ttl,
            absolute_expires_at=now + self._session_policy.absolute_ttl,
            assurance=AssuranceLevel.SINGLE_FACTOR,
            tenant_scope=self._tenant,
        )
        self._sessions.create(raw_token=raw_token, session=session)
        self._record(
            subject_id=credential.subject_id, event_type=AccountEventType.LOGIN, detail=email
        )
        return LoginResult(
            raw_session_token=raw_token,
            session_id=session.session_id,
            subject_id=credential.subject_id,
            absolute_expires_at=session.absolute_expires_at,
        )


class RequestPasswordReset(_LocalBase):
    """``identity.local.request-password-reset`` — email a reset link (uniform result)."""

    def handle(self, request: object) -> GenericResult:
        command = _typed(request, RequestPasswordResetCommand)
        try:
            email = validate_email(command.email)
        except LocalAuthError:
            return GenericResult(ok=True)
        credential = self._accounts.find_by_email(organization_id=self._tenant, email=email)
        if credential is not None:
            raw = self._issue_token(
                subject_id=credential.subject_id,
                email=email,
                purpose=VerificationPurpose.PASSWORD_RESET,
            )
            self._email.send(
                organization_id=self._tenant,
                template_id=TEMPLATE_PASSWORD_RESET,
                to_email=email,
                variables={"email": email, "link": f"{self._base}/reset-password?token={raw}"},
            )
            self._record(
                subject_id=credential.subject_id,
                event_type=AccountEventType.PASSWORD_RESET_REQUESTED,
                detail=email,
            )
        return GenericResult(ok=True)


class ResetPassword(_LocalBase):
    """``identity.local.reset-password`` — consume a reset token, set a new password."""

    def handle(self, request: object) -> GenericResult:
        command = _typed(request, ResetPasswordCommand)
        validate_password(command.password)
        token = self._tokens.find_by_hash(
            organization_id=self._tenant, token_sha256=_hash_token(command.token or "")
        )
        now = self._clock()
        if (
            token is None
            or token.purpose is not VerificationPurpose.PASSWORD_RESET
            or not token.is_valid(now)
        ):
            raise LocalAuthError("this reset link is invalid or has expired")
        self._accounts.set_password(
            organization_id=self._tenant,
            subject_id=token.subject_id,
            password_hash=self._hasher.hash(command.password),
        )
        self._tokens.consume(organization_id=self._tenant, token_id=token.token_id, now=now)
        self._record(
            subject_id=token.subject_id,
            event_type=AccountEventType.PASSWORD_RESET,
            detail=token.email,
        )
        return GenericResult(ok=True)


class ResendConfirmation(_LocalBase):
    """``identity.local.resend-confirmation`` — re-email a confirmation link (uniform result)."""

    def handle(self, request: object) -> GenericResult:
        command = _typed(request, ResendConfirmationCommand)
        try:
            email = validate_email(command.email)
        except LocalAuthError:
            return GenericResult(ok=True)
        credential = self._accounts.find_by_email(organization_id=self._tenant, email=email)
        if credential is not None and not credential.email_verified:
            self._send_confirmation(subject_id=credential.subject_id, email=email)
            self._record(
                subject_id=credential.subject_id,
                event_type=AccountEventType.CONFIRMATION_RESENT,
                detail=email,
            )
        return GenericResult(ok=True)


class ListActivity:
    """``identity.activity.list`` (query) — the signed-in user's account events."""

    def __init__(self, *, events: AccountEventStorePort) -> None:
        self._events = events

    def handle(self, request: object) -> ActivityView:
        query = _typed(request, ActivityListQuery)
        context = getattr(request, "context", None)
        subject_id = getattr(getattr(context, "actor", None), "id", None)
        tenant = getattr(context, "tenant_scope", None)
        if not subject_id or not tenant:
            return ActivityView()
        rows = self._events.list_for_subject(
            organization_id=tenant, subject_id=subject_id, limit=query.limit
        )
        return ActivityView(events=tuple(_to_view(e) for e in rows))


class ListAdminActivity:
    """``identity.activity.admin-list`` (query) — tenant-wide account events (admin)."""

    def __init__(self, *, events: AccountEventStorePort) -> None:
        self._events = events

    def handle(self, request: object) -> ActivityView:
        query = _typed(request, AdminActivityListQuery)
        context = getattr(request, "context", None)
        tenant = getattr(context, "tenant_scope", None)
        if not tenant:
            return ActivityView()
        event_type: str | None = None
        if query.event_type:
            try:
                event_type = AccountEventType(query.event_type).value
            except ValueError:
                return ActivityView(events=(), total=0)
        detail_query = (query.q or "").strip() or None
        created_after = _parse_dt(query.created_after)
        created_before = _parse_dt(query.created_before)
        limit = max(1, min(query.limit, 100))
        offset = max(0, query.offset)
        rows = self._events.list_for_tenant(
            organization_id=tenant,
            limit=limit,
            offset=offset,
            event_type=event_type,
            detail_query=detail_query,
            created_after=created_after,
            created_before=created_before,
        )
        total = self._events.count_for_tenant(
            organization_id=tenant,
            event_type=event_type,
            detail_query=detail_query,
            created_after=created_after,
            created_before=created_before,
        )
        return ActivityView(events=tuple(_to_view(e) for e in rows), total=total)
