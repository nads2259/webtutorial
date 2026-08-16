"""The upload validator: pure policy + quarantine scan + audited rejection (EVAL-SEC-004, LAW-14).

:class:`UploadValidator` is the single authoritative *acceptance* gate for ingested bytes. It:

1. runs the pure, deny-by-default :class:`~northstar.kernel.security.upload.UploadPolicy`
   (content-based MIME sniff, size + decompression-bomb caps, SVG/HTML active-content refusal);
2. runs the :class:`~northstar.kernel.security.upload.ScanPort` quarantine scan *after* the policy
   passes and *before* acceptance, refusing a flagged artifact;
3. records a tamper-evident ``upload.rejected`` audit event on every refusal (LAW-14), carrying the
   stable reason code only — never the raw bytes or a decompressed payload.

It raises :class:`~northstar.kernel.security.upload.UploadRejected` on refusal; the trust boundary
maps that typed error to an RFC 9457 problem (see :mod:`.problem`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from northstar.kernel.audit.ports import AuditOutcome, AuditRecorderPort
from northstar.kernel.context import Actor, ActorType, ResourceRef
from northstar.kernel.security.upload import (
    ScanPort,
    UploadPolicy,
    UploadReason,
    UploadRejected,
    ValidatedUpload,
)

UPLOAD_DECISION_EVENT = "security.upload.decision"
UPLOAD_DECISION_ACTION = "platform.upload.validate"

_DEFAULT_UPLOAD_ACTOR = Actor(type=ActorType.SERVICE, id="platform.upload-validator")


class UploadValidator:
    """Deny-by-default upload acceptance gate: validate + scan, audit-on-reject (EVAL-SEC-004)."""

    def __init__(
        self,
        *,
        policy: UploadPolicy | None = None,
        scanner: ScanPort,
        audit: AuditRecorderPort | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._policy = policy or UploadPolicy()
        self._scanner = scanner
        self._audit = audit
        self._clock = clock

    def validate(
        self,
        *,
        filename: str | None,
        declared_content_type: str,
        data: bytes,
        actor: Actor | None = None,
        correlation_id: str | None = None,
    ) -> ValidatedUpload:
        """Return a :class:`ValidatedUpload` for accepted bytes, else raise ``UploadRejected``.

        ``actor``/``correlation_id`` scope the audit record; when omitted a platform service actor
        and a synthetic correlation id are used so a rejection is still attributable.
        """
        acting = actor or _DEFAULT_UPLOAD_ACTOR
        correlation = correlation_id or f"upload-{int(self._clock().timestamp())}"
        try:
            validated = self._policy.inspect(
                filename=filename, declared_content_type=declared_content_type, data=data
            )
        except UploadRejected as err:
            self._audit_reject(err.reason, filename=filename, actor=acting, correlation=correlation)
            raise
        verdict = self._scanner.scan(data=data, content_type=validated.sniffed_content_type)
        if verdict.flagged:
            err = UploadRejected(
                reason=UploadReason.SCAN_FLAGGED,
                declared_content_type=validated.declared_content_type,
                filename=filename,
            )
            self._audit_reject(err.reason, filename=filename, actor=acting, correlation=correlation)
            raise err
        return validated

    def _audit_reject(
        self, reason: UploadReason, *, filename: str | None, actor: Actor, correlation: str
    ) -> None:
        if self._audit is None:
            return
        self._audit.record(
            event_type=UPLOAD_DECISION_EVENT,
            actor=actor,
            action=UPLOAD_DECISION_ACTION,
            outcome=AuditOutcome.DENIED,
            correlation_id=correlation,
            resource=ResourceRef(type="upload.artifact", id=filename or "unknown"),
            reason_codes=(f"upload.{reason.value}",),
        )


__all__ = ["UPLOAD_DECISION_ACTION", "UPLOAD_DECISION_EVENT", "UploadValidator"]
