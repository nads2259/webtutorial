"""Reference message provider adapter (in-memory), idempotent by key (FR-MSG-006, docs/16 §8).

This is the reference implementation of :class:`MessageProviderPort`. A real ESP/SMS/push provider
is a drop-in adapter swap behind the same port; the domain never learns provider concepts (LAW-12).

Idempotency is the core guarantee: two submissions with the same ``idempotency_key`` produce at
most ONE visible send. The second call returns the prior ``provider_message_id`` with
``deduplicated=True`` and does NOT increment :attr:`sent_count`. Transient failures are simulated
via ``fail_first`` and are raised (never swallowed) so the caller retries; a retry with the same key
still results in a single send.
"""

from __future__ import annotations

from ..application.ports import ProviderReceipt, SubmissionRequest
from ..domain.model import DeliveryStatus


class ProviderTransientError(RuntimeError):
    """A transient provider failure the caller should retry (never a duplicate on retry)."""


class InMemoryMessageProvider:
    """Deterministic, idempotent in-memory provider for tests and the reference wiring."""

    def __init__(self, *, fail_first: int = 0) -> None:
        self._accepted: dict[str, str] = {}
        self._remaining_failures = fail_first
        self.submit_attempts = 0
        self.sent_count = 0

    def submit(self, request: SubmissionRequest) -> ProviderReceipt:
        self.submit_attempts += 1
        prior = self._accepted.get(request.idempotency_key)
        if prior is not None:
            return ProviderReceipt(
                provider_message_id=prior, status=DeliveryStatus.ACCEPTED, deduplicated=True
            )
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise ProviderTransientError(
                f"transient provider failure for key {request.idempotency_key!r}"
            )
        # Deterministic provider id derived from the idempotency key (stable across retries).
        message_id = f"prov-{request.idempotency_key}"
        self._accepted[request.idempotency_key] = message_id
        self.sent_count += 1
        return ProviderReceipt(
            provider_message_id=message_id, status=DeliveryStatus.ACCEPTED, deduplicated=False
        )


__all__ = ["InMemoryMessageProvider", "ProviderTransientError"]
