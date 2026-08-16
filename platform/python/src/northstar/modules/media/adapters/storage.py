"""The media module's authoritative validated ingestion seam (EVAL-MED-001, reuses EVAL-SEC-004).

:class:`ValidatingMediaStorage` implements :class:`~northstar.modules.media.application.ports.
MediaStoragePort` by DELEGATING every write to the shared H02
:class:`~northstar.adapters.upload.ValidatingObjectStorage`. Because that decorator runs the
deny-by-default :class:`~northstar.adapters.upload.UploadValidator` (content-based MIME sniffing,
size + decompression-bomb caps, SVG/HTML active-content refusal, quarantine scan, audit-on-reject)
BEFORE the inner store ever receives bytes, there is no unvalidated media write path — a mismatched
or malicious asset raises :class:`~northstar.kernel.security.upload.UploadRejected` and nothing is
stored. The validator/object-storage are reused unchanged (not reimplemented).

The persisted object is labelled by its *sniffed* content type, so the recorded
:class:`~northstar.modules.media.application.ports.StoredBlob.content_type` is the validated type of
the bytes — the deny-by-default policy guarantees ``sniffed == declared`` for any accepted upload.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from northstar.adapters.upload import (
    ObjectStoreLike,
    PassThroughScanner,
    UploadValidator,
    ValidatingObjectStorage,
)
from northstar.kernel.audit.ports import AuditRecorderPort
from northstar.kernel.context import Actor
from northstar.kernel.security.upload import ScanPort, UploadPolicy, sniff_content_type

from ..application.ports import StoredBlob


def _normalize(declared: str) -> str:
    return declared.split(";", 1)[0].strip().lower()


class ValidatingMediaStorage:
    """Media storage that validates every write via the H02 validated object store (deny-first)."""

    def __init__(self, *, storage: ValidatingObjectStorage) -> None:
        self._storage = storage

    def store(
        self,
        *,
        key: str,
        data: bytes,
        declared_content_type: str,
        actor: Actor | None = None,
        correlation_id: str | None = None,
    ) -> StoredBlob:
        """Validate ``data`` then store it; raise ``UploadRejected`` (audited) on refusal.

        The write is delegated to the shared ``ValidatingObjectStorage`` so the bytes pass the
        upload validator first; on success the sniffed type is the validated label persisted.
        """
        stored_key = self._storage.put(
            key=key,
            data=data,
            content_type=declared_content_type,
            actor=actor,
            correlation_id=correlation_id,
        )
        sniffed = sniff_content_type(data)
        return StoredBlob(
            key=stored_key,
            content_type=sniffed or _normalize(declared_content_type),
            byte_size=len(data),
        )


def build_media_storage(
    *,
    inner: ObjectStoreLike,
    policy: UploadPolicy | None = None,
    scanner: ScanPort | None = None,
    audit: AuditRecorderPort | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ValidatingMediaStorage:
    """Build the module's validated media storage over ``inner`` (deny-by-default ingestion).

    ``policy``/``scanner`` default to the reference upload policy and a pass-through scanner; a
    deployment injects tightened limits or a real malware scanner behind the same ports (LAW-12).
    """
    validator = UploadValidator(
        policy=policy or UploadPolicy(),
        scanner=scanner or PassThroughScanner(),
        audit=audit,
        clock=clock,
    )
    return ValidatingMediaStorage(storage=ValidatingObjectStorage(inner=inner, validator=validator))


__all__ = ["ValidatingMediaStorage", "build_media_storage"]
