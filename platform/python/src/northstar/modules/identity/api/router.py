"""``/auth/*`` FastAPI router: OIDC+PKCE login, secure sessions, logout (docs/07 §3-4).

Endpoints:

* ``GET  /auth/login``    — begin Authorization Code + PKCE; redirects to the IdP and sets an
  HttpOnly, Secure, SameSite ``state`` cookie that binds the callback (RFC 9700 CSRF defense).
* ``GET  /auth/callback`` — validate the callback, mint a server session and set the HttpOnly,
  Secure, SameSite session cookie plus a readable CSRF cookie (double-submit).
* ``GET  /auth/session``  — protected: describe the current session (401 without a valid cookie).
* ``POST /auth/rotate``   — protected + CSRF: rotate the session id (privilege change).
* ``POST /auth/logout``   — protected + CSRF: revoke the session and clear cookies.

The router never stores raw tokens and never reveals why authentication failed (anti-enumeration):
every failed authentication returns the same 401 problem document.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from northstar.kernel.context import Actor, ActorType, RequestContext, ResourceRef
from northstar.kernel.messaging import Command, CommandBus, Query, QueryBus

from ..application.capabilities import (
    CAP_BEGIN_AUTHENTICATION,
    CAP_COMPLETE_AUTHENTICATION,
    CAP_DESCRIBE_SESSION,
    CAP_REVOKE_SESSION,
    CAP_ROTATE_SESSION,
    CAP_VERSION,
    SESSION_RESOURCE_TYPE,
    BeginAuthenticationCommand,
    BeginAuthenticationResult,
    CompleteAuthenticationCommand,
    CompleteAuthenticationResult,
    DescribeSessionQuery,
    RevokeSessionCommand,
    RotateSessionCommand,
    RotateSessionResult,
    SessionView,
)
from ..application.mfa import (
    CAP_BEGIN_WEBAUTHN_AUTHENTICATION,
    CAP_BEGIN_WEBAUTHN_REGISTRATION,
    CAP_COMPLETE_WEBAUTHN_AUTHENTICATION,
    CAP_COMPLETE_WEBAUTHN_REGISTRATION,
    CAP_ENFORCE_STEP_UP,
    CAP_ENROLL_TOTP,
    CAP_VERIFY_TOTP,
    BeginWebAuthnAuthenticationCommand,
    BeginWebAuthnAuthenticationResult,
    BeginWebAuthnRegistrationCommand,
    BeginWebAuthnRegistrationResult,
    CompleteWebAuthnAuthenticationCommand,
    CompleteWebAuthnAuthenticationResult,
    CompleteWebAuthnRegistrationCommand,
    CompleteWebAuthnRegistrationResult,
    EnforceStepUpQuery,
    EnrollTotpCommand,
    EnrollTotpResult,
    VerifyTotpCommand,
    VerifyTotpResult,
)
from ..application.ports import SessionStorePort
from ..domain.errors import IdentityError, StepUpRequired

_STATE_KEY = "northstar_identity_api_dependencies"
_PROBLEM_CONTENT_TYPE = "application/problem+json"
_CSRF_HEADER = "X-CSRF-Token"
_SESSION_RESOURCE = SESSION_RESOURCE_TYPE


@dataclass(frozen=True, slots=True)
class IdentityCookieConfig:
    """Cookie attributes for the session/state/CSRF cookies (rule 50 secure defaults).

    ``secure`` defaults to ``True`` (cookies only over HTTPS); tests that use the ASGI transport
    over plain HTTP set it to ``False`` explicitly. ``samesite`` is bounded (``lax``) and the
    session/state cookies are ``HttpOnly`` so client script cannot read them.
    """

    secure: bool = True
    samesite: str = "lax"
    path: str = "/"
    session_cookie: str = "ns_session"
    state_cookie: str = "ns_auth_state"
    csrf_cookie: str = "ns_csrf"


@dataclass(frozen=True, slots=True)
class IdentityApiDependencies:
    """Collaborators the ``/auth`` router needs, injected at the composition root."""

    command_bus: CommandBus
    query_bus: QueryBus
    session_store: SessionStorePort
    clock: Callable[[], datetime]
    callback_url: str
    login_scopes: tuple[str, ...] = ("openid", "email")
    cookies: IdentityCookieConfig = field(default_factory=IdentityCookieConfig)
    post_login_url: str | None = None


def bind_identity_dependencies(app_state: object, deps: IdentityApiDependencies) -> None:
    """Attach identity router dependencies to ``app.state`` under the module's private key."""
    setattr(app_state, _STATE_KEY, deps)


