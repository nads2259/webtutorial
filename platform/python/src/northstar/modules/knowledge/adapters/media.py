"""The knowledge module's authoritative, validated media-ingestion seam (FR-CNT-009, EVAL-SEC-004).

The knowledge module stores media behind an ``ObjectStoragePort`` (docs/06, FR-CNT-009). This
adapter is the ONLY object storage the module hands to its ingestion path: it wraps a reference
object store in the shared, deny-by-default
:class:`~northstar.adapters.upload.ValidatingObjectStorage`, so every ingested byte passes
content-based MIME sniffing, the size + decompression-bomb caps, SVG/HTML active-content refusal
and the quarantine scan before it is ever written — no unvalidated write path remains
(NFR-SEC-004). A rejected upload raises the typed
:class:`~northstar.kernel.security.upload.UploadRejected` and is audited (LAW-14); the reference
scanner is a pass-through seam that a real AV/malware engine replaces without any module change.
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
from northstar.kernel.security.upload import ScanPort, UploadPolicy


def build_knowledge_media_storage(
    *,
    inner: ObjectStoreLike,
    policy: UploadPolicy | None = None,
    scanner: ScanPort | None = None,
    audit: AuditRecorderPort | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ValidatingObjectStorage:
    """Build the module's validated media storage over ``inner`` (deny-by-default ingestion).

    ``policy``/``scanner`` default to the reference upload policy and a pass-through scanner; a
    deployment injects tightened limits or a real malware scanner behind the same ports.
    """
    validator = UploadValidator(
        policy=policy or UploadPolicy(),
        scanner=scanner or PassThroughScanner(),
        audit=audit,
        clock=clock,
    )
    return ValidatingObjectStorage(inner=inner, validator=validator)


__all__ = ["build_knowledge_media_storage"]
