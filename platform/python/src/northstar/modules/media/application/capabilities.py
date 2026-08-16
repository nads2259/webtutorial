"""Media capabilities: one authoritative implementation per action (LAW-04).

Each handler runs through the kernel command/query bus, so every mutation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope is taken from the authenticated
:class:`RequestContext` (``tenant_scope``), **never** from the payload (rule 50). Ingestion
(:class:`UploadMedia`) writes ONLY through the :class:`MediaStoragePort` — the H02 validated
object-storage seam — so a mismatched/malicious asset is refused before any byte is stored
(EVAL-MED-001). Publication (:class:`PublishMedia`) enforces the hard accessibility gate
(EVAL-MED-002). Handlers depend only on :mod:`.ports` and the pure :mod:`..domain`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from northstar.kernel.context import Actor

from ..domain.errors import MediaInvariantViolation, MediaNotFound, TenantScopeMissing
from ..domain.model import (
    CaptionTrack,
    MediaAsset,
    MediaType,
    Transcript,
    summarize_accessibility,
)
from ..domain.time_selectors import Cue, TimeSelector, TranscriptSegment
from .ports import MediaRepositoryPort, MediaStoragePort

CAP_VERSION = "1.0.0"

CAP_UPLOAD = "media.asset.upload"
CAP_ATTACH_TRANSCRIPT = "media.transcript.attach"
CAP_ATTACH_CAPTIONS = "media.captions.attach"
CAP_ATTACH_ALT = "media.alt.attach"
CAP_PUBLISH = "media.asset.publish"
CAP_GET = "media.asset.get"
CAP_RESOLVE_TIME = "media.timeselector.resolve"

MEDIA_CAPABILITIES: tuple[str, ...] = (
    CAP_UPLOAD,
    CAP_ATTACH_TRANSCRIPT,
    CAP_ATTACH_CAPTIONS,
    CAP_ATTACH_ALT,
    CAP_PUBLISH,
    CAP_GET,
    CAP_RESOLVE_TIME,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UploadMediaCommand:
    media_type: str
    filename: str
    declared_content_type: str
    data: bytes
    title: str | None = None


@dataclass(frozen=True, slots=True)
class UploadMediaResult:
    asset_id: str
    media_type: str
    content_type: str
    blob_ref: str
    byte_size: int
    state: str


@dataclass(frozen=True, slots=True)
class AttachTranscriptCommand:
    asset_id: str
    language: str
    segments: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class AttachCaptionsCommand:
    asset_id: str
    language: str
    cues: tuple[dict[str, Any], ...]
    label: str | None = None


@dataclass(frozen=True, slots=True)
class AttachAltTextCommand:
    asset_id: str
    text: str | None = None
    decorative: bool = False


@dataclass(frozen=True, slots=True)
class AssetMutationResult:
    asset_id: str
    state: str
    missing_accessibility: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublishMediaCommand:
    asset_id: str


@dataclass(frozen=True, slots=True)
class PublishMediaResult:
    asset_id: str
    state: str


@dataclass(frozen=True, slots=True)
class GetMediaQuery:
    asset_id: str


@dataclass(frozen=True, slots=True)
class MediaAssetView:
    asset_id: str
    media_type: str
    content_type: str
    blob_ref: str
    byte_size: int
    state: str
    title: str | None
    accessibility: dict[str, Any]
    time_fragments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolveTimeSelectorQuery:
    asset_id: str
    at: float


@dataclass(frozen=True, slots=True)
class ResolveTimeSelectorResult:
    asset_id: str
    resolution: dict[str, Any]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    scope = getattr(context, "tenant_scope", None)
    if not scope:
        raise TenantScopeMissing()
    return scope


def _actor(invocation: object) -> Actor:
    context = getattr(invocation, "context", None)
    return context.actor


def _correlation(invocation: object) -> str | None:
    context = getattr(invocation, "context", None)
    return getattr(context, "correlation_id", None)


def _media_type(value: str) -> MediaType:
    try:
        return MediaType(value)
    except ValueError as exc:
        raise MediaInvariantViolation(
            f"invalid media type {value!r}", code="media.type.invalid"
        ) from exc


def _load(repo: MediaRepositoryPort, *, organization_id: str, asset_id: str) -> MediaAsset:
    asset = repo.get(organization_id=organization_id, asset_id=asset_id)
    if asset is None:
        raise MediaNotFound()
    return asset


def _segments(items: tuple[dict[str, Any], ...]) -> tuple[TranscriptSegment, ...]:
    return tuple(TranscriptSegment.from_dict(item) for item in items)


def _cues(items: tuple[dict[str, Any], ...]) -> tuple[Cue, ...]:
    return tuple(Cue.from_dict(item) for item in items)


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class UploadMedia:
    """``media.asset.upload`` — ingest an asset ONLY through the validated storage seam.

    The bytes are validated + stored by the :class:`MediaStoragePort` (delegating to the H02
    ``ValidatingObjectStorage``); a mismatched/malicious asset raises ``UploadRejected`` and no
    ``MediaAsset`` row is created (no unvalidated write path, EVAL-MED-001).
    """

    def __init__(
        self,
        *,
        repository: MediaRepositoryPort,
        storage: MediaStoragePort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._storage = storage
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> UploadMediaResult:
        command = _typed(request, UploadMediaCommand)
        organization_id = _tenant(request)
        media_type = _media_type(command.media_type)
        asset_id = self._id_factory()
        # Tenant-prefixed, asset-scoped storage key (opaque; never derived from the payload tenant).
        key = f"{organization_id}/media/{asset_id}/{command.filename}"
        stored = self._storage.store(
            key=key,
            data=command.data,
            declared_content_type=command.declared_content_type,
            actor=_actor(request),
            correlation_id=_correlation(request),
        )
        asset = MediaAsset(
            asset_id=asset_id,
            organization_id=organization_id,
            media_type=media_type,
            content_type=stored.content_type,
            blob_ref=stored.key,
            byte_size=stored.byte_size,
            created_by=_actor(request),
            created_at=self._clock(),
            title=command.title,
        )
        self._repo.add(asset)
        return UploadMediaResult(
            asset_id=asset.asset_id,
            media_type=asset.media_type.value,
            content_type=asset.content_type,
            blob_ref=asset.blob_ref,
            byte_size=asset.byte_size,
            state=asset.state.value,
        )


class AttachTranscript:
    """``media.transcript.attach`` — attach a timecode-addressable transcript (time-based media)."""

    def __init__(self, *, repository: MediaRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> AssetMutationResult:
        command = _typed(request, AttachTranscriptCommand)
        organization_id = _tenant(request)
        asset = _load(self._repo, organization_id=organization_id, asset_id=command.asset_id)
        transcript = Transcript(language=command.language, segments=_segments(command.segments))
        updated = asset.with_transcript(transcript)
        self._repo.update(updated)
        return _mutation_result(updated)


class AttachCaptions:
    """``media.captions.attach`` — append a timecode-addressable caption track (time-based only)."""

    def __init__(self, *, repository: MediaRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> AssetMutationResult:
        command = _typed(request, AttachCaptionsCommand)
        organization_id = _tenant(request)
        asset = _load(self._repo, organization_id=organization_id, asset_id=command.asset_id)
        track = CaptionTrack(
            language=command.language, cues=_cues(command.cues), label=command.label
        )
        updated = asset.with_caption_track(track)
        self._repo.update(updated)
        return _mutation_result(updated)


class AttachAltText:
    """``media.alt.attach`` — set alt text or the decorative flag on an image (deny-by-default)."""

    def __init__(self, *, repository: MediaRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> AssetMutationResult:
        command = _typed(request, AttachAltTextCommand)
        organization_id = _tenant(request)
        asset = _load(self._repo, organization_id=organization_id, asset_id=command.asset_id)
        updated = asset.with_alt_text(text=command.text, decorative=command.decorative)
        self._repo.update(updated)
        return _mutation_result(updated)


class PublishMedia:
    """``media.asset.publish`` — publish ONLY after the accessibility gate passes (EVAL-MED-002).

    A video/audio asset without a transcript AND captions, or an image without alt text/decorative,
    is REJECTED with a typed :class:`AccessibilityRequirementNotMet` (NFR-A11Y-003).
    """

    def __init__(self, *, repository: MediaRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> PublishMediaResult:
        command = _typed(request, PublishMediaCommand)
        organization_id = _tenant(request)
        asset = _load(self._repo, organization_id=organization_id, asset_id=command.asset_id)
        published = asset.publish()  # raises AccessibilityRequirementNotMet if a gate is unmet
        self._repo.update(published)
        return PublishMediaResult(asset_id=published.asset_id, state=published.state.value)


class GetMediaAsset:
    """``media.asset.get`` (query) — return an asset view incl. accessibility + time-selectors."""

    def __init__(self, *, repository: MediaRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> MediaAssetView:
        query = _typed(request, GetMediaQuery)
        organization_id = _tenant(request)
        asset = _load(self._repo, organization_id=organization_id, asset_id=query.asset_id)
        return MediaAssetView(
            asset_id=asset.asset_id,
            media_type=asset.media_type.value,
            content_type=asset.content_type,
            blob_ref=asset.blob_ref,
            byte_size=asset.byte_size,
            state=asset.state.value,
            title=asset.title,
            accessibility=summarize_accessibility(asset),
            time_fragments=tuple(sel.fragment for sel in asset.time_selectors()),
        )


class ResolveTimeSelector:
    """``media.timeselector.resolve`` (query) — resolve cues/segment at a timecode (FR-CNT-010)."""

    def __init__(self, *, repository: MediaRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ResolveTimeSelectorResult:
        query = _typed(request, ResolveTimeSelectorQuery)
        organization_id = _tenant(request)
        asset = _load(self._repo, organization_id=organization_id, asset_id=query.asset_id)
        # Validate the requested timecode is well-formed via the pure selector value object.
        TimeSelector(start=query.at)
        return ResolveTimeSelectorResult(
            asset_id=asset.asset_id, resolution=asset.resolve_at(query.at)
        )


def _mutation_result(asset: MediaAsset) -> AssetMutationResult:
    return AssetMutationResult(
        asset_id=asset.asset_id,
        state=asset.state.value,
        missing_accessibility=asset.missing_accessibility(),
    )
