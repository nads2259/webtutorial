"""Guarded outbound HTTP for AI tools (EVAL-SEC-005 / NFR-SEC-005, LAW-09).

An AI tool that fetches an external URL is a classic SSRF vector (indirect prompt injection can
steer it at ``169.254.169.254`` or an internal service). :class:`GuardedFetchTool` is the ONLY fetch
path the AI module exposes: it routes every request through the kernel :class:`EgressGuardPort`
before a byte leaves, so a loopback/private/link-local/metadata/non-allowlisted/redirect-to-blocked
target is refused (``EgressBlocked``) and an allowlisted public host is permitted. It is a tool
callable so the Tool Broker can register it against the declared fetch tool; the AI actor therefore
holds no unguarded network authority (ARCH-009).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from northstar.kernel.security.egress import EgressGuardPort

from ..application.ports import ToolExecutionContext

# A byte fetcher for an ALREADY-authorized URL (injected; default is a no-op ack so the reference
# build has no real network dependency). A deployment supplies a bounded HTTP client here.
UrlFetcher = Callable[[str], bytes]


class GuardedFetchTool:
    """A tool callable that fetches a URL ONLY after the egress guard authorizes it."""

    def __init__(self, *, guard: EgressGuardPort, fetch: UrlFetcher | None = None) -> None:
        self._guard = guard
        self._fetch = fetch

    def __call__(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> Mapping[str, object]:
        url = str(arguments.get("url", ""))
        # Raises EgressBlocked for any refused destination (audited inside the guard).
        authorized = self._guard.authorize(url)
        body = self._fetch(authorized.url) if self._fetch is not None else b""
        return {
            "status": "fetched",
            "host": authorized.host,
            "resolved_ips": list(authorized.resolved_ips),
            "bytes": len(body),
        }


__all__ = ["GuardedFetchTool", "UrlFetcher"]
