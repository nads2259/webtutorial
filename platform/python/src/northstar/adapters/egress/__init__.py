"""Egress guard adapters (the infrastructure behind :class:`EgressGuardPort`, EVAL-SEC-005).

The kernel owns the pure deny-by-default SSRF policy (:mod:`northstar.kernel.security.egress`); this
package holds the pieces that need infrastructure:

* :class:`AllowlistEgressGuard` — resolves a URL's host to IPs (DNS), applies the policy and audits
  a refusal. It is the single seam fetchers/webhooks/AI tools call before any request leaves.
* :class:`GuardedHttpClient` — an outbound client that re-authorizes **every** redirect hop through
  the guard, so a redirect whose Location points to a blocked target is refused.

The DNS resolver and the HTTP transport are injected, so tests are deterministic and offline and a
production deployment can supply a real resolver/transport (or an egress proxy) without changing the
policy or any caller.
"""

from __future__ import annotations

from .guard import (
    AllowlistEgressGuard,
    GuardedHttpClient,
    GuardedResponse,
    HttpTransport,
    Resolver,
    system_resolver,
)

__all__ = [
    "AllowlistEgressGuard",
    "GuardedHttpClient",
    "GuardedResponse",
    "HttpTransport",
    "Resolver",
    "system_resolver",
]
