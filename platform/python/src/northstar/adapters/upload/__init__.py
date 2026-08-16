"""Reference upload-security adapters (behind the kernel upload policy + ScanPort, EVAL-SEC-004).

The kernel declares *what* an ingested file must satisfy (the pure
:class:`~northstar.kernel.security.upload.UploadPolicy`) and the quarantine seam
(:class:`~northstar.kernel.security.upload.ScanPort`). This package supplies the concrete,
dependency-light adapters a deployment wires in:

* :class:`PassThroughScanner` — a reference scanner that admits everything (the production seam is
  an adapter swap for a real AV/malware engine);
* :class:`SignatureScanner` — a deterministic signature scanner (default: the EICAR test marker) so
  a flagged artifact is provably refused in tests without a network dependency;
* :class:`UploadValidator` — orchestrates the pure policy + the scan and audits every rejection;
* :class:`ValidatingObjectStorage` — a drop-in object-storage decorator whose ``put`` validates the
  bytes first, so no unvalidated write path to media storage remains.
"""

from __future__ import annotations

from .problem import upload_rejected_problem
from .scanner import PassThroughScanner, SignatureScanner
from .storage import ObjectStoreLike, ValidatingObjectStorage
from .validator import UPLOAD_DECISION_ACTION, UPLOAD_DECISION_EVENT, UploadValidator

__all__ = [
    "UPLOAD_DECISION_ACTION",
    "UPLOAD_DECISION_EVENT",
    "ObjectStoreLike",
    "PassThroughScanner",
    "SignatureScanner",
    "UploadValidator",
    "ValidatingObjectStorage",
    "upload_rejected_problem",
]
