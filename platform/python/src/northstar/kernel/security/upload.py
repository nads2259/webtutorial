"""Pure deny-by-default upload/media validation policy (LAW-02/08, rule 50, EVAL-SEC-004).

Stdlib-only and infrastructure-free: this module is the single authoritative *decision* for whether
an ingested file may be accepted. It never touches a database, socket, filesystem or provider SDK —
it inspects the raw ``bytes`` a caller already holds and either returns a :class:`ValidatedUpload`
or raises :class:`UploadRejected`. The policy is **deny-by-default** (docs/08 §5, "Quarantine
uploads before publication; verify MIME by content, filename, dimensions, archive depth and size;
sanitize SVG or convert to safe raster"):

* **content-based MIME sniffing** — the declared type/extension is never trusted; the type is
  sniffed from the leading magic bytes (a small stdlib table) and an upload is refused when the
  sniffed type does not match the declared type/extension or is not on the accepted allowlist;
* **size + decompression-bomb caps** — a single-upload byte cap, and for archives a per-entry size
  cap, a total-expanded-size cap, an expansion-ratio cap, an entry-count cap and a nesting-depth
  cap, all measured by *bounded* decompression so a zip/gzip bomb is refused without exhausting
  memory;
* **active-content defense** — an SVG/HTML upload carrying a ``<script>``, an ``on*`` event handler,
  ``javascript:``, a ``foreignObject``, an external/entity reference or an ``<iframe>``/``<object>``
  is refused (a benign markup file is accepted).

The quarantine/scan seam is the :class:`ScanPort` (a byte-in / verdict-out abstraction); the
orchestrating adapter runs the scan *after* this pure policy passes and before acceptance, so a
flagged artifact is refused. Implementations of the scanner live behind the port (LAW-12).
"""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..errors import Diagnostic, KernelError


class UploadReason(StrEnum):
    """Why an upload was refused (stable, machine-comparable, non-secret)."""

    EMPTY = "empty"
    OVERSIZE = "oversize"
    TYPE_NOT_ALLOWED = "type_not_allowed"
    EXTENSION_MISMATCH = "extension_mismatch"
    MIME_MISMATCH = "mime_mismatch"
    ACTIVE_CONTENT = "active_content"
    ARCHIVE_ENTRY_OVERSIZE = "archive_entry_oversize"
    ARCHIVE_TOTAL_OVERSIZE = "archive_total_oversize"
    ARCHIVE_RATIO_EXCEEDED = "archive_ratio_exceeded"
    ARCHIVE_TOO_MANY_ENTRIES = "archive_too_many_entries"
    ARCHIVE_TOO_DEEP = "archive_too_deep"
    MALFORMED_ARCHIVE = "malformed_archive"
    SCAN_FLAGGED = "scan_flagged"


class UploadRejected(KernelError):  # noqa: N818 canonical error name
    """An ingested file was refused by the deny-by-default upload policy (EVAL-SEC-004).

    Carries the stable :class:`UploadReason`, the (non-secret) ``declared_content_type`` and
    ``filename`` so the trust boundary maps it to a typed RFC 9457 problem and the audit trail
    records a tamper-evident ``upload.rejected`` decision. The ``detail`` is the reason code only —
    never the raw bytes, a decompressed payload or attacker-controlled content.
    """

    def __init__(
        self, *, reason: UploadReason, declared_content_type: str, filename: str | None = None
    ) -> None:
        self.reason = reason
        self.declared_content_type = declared_content_type
        self.filename = filename
        diag = Diagnostic(
            code="upload.rejected",
            message=f"upload refused ({reason.value})",
            detail=reason.value,
        )
        super().__init__(f"upload rejected: {reason.value}", (diag,))


# ---------------------------------------------------------------------------
# Content-based MIME sniffing (a small stdlib magic-byte table — no heavy dep)
# ---------------------------------------------------------------------------

