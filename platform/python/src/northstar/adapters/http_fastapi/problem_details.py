"""RFC 9457 ``application/problem+json`` mapping at the HTTP trust boundary (rule 40/50).

Typed kernel errors are translated into safe problem documents here — never inside the kernel,
which stays infra-free. Statuses/codes/retryability come from
``spec/contracts/error-catalog.yaml`` (mirrored in :data:`_ERROR_CATALOG`): ``PolicyDenied`` ->
403 ``authorization.denied``; ``UnknownCapability`` -> 404 ``resource.not-found``; request
validation -> 422 ``validation.failed``. ``detail`` is caller-safe (no secrets, stack traces or
existence leaks, docs/05 §6); internal diagnostics are correlated by ``trace_id`` only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from northstar.kernel.errors import KernelError, PolicyDenied, UnknownCapability
from northstar.kernel.security.egress import EgressBlocked
from northstar.kernel.security.rate_limit import RateLimitExceeded

PROBLEM_CONTENT_TYPE = "application/problem+json"
_TYPE_BASE = "https://errors.northstar.example/"
_CORRELATION_HEADER = "X-Correlation-Id"
_RETRY_AFTER_HEADER = "Retry-After"


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """A single row of the canonical error catalog (code/status/retryable/safe detail)."""

    code: str
    http_status: int
    retryable: bool
    safe_detail: str


# Mirror of spec/contracts/error-catalog.yaml (the canonical source; kept in sync, not invented).
_ERROR_CATALOG: dict[str, CatalogEntry] = {
    "validation.failed": CatalogEntry(
        "validation.failed", 422, False, "One or more inputs are invalid."
    ),
    "authentication.required": CatalogEntry(
        "authentication.required", 401, False, "Authentication is required."
    ),
    "authorization.denied": CatalogEntry(
        "authorization.denied", 403, False, "The actor is not permitted to perform this action."
    ),
    "resource.not-found": CatalogEntry(
        "resource.not-found", 404, False, "The resource does not exist or is not visible."
    ),
    "quota.exceeded": CatalogEntry(
        "quota.exceeded", 429, True, "The applicable usage limit has been reached."
    ),
    "state.conflict": CatalogEntry(
        "state.conflict", 409, False, "The resource state conflicts with this request."
    ),
    "idempotency.conflict": CatalogEntry(
        "idempotency.conflict", 409, False, "The idempotency key was used for a different request."
    ),
    "provider.unavailable": CatalogEntry(
        "provider.unavailable", 503, True, "A required provider is temporarily unavailable."
    ),
}

# Adapter-level safe fallback for an unhandled failure (not a domain contract code): never leaks
# internal diagnostics; those are correlated by trace_id only (docs/05 §6).
_INTERNAL = CatalogEntry("internal.error", 500, False, "An unexpected error occurred.")


@dataclass(frozen=True, slots=True)
class ProblemDetail:
    """An RFC 9457 problem document with the Northstar extensions (docs/05 §6)."""

    status: int
    code: str
    title: str
    detail: str
    retryable: bool
    trace_id: str
    correlation_id: str | None
    violations: tuple[dict[str, Any], ...] = ()
    retry_after_seconds: int | None = None

    def to_body(self) -> dict[str, Any]:
        return {
            "type": f"{_TYPE_BASE}{self.code.replace('.', '/')}",
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "code": self.code,
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
            "violations": list(self.violations),
            "retryable": self.retryable,
        }


def _new_trace_id() -> str:
    return f"trc_{uuid.uuid4().hex}"


def _correlation_of(request: Request) -> str | None:
    return request.headers.get(_CORRELATION_HEADER)


def _problem_response(problem: ProblemDetail) -> JSONResponse:
    headers: dict[str, str] = {}
    if problem.retry_after_seconds is not None:
        headers[_RETRY_AFTER_HEADER] = str(problem.retry_after_seconds)
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_body(),
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers or None,
    )


def problem_response(problem: ProblemDetail) -> JSONResponse:
    """Public builder for a ``problem+json`` response (used by the rate-limit middleware)."""
    return _problem_response(problem)


def problem_from_rate_limit(err: RateLimitExceeded, *, correlation_id: str | None) -> ProblemDetail:
    """Map an over-budget throttle to a 429 ``quota.exceeded`` problem with ``Retry-After``."""
    entry = _entry("quota.exceeded")
    return ProblemDetail(
        status=entry.http_status,
        code=entry.code,
        title="Too many requests",
        detail=entry.safe_detail,
        retryable=entry.retryable,
        trace_id=_new_trace_id(),
        correlation_id=correlation_id,
        violations=({"code": err.diagnostics[0].code, "message": err.diagnostics[0].message},),
        retry_after_seconds=err.retry_after_seconds,
    )


def problem_from_egress_blocked(err: EgressBlocked, *, correlation_id: str | None) -> ProblemDetail:
    """Map a refused outbound destination to a 403 ``authorization.denied`` problem (SSRF).

    A blocked egress is a deny-by-default authorization decision at the outbound trust boundary; the
    stable reason code travels in ``violations`` (never the raw address list or attacker payload).
    """
    entry = _entry("authorization.denied")
    return ProblemDetail(
        status=entry.http_status,
        code=entry.code,
        title="The outbound destination is not permitted",
        detail=entry.safe_detail,
        retryable=entry.retryable,
        trace_id=_new_trace_id(),
        correlation_id=correlation_id,
        violations=({"code": err.diagnostics[0].code, "message": err.diagnostics[0].message},),
    )


def _entry(code: str) -> CatalogEntry:
    return _ERROR_CATALOG.get(code, _INTERNAL)


def problem_from_policy_denied(err: PolicyDenied, *, correlation_id: str | None) -> ProblemDetail:
    entry = _entry("authorization.denied")
    violations = tuple(
        {"code": d.code, "message": d.message, "field": d.detail} for d in err.diagnostics
    )
    return ProblemDetail(
        status=entry.http_status,
        code=entry.code,
        title="The requested action is not permitted",
        detail=entry.safe_detail,
        retryable=entry.retryable,
        trace_id=_new_trace_id(),
        correlation_id=correlation_id,
        violations=violations,
    )


def problem_from_unknown_capability(
    err: UnknownCapability, *, correlation_id: str | None
) -> ProblemDetail:
    entry = _entry("resource.not-found")
    return ProblemDetail(
        status=entry.http_status,
        code=entry.code,
        title="The requested capability is not registered",
        detail=entry.safe_detail,
        retryable=entry.retryable,
        trace_id=_new_trace_id(),
        correlation_id=correlation_id,
        violations=({"code": err.diagnostics[0].code, "message": err.diagnostics[0].message},),
    )


def problem_from_validation(
    err: RequestValidationError, *, correlation_id: str | None
) -> ProblemDetail:
    entry = _entry("validation.failed")
    violations = tuple(
        {
            "field": ".".join(str(p) for p in e.get("loc", ()) if p not in ("body",)),
            "code": str(e.get("type", "invalid")),
            "message": str(e.get("msg", "invalid value")),
        }
        for e in err.errors()
    )
    return ProblemDetail(
        status=entry.http_status,
        code=entry.code,
        title="The request envelope is invalid",
        detail=entry.safe_detail,
        retryable=entry.retryable,
        trace_id=_new_trace_id(),
        correlation_id=correlation_id,
        violations=violations,
    )


def _problem_from_kernel_error(err: KernelError, *, correlation_id: str | None) -> ProblemDetail:
    if isinstance(err, PolicyDenied):
        return problem_from_policy_denied(err, correlation_id=correlation_id)
    if isinstance(err, UnknownCapability):
        return problem_from_unknown_capability(err, correlation_id=correlation_id)
    if isinstance(err, RateLimitExceeded):
        return problem_from_rate_limit(err, correlation_id=correlation_id)
    if isinstance(err, EgressBlocked):
        return problem_from_egress_blocked(err, correlation_id=correlation_id)
    return ProblemDetail(
        status=_INTERNAL.http_status,
        code=_INTERNAL.code,
        title="An unexpected error occurred",
        detail=_INTERNAL.safe_detail,
        retryable=_INTERNAL.retryable,
        trace_id=_new_trace_id(),
        correlation_id=correlation_id,
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Register the problem-details handlers so every error is ``problem+json`` (rule 40)."""

    async def _on_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem_response(
            problem_from_validation(exc, correlation_id=_correlation_of(request))
        )

    async def _on_kernel_error(request: Request, exc: KernelError) -> JSONResponse:
        return _problem_response(
            _problem_from_kernel_error(exc, correlation_id=_correlation_of(request))
        )

    app.add_exception_handler(RequestValidationError, _on_validation)
    app.add_exception_handler(KernelError, _on_kernel_error)
