"""Reference egress guard + guarded HTTP client (adapters behind ``EgressGuardPort``, EVAL-SEC-005).

:class:`AllowlistEgressGuard` is the single authoritative outbound-HTTP gate. It parses a URL,
resolves the host to its IP set (DNS is the only infrastructure here — injected as :data:`Resolver`
so tests stay offline and a deployment can front it with an egress proxy), applies the kernel's pure
deny-by-default :class:`~northstar.kernel.security.egress.EgressPolicy` and, on refusal, records a
tamper-evident ``egress.denied`` audit event before raising
:class:`~northstar.kernel.security.egress.EgressBlocked`.

:class:`GuardedHttpClient` performs an actual request only through that guard and re-authorizes the
Location target of **every** redirect hop, so DNS-rebinding and redirect-to-internal SSRF are both
closed: an allowlisted name that resolves to an internal IP, or a 302 that points at the metadata
service, is refused before the socket is opened.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin

from northstar.kernel.audit.ports import AuditOutcome, AuditRecorderPort
from northstar.kernel.context import Actor, ActorType, ResourceRef
from northstar.kernel.security.egress import (
    AuthorizedEgress,
    EgressBlocked,
    EgressPolicy,
    EgressReason,
    parse_target,
)

# A resolver maps a hostname to the tuple of IP-address strings it currently resolves to.
Resolver = Callable[[str], tuple[str, ...]]

_EGRESS_ACTOR = Actor(type=ActorType.SERVICE, id="platform.egress-guard")
_EGRESS_EVENT = "security.egress.decision"
_EGRESS_ACTION = "platform.egress.authorize"


def system_resolver(host: str) -> tuple[str, ...]:
    """Resolve ``host`` to its unique IPv4/IPv6 addresses via the system DNS (blocking)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return ()
    seen: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in seen:
            seen.append(address)
    return tuple(seen)


class AllowlistEgressGuard:
    """Deny-by-default egress guard: resolve, evaluate, audit-on-block (``EgressGuardPort``)."""

    def __init__(
        self,
        *,
        policy: EgressPolicy,
        resolver: Resolver = system_resolver,
        audit: AuditRecorderPort | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._policy = policy
        self._resolver = resolver
        self._audit = audit
        self._clock = clock

    def _audit_block(self, *, host: str, reason: EgressReason, correlation_id: str) -> None:
        if self._audit is None:
            return
        self._audit.record(
            event_type=_EGRESS_EVENT,
            actor=_EGRESS_ACTOR,
            action=_EGRESS_ACTION,
            outcome=AuditOutcome.DENIED,
            correlation_id=correlation_id,
            resource=ResourceRef(type="egress.destination", id=host or "unknown"),
            reason_codes=(f"egress.{reason.value}",),
        )

    def authorize(self, url: str, *, correlation_id: str | None = None) -> AuthorizedEgress:
        """Return an authorized destination for ``url`` or raise ``EgressBlocked`` (audited)."""
        correlation = correlation_id or f"egress-{int(self._clock().timestamp())}"
        target = parse_target(url)
        if target is None:
            self._audit_block(host="", reason=EgressReason.MALFORMED, correlation_id=correlation)
            raise EgressBlocked(reason=EgressReason.MALFORMED, destination=url)
        resolved = self._resolver(target.host)
        decision = self._policy.evaluate(
            scheme=target.scheme, host=target.host, resolved_ips=resolved
        )
        if decision.blocked:
            reason = decision.reason or EgressReason.NOT_ALLOWLISTED
            self._audit_block(host=target.host, reason=reason, correlation_id=correlation)
            raise EgressBlocked(reason=reason, destination=target.host)
        return AuthorizedEgress(url=url, host=target.host, resolved_ips=resolved)


@dataclass(frozen=True, slots=True)
class GuardedResponse:
    """The neutral result of a guarded HTTP call (status + headers + body + resolved host)."""

    status: int
    body: bytes
    host: str
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def location(self) -> str | None:
        for name, value in self.headers.items():
            if name.lower() == "location":
                return value
        return None

    @property
    def is_redirect(self) -> bool:
        return self.status in (301, 302, 303, 307, 308) and self.location is not None


# A transport performs the actual byte exchange for an ALREADY-authorized request.
HttpTransport = Callable[[str, str], GuardedResponse]


class GuardedHttpClient:
    """An outbound HTTP client that authorizes every hop (initial + each redirect) via the guard."""

    def __init__(
        self,
        *,
        guard: AllowlistEgressGuard,
        transport: HttpTransport,
        max_redirects: int = 5,
    ) -> None:
        self._guard = guard
        self._transport = transport
        self._max_redirects = max_redirects

    def request(
        self, method: str, url: str, *, correlation_id: str | None = None
    ) -> GuardedResponse:
        """Perform ``method url`` following redirects, re-authorizing the target of each hop."""
        current = url
        for _hop in range(self._max_redirects + 1):
            self._guard.authorize(current, correlation_id=correlation_id)
            response = self._transport(method, current)
            location = response.location
            if not response.is_redirect or location is None:
                return response
            current = urljoin(current, location)
        raise EgressBlocked(reason=EgressReason.TOO_MANY_REDIRECTS, destination=current)

    def get(self, url: str, *, correlation_id: str | None = None) -> GuardedResponse:
        return self.request("GET", url, correlation_id=correlation_id)


__all__ = [
    "AllowlistEgressGuard",
    "GuardedHttpClient",
    "GuardedResponse",
    "HttpTransport",
    "Resolver",
    "system_resolver",
]
