"""In-memory reference audit recorder computing a stable ``record_sha256`` (LAW-14).

Proves the audit hook end to end (LAW-01) without a durable store (that is IMPL-004). The
integrity digest is ``sha256`` over a canonical JSON serialization of the record's content
(the digest field excluded), so the same inputs always yield the same hash and any tampering
with a stored field is detectable. Injecting ``clock``/``id_factory`` keeps it deterministic
under test.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from ..context import Actor, ResourceRef
from .ports import AuditOutcome, AuditRecord


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_evidence_id() -> str:
    return str(uuid.uuid4())


def compute_record_sha256(payload: dict[str, object]) -> str:
    """Return the hex ``sha256`` over a canonical (sorted-key, compact) JSON serialization."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InMemoryAuditRecorder:
    """Append-only in-memory recorder that seals each entry with an integrity digest.

    Stored records are exposed as an immutable tuple so callers can inspect the audit trail
    without mutating it.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], str] = _utc_now_iso,
        id_factory: Callable[[], str] = _new_evidence_id,
    ) -> None:
        self._clock = clock
        self._id_factory = id_factory
        self._records: list[AuditRecord] = []

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    def record(
        self,
        *,
        event_type: str,
        actor: Actor,
        action: str,
        outcome: AuditOutcome,
        correlation_id: str,
        resource: ResourceRef | None = None,
        decision_ref: str | None = None,
        reason_codes: tuple[str, ...] = (),
    ) -> AuditRecord:
        evidence_id = self._id_factory()
        occurred_at = self._clock()
        content: dict[str, object] = {
            "evidence_id": evidence_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "actor": {
                "type": actor.type.value,
                "id": actor.id,
                "delegated_by": actor.delegated_by,
            },
            "action": action,
            "outcome": outcome.value,
            "correlation_id": correlation_id,
            "resource": (None if resource is None else {"type": resource.type, "id": resource.id}),
            "decision_ref": decision_ref,
            "reason_codes": list(reason_codes),
        }
        record = AuditRecord(
            evidence_id=evidence_id,
            event_type=event_type,
            occurred_at=occurred_at,
            actor=actor,
            action=action,
            outcome=outcome,
            correlation_id=correlation_id,
            record_sha256=compute_record_sha256(content),
            resource=resource,
            decision_ref=decision_ref,
            reason_codes=reason_codes,
        )
        self._records.append(record)
        return record