def _deps(request: Request) -> IdentityApiDependencies:
    return getattr(request.app.state, _STATE_KEY)


def _correlation_id(request: Request) -> str:
    return request.headers.get("X-Correlation-Id") or f"cor_{uuid.uuid4().hex}"


def _anonymous_context(request: Request) -> RequestContext:
    return RequestContext(
        actor=Actor(type=ActorType.ANONYMOUS, id="anonymous"),
        correlation_id=_correlation_id(request),
    )


def _problem(status: int, code: str, title: str, detail: str, correlation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type=_PROBLEM_CONTENT_TYPE,
        content={
            "type": f"https://errors.northstar.example/{code.replace('.', '/')}",
            "title": title,
            "status": status,
            "detail": detail,
            "code": code,
            "correlation_id": correlation_id,
            "retryable": False,
        },
    )


def _unauthenticated(correlation_id: str) -> JSONResponse:
    return _problem(
        401,
        "authentication.required",
        "Authentication is required",
        "Authentication is required.",
        correlation_id,
    )


def _set_session_cookies(
    response: Response, deps: IdentityApiDependencies, *, raw_session_token: str
) -> str:
    """Set the HttpOnly session cookie and a readable CSRF cookie; return the CSRF token."""
    cookies = deps.cookies
    response.set_cookie(
        key=cookies.session_cookie,
        value=raw_session_token,
        httponly=True,
        secure=cookies.secure,
        samesite=cookies.samesite,
        path=cookies.path,
    )
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=cookies.csrf_cookie,
        value=csrf_token,
        httponly=False,  # readable by client script for the double-submit CSRF pattern
        secure=cookies.secure,
        samesite=cookies.samesite,
        path=cookies.path,
    )
    return csrf_token


def _authenticated_context(
    request: Request, deps: IdentityApiDependencies
) -> tuple[RequestContext, str] | None:
    """Resolve the session cookie to an authenticated context, or ``None`` when absent/invalid."""
    raw = request.cookies.get(deps.cookies.session_cookie)
    if not raw:
        return None
    session = deps.session_store.authenticate(raw_token=raw, now=deps.clock())
    if session is None:
        return None
    context = RequestContext(
        actor=Actor(type=ActorType.USER, id=session.subject_id),
        correlation_id=_correlation_id(request),
        tenant_scope=session.tenant_scope,
    )
    return context, session.session_id


def _csrf_ok(request: Request, deps: IdentityApiDependencies) -> bool:
    header = request.headers.get(_CSRF_HEADER)
    cookie = request.cookies.get(deps.cookies.csrf_cookie)
    return bool(header) and bool(cookie) and secrets.compare_digest(header, cookie)


