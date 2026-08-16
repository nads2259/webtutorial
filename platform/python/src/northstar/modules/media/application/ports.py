"""Ports (abstractions) for the media application layer (rule 10/20, DIP).

* :class:`MediaRepositoryPort` — tenant-scoped persistence; every read/write is scoped by
  ``organization_id`` so a caller can never reach another tenant's assets (rule 50).
* :class:`MediaStoragePort` — the ONLY media write seam. Its concrete adapter delegates to the H02
  :class:`~northstar.adapters.upload.ValidatingObjectStorage`, so every ingested byte passes the
  deny-by-default upload validator before storage (EVAL-MED-001; no unvalidated write path). It
  raises :class:`~northstar.kernel.security.upload.UploadRejected` on refusal.
* :class:`CaptionGeneratorPort` — an OPTIONAL transcription/captioning seam (reference/mock in the
  build; a real ASR engine is an adapter swap, LAW-12). Capabilities never depend on a provider SDK.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from northstar.kernel.context import Actor

from ..domain.model import CaptionTrack, MediaAsset, Transcript


@runtime_checkable
class MediaRepositoryPort(Protocol):
    """Persists and reads media assets, always tenant-scoped."""

    def add(self, asset: MediaAsset) -> None: ...

    def get(self, *, organization_id: str, asset_id: str) -> MediaAsset | None:
        """Return the asset only if it belongs to ``organization_id`` (else ``None``)."""
        ...

    def update(self, asset: MediaAsset) -> None:
        """Persist mutated alternatives/state for an existing asset (tenant-scoped)."""
        ...

    def list_for_org(self, *, organization_id: str) -> Sequence[MediaAsset]: ...


@dataclass(frozen=True, slots=True)
class StoredBlob:
    """Evidence that bytes passed the upload validator and were stored (validated content type)."""

    key: str
    content_type: str
    byte_size: int


@runtime_checkable
class MediaStoragePort(Protocol):
    """The single validated media-ingestion seam (delegates to ValidatingObjectStorage)."""

    def store(
        self,
        *,
        key: str,
        data: bytes,
        declared_content_type: str,
        actor: Actor | None = None,
        correlation_id: str | None = None,
    ) -> StoredBlob:
        """Validate ``data`` (deny-by-default) then store it; raise ``UploadRejected`` on refuse."""
        ...


@dataclass(frozen=True, slots=True)
class GeneratedAlternatives:
    """A generated transcript + caption track for time-based media (reference/mock seam)."""

    transcript: Transcript
    captions: CaptionTrack


@runtime_checkable
class CaptionGeneratorPort(Protocol):
    """Optional transcription/captioning seam (LAW-12); a real ASR engine swaps behind it."""

    def generate(self, *, blob_ref: str, language: str) -> GeneratedAlternatives: ...
