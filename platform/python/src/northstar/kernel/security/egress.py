"""Pure deny-by-default SSRF / egress policy (LAW-02/08, rule 50, EVAL-SEC-005, NFR-SEC-005).

Stdlib-only and infrastructure-free: this module is the single authoritative *decision* for whether
an outbound HTTP destination is permitted. It never performs DNS or opens a socket — the adapter
resolves the host and hands the resolved IPs here (so the kernel stays pure and a deployment can
swap the resolver). The policy is **deny-by-default**, mirroring the simulation sandbox's
``permits_egress`` (docs/15, FR-SIM-003) and extending that isolation to fetchers/webhooks/AI tools:

* the scheme must be ``http``/``https`` (no ``file:``/``gopher:``/``ftp:`` SSRF vectors);
* the host must be on the explicit allowlist (an unknown host is refused even if it is public);
* **every** resolved IP must be a normal public address — a loopback / private / link-local /
  cloud-metadata (``169.254.169.254``) / reserved / multicast / unspecified target is refused. This
  closes DNS-rebinding: an allowlisted name that resolves to an internal IP is still blocked.

The redirect defense lives in the adapter's guarded client, which re-evaluates this policy for the
Location target of every hop, so a redirect to a blocked destination is refused.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from ..errors import Diagnostic, KernelError

# The cloud instance-metadata endpoints (IMDS) — a canonical SSRF target (doc 08 §6, OWASP A10).
_METADATA_IPS: frozenset[str] = frozenset({"169.254.169.254", "fd00:ec2::254"})


class EgressReason(StrEnum):
    """Why an outbound destination was refused (stable, machine-comparable, non-secret)."""

    SCHEME_FORBIDDEN = "scheme_forbidden"
    NOT_ALLOWLISTED = "not_allowlisted"
    LOOPBACK = "loopback"
    PRIVATE = "private"
    LINK_LOCAL = "link_local"
    METADATA = "metadata"
    RESERVED = "reserved"
    MULTICAST = "multicast"
    UNSPECIFIED = "unspecified"
    UNRESOLVABLE = "unresolvable"
    MALFORMED = "malformed"
    REDIRECT_BLOCKED = "redirect_blocked"
    TOO_MANY_REDIRECTS = "too_many_redirects"


class EgressBlocked(KernelError):  # noqa: N818 canonical error name
    """An outbound HTTP destination was refused by the deny-by-default egress policy (SSRF).

    Carries the stable :class:`EgressReason` and the (non-secret) ``destination`` host so the trust
    boundary maps it to a typed RFC 9457 problem and the audit trail records a tamper-evident
    ``egress.denied`` event. The ``detail`` is the reason code only — never a raw address list or
    attacker-controlled payload.
    """

    def __init__(self, *, reason: EgressReason, destination: str) -> None:
        self.reason = reason
        self.destination = destination
        diag = Diagnostic(
            code="egress.blocked",
            message=f"outbound destination refused ({reason.value})",
            detail=reason.value,
        )
        super().__init__(f"egress blocked: {reason.value}", (diag,))


def classify_ip(ip_text: str) -> EgressReason | None:
    """Return the blocking :class:`EgressReason` for ``ip_text``, or ``None`` for a public IP.

    Metadata is checked before the overlapping link-local range so an IMDS hit reports ``metadata``.
    """
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return EgressReason.MALFORMED
    if ip_text in _METADATA_IPS:
        return EgressReason.METADATA
    if ip.is_loopback:
        return EgressReason.LOOPBACK
    if ip.is_link_local:
        return EgressReason.LINK_LOCAL
    if ip.is_unspecified:
        return EgressReason.UNSPECIFIED
    if ip.is_multicast:
        return EgressReason.MULTICAST
    if ip.is_private:
        return EgressReason.PRIVATE
    if ip.is_reserved:
        return EgressReason.RESERVED
    return None


@dataclass(frozen=True, slots=True)
class EgressDecision:
    """The outcome of an egress evaluation: allowed, or blocked with a stable reason."""

    allowed: bool
    reason: EgressReason | None = None

    @property
    def blocked(self) -> bool:
        return not self.allowed


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """A deny-by-default outbound-HTTP allowlist policy (rule 50, EVAL-SEC-005).

    ``allowlist`` is the set of permitted hostnames (compared case-insensitively). Egress is refused
    unless the scheme is allowed, the host is explicitly allowlisted AND every resolved IP is a
    normal public address. An empty allowlist therefore blocks everything (the safest default).
    """

    allowlist: frozenset[str]
    allowed_schemes: frozenset[str] = frozenset({"http", "https"})

    def host_allowed(self, host: str) -> bool:
        return host.lower() in {h.lower() for h in self.allowlist}

    def evaluate(self, *, scheme: str, host: str, resolved_ips: tuple[str, ...]) -> EgressDecision:
        """Evaluate a destination whose host has already been resolved to ``resolved_ips``."""
        if scheme.lower() not in self.allowed_schemes:
            return EgressDecision(allowed=False, reason=EgressReason.SCHEME_FORBIDDEN)
        if not host or not self.host_allowed(host):
            return EgressDecision(allowed=False, reason=EgressReason.NOT_ALLOWLISTED)
        if not resolved_ips:
            return EgressDecision(allowed=False, reason=EgressReason.UNRESOLVABLE)
        for ip_text in resolved_ips:
            reason = classify_ip(ip_text)
            if reason is not None:
                return EgressDecision(allowed=False, reason=reason)
        return EgressDecision(allowed=True)


@dataclass(frozen=True, slots=True)
class ParsedTarget:
    """The scheme + host + port extracted from a URL for evaluation (pure helper)."""

    scheme: str
    host: str
    port: int | None


def parse_target(url: str) -> ParsedTarget | None:
    """Split ``url`` into scheme/host/port; return ``None`` when it has no usable host."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = parts.hostname
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    return ParsedTarget(scheme=parts.scheme, host=host, port=port)


@dataclass(frozen=True, slots=True)
class AuthorizedEgress:
    """A destination that passed the egress policy: URL, host and resolved IPs (evidence)."""

    url: str
    host: str
    resolved_ips: tuple[str, ...]


@runtime_checkable
class EgressGuardPort(Protocol):
    """The single seam ALL outbound HTTP must pass before a request leaves the process (LAW-12).

    :meth:`authorize` resolves the URL's host, applies the deny-by-default :class:`EgressPolicy` and
    either returns an :class:`AuthorizedEgress` (with the resolved IPs as attributable evidence) or
    raises :class:`EgressBlocked`. Implementations own the DNS resolution and the audit sink; the
    kernel policy they consume stays pure. Fetchers, webhook delivery and AI-tool calls hold this
    port and cannot make an unguarded call.
    """

    def authorize(self, url: str) -> AuthorizedEgress: ...


__all__ = [
    "AuthorizedEgress",
    "EgressBlocked",
    "EgressDecision",
    "EgressGuardPort",
    "EgressPolicy",
    "EgressReason",
    "ParsedTarget",
    "classify_ip",
    "parse_target",
]