def create_identity_router() -> APIRouter:
    """Build the ``/auth`` router (dependencies are read from ``app.state`` at request time)."""
    router = APIRouter(prefix="/auth", tags=["identity"])

    @router.get("/login")
    def login(request: Request) -> Response:
        deps = _deps(request)
        context = _anonymous_context(request)
        command = Command(
            capability=CAP_BEGIN_AUTHENTICATION,
            version=CAP_VERSION,
            payload=BeginAuthenticationCommand(
                redirect_uri=deps.callback_url, scopes=deps.login_scopes
            ),
        )
        result = deps.command_bus.dispatch(command, context)
        begin: BeginAuthenticationResult = result.value  # type: ignore[assignment]
        redirect = RedirectResponse(url=begin.authorization_url, status_code=302)
        redirect.set_cookie(
            key=deps.cookies.state_cookie,
            value=begin.state,
            httponly=True,
            secure=deps.cookies.secure,
            samesite=deps.cookies.samesite,
            path=deps.cookies.path,
        )
        redirect.headers["X-Correlation-Id"] = context.correlation_id
        return redirect

    @router.get("/callback")
    def callback(request: Request, code: str = "", state: str = "") -> Response:
        deps = _deps(request)
        context = _anonymous_context(request)
        state_cookie = request.cookies.get(deps.cookies.state_cookie)
        command = Command(
            capability=CAP_COMPLETE_AUTHENTICATION,
            version=CAP_VERSION,
            payload=CompleteAuthenticationCommand(
                code=code, state=state, state_cookie=state_cookie
            ),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except IdentityError:
            return _unauthenticated(context.correlation_id)
        completed: CompleteAuthenticationResult = result.value  # type: ignore[assignment]
        body = {
            "subject_id": completed.subject_id,
            "assurance": completed.assurance.value,
            "provisioned": completed.provisioned,
        }
        response = JSONResponse(status_code=200, content=body)
        _set_session_cookies(response, deps, raw_session_token=completed.raw_session_token)
        response.delete_cookie(deps.cookies.state_cookie, path=deps.cookies.path)
        response.headers["X-Correlation-Id"] = context.correlation_id
        return response

    @router.get("/session")
    def describe_session(request: Request) -> Response:
        deps = _deps(request)
        authed = _authenticated_context(request, deps)
        if authed is None:
            return _unauthenticated(_correlation_id(request))
        context, session_id = authed
        query = Query(
            capability=CAP_DESCRIBE_SESSION,
            version=CAP_VERSION,
            parameters=DescribeSessionQuery(session_id=session_id),
            resource=ResourceRef(type=_SESSION_RESOURCE, id=session_id),
        )
        try:
            result = deps.query_bus.dispatch(query, context)
        except IdentityError:
            return _unauthenticated(context.correlation_id)
        view: SessionView = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=200,
            content={
                "session_id": view.session_id,
                "subject_id": view.subject_id,
                "assurance": view.assurance.value,
                "tenant_scope": view.tenant_scope,
            },
            headers={"X-Correlation-Id": context.correlation_id},
        )

    @router.post("/rotate")
    def rotate_session(request: Request) -> Response:
        deps = _deps(request)
        authed = _authenticated_context(request, deps)
        if authed is None:
            return _unauthenticated(_correlation_id(request))
        context, session_id = authed
        if not _csrf_ok(request, deps):
            return _problem(
                403,
                "authorization.denied",
                "CSRF validation failed",
                "The CSRF token is missing or invalid.",
                context.correlation_id,
            )
        command = Command(
            capability=CAP_ROTATE_SESSION,
            version=CAP_VERSION,
            payload=RotateSessionCommand(session_id=session_id, reason="privilege_change"),
            resource=ResourceRef(type=_SESSION_RESOURCE, id=session_id),
        )
        result = deps.command_bus.dispatch(command, context)
        rotated: RotateSessionResult = result.value  # type: ignore[assignment]
        response = JSONResponse(
            status_code=200,
            content={"session_id": rotated.session_id, "rotated_from": rotated.rotated_from},
        )
        _set_session_cookies(response, deps, raw_session_token=rotated.raw_session_token)
        response.headers["X-Correlation-Id"] = context.correlation_id
        return response

    @router.post("/logout")
    def logout(request: Request) -> Response:
        deps = _deps(request)
        authed = _authenticated_context(request, deps)
        if authed is None:
            return _unauthenticated(_correlation_id(request))
        context, session_id = authed
        if not _csrf_ok(request, deps):
            return _problem(
                403,
                "authorization.denied",
                "CSRF validation failed",
                "The CSRF token is missing or invalid.",
                context.correlation_id,
            )
        command = Command(
            capability=CAP_REVOKE_SESSION,
            version=CAP_VERSION,
            payload=RevokeSessionCommand(session_id=session_id),
            resource=ResourceRef(type=_SESSION_RESOURCE, id=session_id),
        )
        deps.command_bus.dispatch(command, context)
        response = JSONResponse(status_code=200, content={"revoked": True})
        response.delete_cookie(deps.cookies.session_cookie, path=deps.cookies.path)
        response.delete_cookie(deps.cookies.csrf_cookie, path=deps.cookies.path)
        response.headers["X-Correlation-Id"] = context.correlation_id
        return response

    def _mfa_guard(
        request: Request,
    ) -> tuple[IdentityApiDependencies, RequestContext, str] | JSONResponse:
        """Resolve the authenticated session and enforce CSRF for an MFA mutation, or a problem."""
        deps = _deps(request)
        authed = _authenticated_context(request, deps)
        if authed is None:
            return _unauthenticated(_correlation_id(request))
        context, session_id = authed
        if not _csrf_ok(request, deps):
            return _problem(
                403,
                "authorization.denied",
                "CSRF validation failed",
                "The CSRF token is missing or invalid.",
                context.correlation_id,
            )
        return deps, context, session_id

    @router.post("/mfa/totp/enroll")
    async def enroll_totp(request: Request) -> Response:
        guard = _mfa_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        deps, context, _session_id = guard
        body = await _json_body(request)
        command = Command(
            capability=CAP_ENROLL_TOTP,
            version=CAP_VERSION,
            payload=EnrollTotpCommand(
                subject_id=context.actor.id,
                account_name=str(body.get("account_name") or context.actor.id),
                label=body.get("label"),
            ),
        )
        result = deps.command_bus.dispatch(command, context)
        enrolled: EnrollTotpResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=200,
            content={
                "credential_id": enrolled.credential_id,
                "secret": enrolled.secret,
                "provisioning_uri": enrolled.provisioning_uri,
                "digits": enrolled.digits,
                "period": enrolled.period,
                "algorithm": enrolled.algorithm,
            },
            headers={"X-Correlation-Id": context.correlation_id},
        )

    @router.post("/mfa/totp/verify")
    async def verify_totp(request: Request) -> Response:
        guard = _mfa_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        deps, context, session_id = guard
        body = await _json_body(request)
        command = Command(
            capability=CAP_VERIFY_TOTP,
            version=CAP_VERSION,
            payload=VerifyTotpCommand(
                subject_id=context.actor.id,
                code=str(body.get("code") or ""),
                session_id=session_id,
            ),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except IdentityError:
            return _problem(
                400,
                "identity.mfa.verification-failed",
                "MFA verification failed",
                "The presented authentication factor could not be verified.",
                context.correlation_id,
            )
        verified: VerifyTotpResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=200,
            content={
                "verified": verified.verified,
                "mfa_satisfied": verified.mfa_satisfied,
                "assurance": verified.assurance.value if verified.assurance else None,
            },
            headers={"X-Correlation-Id": context.correlation_id},
        )

    @router.post("/mfa/webauthn/register/begin")
    async def webauthn_register_begin(request: Request) -> Response:
        guard = _mfa_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        deps, context, _session_id = guard
        body = await _json_body(request)
        command = Command(
            capability=CAP_BEGIN_WEBAUTHN_REGISTRATION,
            version=CAP_VERSION,
            payload=BeginWebAuthnRegistrationCommand(
                subject_id=context.actor.id,
                user_name=str(body.get("user_name") or context.actor.id),
            ),
        )
        result = deps.command_bus.dispatch(command, context)
        begun: BeginWebAuthnRegistrationResult = result.value  # type: ignore[assignment]
        return Response(
            status_code=200,
            content=begun.options_json,
            media_type="application/json",
            headers={"X-Correlation-Id": context.correlation_id},
        )

    @router.post("/mfa/webauthn/register/complete")
    async def webauthn_register_complete(request: Request) -> Response:
        guard = _mfa_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        deps, context, _session_id = guard
        body = await _json_body(request)
        command = Command(
            capability=CAP_COMPLETE_WEBAUTHN_REGISTRATION,
            version=CAP_VERSION,
            payload=CompleteWebAuthnRegistrationCommand(
                subject_id=context.actor.id, response=body, label=body.get("label")
            ),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except IdentityError:
            return _problem(
                400,
                "identity.mfa.verification-failed",
                "MFA verification failed",
                "The presented authentication factor could not be verified.",
                context.correlation_id,
            )
        completed: CompleteWebAuthnRegistrationResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=200,
            content={
                "credential_id": completed.credential_id,
                "sign_count": completed.sign_count,
            },
            headers={"X-Correlation-Id": context.correlation_id},
        )

    @router.post("/mfa/webauthn/authenticate/begin")
    async def webauthn_authenticate_begin(request: Request) -> Response:
        guard = _mfa_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        deps, context, _session_id = guard
        command = Command(
            capability=CAP_BEGIN_WEBAUTHN_AUTHENTICATION,
            version=CAP_VERSION,
            payload=BeginWebAuthnAuthenticationCommand(subject_id=context.actor.id),
        )
        result = deps.command_bus.dispatch(command, context)
        begun: BeginWebAuthnAuthenticationResult = result.value  # type: ignore[assignment]
        return Response(
            status_code=200,
            content=begun.options_json,
            media_type="application/json",
            headers={"X-Correlation-Id": context.correlation_id},
        )

    @router.post("/mfa/webauthn/authenticate/complete")
    async def webauthn_authenticate_complete(request: Request) -> Response:
        guard = _mfa_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        deps, context, session_id = guard
        body = await _json_body(request)
        command = Command(
            capability=CAP_COMPLETE_WEBAUTHN_AUTHENTICATION,
            version=CAP_VERSION,
            payload=CompleteWebAuthnAuthenticationCommand(
                subject_id=context.actor.id, response=body, session_id=session_id
            ),
        )
        try:
            result = deps.command_bus.dispatch(command, context)
        except IdentityError:
            return _problem(
                400,
                "identity.mfa.verification-failed",
                "MFA verification failed",
                "The presented authentication factor could not be verified.",
                context.correlation_id,
            )
        completed: CompleteWebAuthnAuthenticationResult = result.value  # type: ignore[assignment]
        return JSONResponse(
            status_code=200,
            content={
                "verified": completed.verified,
                "assurance": completed.assurance.value if completed.assurance else None,
            },
            headers={"X-Correlation-Id": context.correlation_id},
        )

    @router.get("/mfa/step-up")
    def step_up_status(request: Request) -> Response:
        """Privileged-action guard: 200 when step-up is satisfied, else 403 (FR-IDN-003)."""
        deps = _deps(request)
        authed = _authenticated_context(request, deps)
        if authed is None:
            return _unauthenticated(_correlation_id(request))
        context, session_id = authed
        query = Query(
            capability=CAP_ENFORCE_STEP_UP,
            version=CAP_VERSION,
            parameters=EnforceStepUpQuery(session_id=session_id),
            resource=ResourceRef(type=_SESSION_RESOURCE, id=session_id),
        )
        try:
            deps.query_bus.dispatch(query, context)
        except StepUpRequired:
            return _problem(
                403,
                "identity.mfa.step-up-required",
                "Step-up authentication required",
                "This action requires step-up multi-factor authentication.",
                context.correlation_id,
            )
        except IdentityError:
            return _unauthenticated(context.correlation_id)
        return JSONResponse(
            status_code=200,
            content={"step_up_satisfied": True},
            headers={"X-Correlation-Id": context.correlation_id},
        )

    return router


async def _json_body(request: Request) -> dict[str, object]:
    """Parse the JSON request body into a dict (empty dict when absent or not an object)."""
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
