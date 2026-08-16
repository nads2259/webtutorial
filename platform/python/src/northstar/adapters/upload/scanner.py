"""Reference :class:`~northstar.kernel.security.upload.ScanPort` adapters (EVAL-SEC-004).

Both are dependency-light and deterministic so the quarantine seam is provable offline:

* :class:`PassThroughScanner` admits every artifact (the reference seam; a real AV/malware engine
  is a straight adapter swap behind the same port — the production build injects it);
* :class:`SignatureScanner` flags an artifact whose bytes contain any configured signature. Its
  default signature is the industry-standard EICAR anti-malware test string, which is safe to embed
  and lets a test prove that a flagged upload is refused before acceptance.
"""

from __future__ import annotations

from northstar.kernel.security.upload import ScanOutcome, ScanResult

# The EICAR standard anti-malware test file marker (harmless; used everywhere to exercise scanners).
EICAR_TEST_SIGNATURE = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class PassThroughScanner:
    """A reference scanner that admits every artifact (``ScanPort``)."""

    def scan(self, *, data: bytes, content_type: str) -> ScanResult:
        return ScanResult(outcome=ScanOutcome.CLEAN)


class SignatureScanner:
    """A deterministic byte-signature scanner (``ScanPort``): flag if any signature is present."""

    def __init__(self, signatures: tuple[bytes, ...] = (EICAR_TEST_SIGNATURE,)) -> None:
        self._signatures = signatures

    def scan(self, *, data: bytes, content_type: str) -> ScanResult:
        for signature in self._signatures:
            if signature in data:
                return ScanResult(outcome=ScanOutcome.FLAGGED, signature="signature.match")
        return ScanResult(outcome=ScanOutcome.CLEAN)


__all__ = ["EICAR_TEST_SIGNATURE", "PassThroughScanner", "SignatureScanner"]
