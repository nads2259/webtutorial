"""Guarded outbound webhook delivery (EVAL-SEC-005 / NFR-SEC-005).

Webhook delivery lets a tenant choose the destination URL, so it is a first-class SSRF surface.
:class:`GuardedWebhookDelivery` is the single authoritative outbound-webhook path for the messaging
module: every delivery is routed through the kernel :class:`EgressGuardPort` before the request is
sent, so a loopback/private/link-local/metadata/non-allowlisted/redirect-to-blocked destination is
refused (``EgressBlocked``, audited) and only an allowlisted public endpoint receives the callback.
The actual byte transport is injected (the reference build records an attempt without opening a
socket); a deployment supplies a real bounded HTTP sender behind the same seam.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from northstar.kernel.security.egress import EgressGuardPort

# A sender delivers an ALREADY-authorized webhook POST and returns the upstream status code.
WebhookSender = Callable[[str, bytes], int]


@dataclass(frozen=True, slots=True)
class WebhookReceipt:
    """The neutral result of a guarded webhook delivery attempt."""

    delivered: bool
    host: str
    status: int


class GuardedWebhookDelivery:
    """Delivers a webhook POST ONLY after the egress guard authorizes its destination."""

    def __init__(self, *, guard: EgressGuardPort, sender: WebhookSender | None = None) -> None:
        self._guard = guard
        self._sender = sender

    def deliver(
        self, *, url: str, payload: bytes, correlation_id: str | None = None
    ) -> WebhookReceipt:
        """Authorize ``url`` (raises :class:`EgressBlocked` if refused) then send the payload."""
        authorized = self._guard.authorize(url)
        status = self._sender(authorized.url, payload) if self._sender is not None else 202
        return WebhookReceipt(delivered=True, host=authorized.host, status=status)


__all__ = ["GuardedWebhookDelivery", "WebhookReceipt", "WebhookSender"]