# Ordered leading-byte signatures. The first match wins; longer/more-specific signatures precede
# shorter ones so an exact container is not shadowed by a generic prefix.
_MAGIC_SIGNATURES: tuple[tuple[str, bytes], ...] = (
    ("image/png", b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", b"\xff\xd8\xff"),
    ("image/gif", b"GIF87a"),
    ("image/gif", b"GIF89a"),
    ("application/pdf", b"%PDF-"),
    ("application/gzip", b"\x1f\x8b"),
    ("application/zip", b"PK\x03\x04"),
    ("application/zip", b"PK\x05\x06"),  # empty archive
    ("application/zip", b"PK\x07\x08"),  # spanned archive
)

_SVG_ROOT = re.compile(rb"<svg[\s>]", re.IGNORECASE)
_HTML_ROOT = re.compile(rb"<(?:!doctype\s+html|html|head|body)[\s>]", re.IGNORECASE)


def _is_probably_text(data: bytes) -> bool:
    """Return ``True`` when ``data`` decodes as UTF-8 without NUL/most control bytes (heuristic)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    for ch in text:
        code = ord(ch)
        if code == 0 or (code < 32 and ch not in "\t\n\r\f\v"):
            return False
    return True


def sniff_content_type(data: bytes) -> str | None:
    """Return the content type sniffed from ``data``'s bytes, or ``None`` if unrecognized.

    Binary formats are recognized by their magic bytes; markup/text formats have no reliable magic,
    so they are recognized structurally (an ``<svg>`` root, an HTML root element, otherwise plain
    UTF-8 text). The declared type/extension is never consulted here — that comparison is the
    caller's deny-by-default check.
    """
    for media_type, signature in _MAGIC_SIGNATURES:
        if data.startswith(signature):
            return media_type
    head = data[:4096].lstrip()
    if _SVG_ROOT.search(head):
        return "image/svg+xml"
    if _HTML_ROOT.search(head):
        return "text/html"
    if _is_probably_text(data):
        return "text/plain"
    return None


# ---------------------------------------------------------------------------
# Active-content (SVG / HTML) detection
# ---------------------------------------------------------------------------

_ACTIVE_MARKUP_PATTERNS: tuple[re.Pattern[bytes], ...] = (
    re.compile(rb"<\s*script", re.IGNORECASE),
    re.compile(rb"<\s*foreignobject", re.IGNORECASE),
    re.compile(rb"<\s*iframe", re.IGNORECASE),
    re.compile(rb"<\s*embed", re.IGNORECASE),
    re.compile(rb"<\s*object", re.IGNORECASE),
    re.compile(rb"<\s*use[^>]*\bhref", re.IGNORECASE),
    re.compile(rb"\bon[a-z]+\s*=", re.IGNORECASE),  # onload=, onerror=, onclick= ...
    re.compile(rb"javascript:", re.IGNORECASE),
    re.compile(rb"xlink:href\s*=\s*['\"]?\s*(?:https?:|//|javascript:)", re.IGNORECASE),
    re.compile(rb"<!doctype[^>]*(?:system|entity)", re.IGNORECASE),  # XXE / external doctype
    re.compile(rb"<!entity", re.IGNORECASE),  # XML entity expansion / SSRF
)


def contains_active_markup(data: bytes) -> bool:
    """Return ``True`` when SVG/HTML ``data`` carries active or external-reference content.

    Deny-by-default: any script element, ``on*`` event handler, ``javascript:`` URI,
    ``foreignObject``/``iframe``/``embed``/``object`` element, external ``use``/``xlink:href``
    target or entity/external doctype (XXE) marks the markup as unsafe to accept verbatim.
    """
    return any(pattern.search(data) for pattern in _ACTIVE_MARKUP_PATTERNS)


# ---------------------------------------------------------------------------
# Accepted-type allowlist + limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcceptedType:
    """One allowlisted upload type: its canonical media type, extensions and handling flags."""

    media_type: str
    extensions: frozenset[str]
    is_archive: bool = False
    is_active_markup: bool = False


DEFAULT_ACCEPTED_TYPES: tuple[AcceptedType, ...] = (
    AcceptedType("image/png", frozenset({".png"})),
    AcceptedType("image/jpeg", frozenset({".jpg", ".jpeg"})),
    AcceptedType("image/gif", frozenset({".gif"})),
    AcceptedType("application/pdf", frozenset({".pdf"})),
    AcceptedType("text/plain", frozenset({".txt", ".md", ".csv"})),
    AcceptedType("image/svg+xml", frozenset({".svg"}), is_active_markup=True),
    AcceptedType("text/html", frozenset({".html", ".htm"}), is_active_markup=True),
    AcceptedType("application/zip", frozenset({".zip"}), is_archive=True),
    AcceptedType("application/gzip", frozenset({".gz", ".gzip"}), is_archive=True),
)

_ARCHIVE_MEDIA_TYPES: frozenset[str] = frozenset({"application/zip", "application/gzip"})


@dataclass(frozen=True, slots=True)
class UploadLimits:
    """Deny-by-default size + decompression-bomb caps (reference deployment defaults).

    The exact numbers are a deployment default; the spec mandates *content-verified, size- and
    archive-depth-bounded* ingestion (docs/08 §5), not fixed integers. An archive is refused when
    any single caps is exceeded, measured by bounded decompression (never trusting header sizes).
    """

    max_bytes: int = 25 * 1024 * 1024
    max_archive_entry_bytes: int = 25 * 1024 * 1024
    max_archive_total_bytes: int = 100 * 1024 * 1024
    max_expansion_ratio: int = 100
    max_archive_entries: int = 2048
    max_archive_depth: int = 2

    def __post_init__(self) -> None:
        for name in (
            "max_bytes",
            "max_archive_entry_bytes",
            "max_archive_total_bytes",
            "max_expansion_ratio",
            "max_archive_entries",
            "max_archive_depth",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"upload limit {name!r} must be >= 1")


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    """A file that passed the deny-by-default upload policy (evidence for the storage seam)."""

    filename: str | None
    declared_content_type: str
    sniffed_content_type: str
    byte_size: int


# ---------------------------------------------------------------------------
# Quarantine / scan seam
# ---------------------------------------------------------------------------


class ScanOutcome(StrEnum):
    """The verdict of a malware/abuse scan of an upload's bytes."""

    CLEAN = "clean"
    FLAGGED = "flagged"


@dataclass(frozen=True, slots=True)
class ScanResult:
    """A scan verdict: ``CLEAN`` to admit, ``FLAGGED`` (with a non-secret signature) to refuse."""

    outcome: ScanOutcome
    signature: str | None = None

    @property
    def flagged(self) -> bool:
        return self.outcome is ScanOutcome.FLAGGED


@runtime_checkable
class ScanPort(Protocol):
    """The quarantine seam every upload passes before acceptance (LAW-12, EVAL-SEC-004).

    Implementations own the actual engine (a reference pass-through, a deterministic signature
    scanner, or a production AV/malware service) behind this boundary; the pure policy stays
    infrastructure-free and a deployment swaps the scanner without touching the kernel or module.
    """

    def scan(self, *, data: bytes, content_type: str) -> ScanResult: ...


# ---------------------------------------------------------------------------
# Bounded decompression helpers (zip/gzip bomb defense)
# ---------------------------------------------------------------------------


def _bounded_zip_entry_size(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> int:
    """Decompress one zip entry counting bytes, stopping once ``limit`` is exceeded.

    Never materializes more than ``limit + 1`` bytes in the accumulator, so a single entry that
    claims a small size but expands hugely is caught without exhausting memory.
    """
    total = 0
    with archive.open(info) as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                return total
    return total


def _iter_zip_entry_bytes(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    """Read up to ``limit`` bytes of a zip entry (for nested-archive sniffing only)."""
    with archive.open(info) as handle:
        return handle.read(limit)


def _inspect_zip(data: bytes, limits: UploadLimits, depth: int) -> UploadReason | None:
    """Return a blocking reason for a zip (bomb/nesting/oversize) or ``None`` if it is safe."""
    if depth > limits.max_archive_depth:
        return UploadReason.ARCHIVE_TOO_DEEP
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return UploadReason.MALFORMED_ARCHIVE
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > limits.max_archive_entries:
            return UploadReason.ARCHIVE_TOO_MANY_ENTRIES
        total_uncompressed = 0
        total_compressed = 0
        for info in infos:
            actual = _bounded_zip_entry_size(archive, info, limits.max_archive_entry_bytes)
            if actual > limits.max_archive_entry_bytes:
                return UploadReason.ARCHIVE_ENTRY_OVERSIZE
            total_uncompressed += actual
            total_compressed += max(info.compress_size, 0)
            if total_uncompressed > limits.max_archive_total_bytes:
                return UploadReason.ARCHIVE_TOTAL_OVERSIZE
            nested = _nested_archive_reason(
                _iter_zip_entry_bytes(archive, info, limits.max_archive_entry_bytes),
                limits,
                depth,
            )
            if nested is not None:
                return nested
        if _ratio_exceeded(total_uncompressed, total_compressed, limits.max_expansion_ratio):
            return UploadReason.ARCHIVE_RATIO_EXCEEDED
    return None


def _inspect_gzip(data: bytes, limits: UploadLimits, depth: int) -> UploadReason | None:
    """Return a blocking reason for a gzip stream (bomb/oversize/nesting) or ``None`` if safe.

    The stream is decompressed incrementally with a hard per-call output cap (``max_length``), so no
    more than ``cap + one chunk`` bytes are ever held: a gzip bomb trips the entry/total cap and is
    refused before its full expansion is materialized.
    """
    if depth > limits.max_archive_depth:
        return UploadReason.ARCHIVE_TOO_DEEP
    cap = limits.max_archive_entry_bytes
    decompressor = zlib.decompressobj(wbits=31)  # 31 => gzip header/footer handling
    total = 0
    head = bytearray()
    pending = data
    try:
        while pending or decompressor.unconsumed_tail:
            source = decompressor.unconsumed_tail or pending
            if source is pending:
                pending = b""
            out = decompressor.decompress(source, 65536)
            if not out and not decompressor.unconsumed_tail:
                break
            total += len(out)
            if len(head) < 4096:
                head.extend(out[: 4096 - len(head)])
            if total > cap:
                return UploadReason.ARCHIVE_ENTRY_OVERSIZE
            if total > limits.max_archive_total_bytes:
                return UploadReason.ARCHIVE_TOTAL_OVERSIZE
    except zlib.error:
        return UploadReason.MALFORMED_ARCHIVE
    if _ratio_exceeded(total, len(data), limits.max_expansion_ratio):
        return UploadReason.ARCHIVE_RATIO_EXCEEDED
    return _nested_archive_reason(bytes(head), limits, depth)


def _ratio_exceeded(uncompressed: int, compressed: int, max_ratio: int) -> bool:
    if compressed <= 0:
        return uncompressed > 0
    return uncompressed / compressed > max_ratio


def _nested_archive_reason(
    entry_head: bytes, limits: UploadLimits, depth: int
) -> UploadReason | None:
    """Recurse into a nested archive entry (depth-bounded); return a reason or ``None``."""
    sniffed = sniff_content_type(entry_head)
    if sniffed not in _ARCHIVE_MEDIA_TYPES:
        return None
    if depth + 1 > limits.max_archive_depth:
        return UploadReason.ARCHIVE_TOO_DEEP
    if sniffed == "application/zip":
        return _inspect_zip(entry_head, limits, depth + 1)
    return _inspect_gzip(entry_head, limits, depth + 1)


def inspect_archive(data: bytes, media_type: str, limits: UploadLimits) -> UploadReason | None:
    """Return a blocking :class:`UploadReason` for an archive, or ``None`` when within caps."""
    if media_type == "application/zip":
        return _inspect_zip(data, limits, depth=0)
    if media_type == "application/gzip":
        return _inspect_gzip(data, limits, depth=0)
    return None


# ---------------------------------------------------------------------------
# The pure policy
# ---------------------------------------------------------------------------


def _normalize_type(declared: str) -> str:
    return declared.split(";", 1)[0].strip().lower()


def _extension(filename: str | None) -> str | None:
    if not filename or "." not in filename:
        return None
    return "." + filename.rsplit(".", 1)[1].lower()


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    """Deny-by-default upload validation (pure, infrastructure-free — rule 50, EVAL-SEC-004).

    A file is accepted only when it clears **every** check in order: non-empty, within the byte
    cap, a declared type on the allowlist, an extension consistent with that type, a content-sniffed
    type matching the declared type, no active markup (for SVG/HTML), and — for archives — within
    the decompression-bomb caps. Any failure raises :class:`UploadRejected` with a stable reason.
    The quarantine scan is a separate seam (:class:`ScanPort`) the orchestrating adapter applies.
    """

    limits: UploadLimits = field(default_factory=UploadLimits)
    accepted: tuple[AcceptedType, ...] = DEFAULT_ACCEPTED_TYPES

    def _accepted_for(self, media_type: str) -> AcceptedType | None:
        for candidate in self.accepted:
            if candidate.media_type == media_type:
                return candidate
        return None

    def inspect(
        self, *, filename: str | None, declared_content_type: str, data: bytes
    ) -> ValidatedUpload:
        """Validate ``data`` deny-by-default; return :class:`ValidatedUpload` or raise."""
        declared = _normalize_type(declared_content_type)

        def _reject(reason: UploadReason) -> UploadRejected:
            return UploadRejected(reason=reason, declared_content_type=declared, filename=filename)

        if not data:
            raise _reject(UploadReason.EMPTY)
        if len(data) > self.limits.max_bytes:
            raise _reject(UploadReason.OVERSIZE)

        accepted = self._accepted_for(declared)
        if accepted is None:
            raise _reject(UploadReason.TYPE_NOT_ALLOWED)

        extension = _extension(filename)
        if extension is not None and extension not in accepted.extensions:
            raise _reject(UploadReason.EXTENSION_MISMATCH)

        sniffed = sniff_content_type(data)
        if sniffed != accepted.media_type:
            raise _reject(UploadReason.MIME_MISMATCH)

        if accepted.is_active_markup and contains_active_markup(data):
            raise _reject(UploadReason.ACTIVE_CONTENT)

        if accepted.is_archive:
            reason = inspect_archive(data, accepted.media_type, self.limits)
            if reason is not None:
                raise _reject(reason)

        return ValidatedUpload(
            filename=filename,
            declared_content_type=declared,
            sniffed_content_type=sniffed,
            byte_size=len(data),
        )


__all__ = [
    "DEFAULT_ACCEPTED_TYPES",
    "AcceptedType",
    "ScanOutcome",
    "ScanPort",
    "ScanResult",
    "UploadLimits",
    "UploadPolicy",
    "UploadReason",
    "UploadRejected",
    "ValidatedUpload",
    "contains_active_markup",
    "inspect_archive",
    "sniff_content_type",
]
