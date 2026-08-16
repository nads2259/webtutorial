"""Map a typed :class:`UploadRejected` to an RFC 9457 problem document (rule 40/50, docs/05 §6).

Rejections surface as ``422 validation.failed`` — a refused upload is an invalid input at the
ingestion trust boundary. The stable reason code travels in ``violations`` (``upload.rejected``
with the machine-comparable reason as ``field``); the caller-safe ``detail`` never echoes the raw
bytes, a decompressed payload or attacker-controlled content.
"""

from __future__ import annotations

import uuid

from northstar.adapters.http_fastapi.problem_details import ProblemDetail
from northstar.kernel.security.upload import UploadRejected


def upload_rejected_problem(
    err: UploadRejected, *, correlation_id: str | None = None
) -> ProblemDetail:
    """Return the RFC 9457 ``422 validation.failed`` problem for a refused upload."""
    diagnostic = err.diagnostics[0]
    return ProblemDetail(
        status=422,
        code="validation.failed",
        title="The uploaded file was refused",
        detail="The uploaded file failed content, size, archive or malware validation.",
        retryable=False,
        trace_id=f"trc_{uuid.uuid4().hex}",
        correlation_id=correlation_id,
        violations=(
            {
                "code": diagnostic.code,
                "message": diagnostic.message,
                "field": err.reason.value,
            },
        ),
    )


__all__ = ["upload_rejected_problem"]
