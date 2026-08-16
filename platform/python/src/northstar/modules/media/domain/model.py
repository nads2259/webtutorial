"""Media asset aggregate + accessible alternatives + the publish accessibility gate.

A :class:`MediaAsset` carries its type (video/audio/image), the *validated* content type and stored
blob reference (evidence the bytes passed the H02 upload validator), plus accessible alternatives:
a :class:`Transcript` and :class:`CaptionTrack`\\s for time-based media, alt text or a decorative
flag for images. The publish invariant (:meth:`MediaAsset.publish`) is the hard accessibility gate
(EVAL-MED-002, NFR-A11Y-003): a video/audio asset MUST have a transcript AND captions, an image
MUST have alt text OR the decorative flag, otherwise publication raises
:class:`~northstar.modules.media.domain.errors.AccessibilityRequirementNotMet`. Time-based media
exposes timecode-addressable selectors (FR-CNT-010). Pure and infrastructure-free (rule 10, LAW-02).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from northstar.kernel.context import Actor

from .errors import (
    AccessibilityRequirementNotMet,
    MediaInvariantViolation,
    MediaStateError,
)
from .time_selectors import Cue, TimeSelector, TranscriptSegment

RES_MEDIA = "media.asset"

# Accessible-alternative identifiers used by the gate + audit/problem diagnostics.
ALT_TRANSCRIPT = "transcript"
ALT_CAPTIONS = "captions"
ALT_ALT_TEXT = "alt_text"


class MediaType(StrEnum):
    """The kind of media asset (FR-CNT-009/010)."""

    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"


class MediaState(StrEnum):
    """Media asset lifecycle state."""

    DRAFT = "draft"
    PUBLISHED = "published"


# Time-based media (video/audio) both require a transcript AND captions, and expose time-selectors.
TIME_BASED_TYPES: frozenset[MediaType] = frozenset({MediaType.VIDEO, MediaType.AUDIO})


@dataclass(frozen=True, slots=True)
class Transcript:
    """A full transcript for time-based media: ordered, timecode-addressable segments."""

    language: str
    segments: tuple[TranscriptSegment, ...]

    def __post_init__(self) -> None:
        if not self.language.strip():
            raise MediaInvariantViolation(
                "a transcript requires a language", code="media.transcript.language"
            )
        if not self.segments:
            raise MediaInvariantViolation(
                "a transcript requires at least one segment", code="media.transcript.segments"
            )

    def segment_at(self, at: float) -> TranscriptSegment | None:
        """Return the transcript segment addressable at timecode ``at`` (seconds), if any."""
        for segment in self.segments:
            if segment.time.contains(at):
                return segment
        return None

    def selectors(self) -> tuple[TimeSelector, ...]:
        return tuple(segment.time for segment in self.segments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "segments": [segment.to_dict() for segment in self.segments],
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Transcript:
        return Transcript(
            language=str(raw["language"]),
            segments=tuple(TranscriptSegment.from_dict(item) for item in raw.get("segments", ())),
        )


@dataclass(frozen=True, slots=True)
class CaptionTrack:
    """A caption track in one language: ordered, timecode-addressable cues (WCAG 2.2 AA)."""

    language: str
    cues: tuple[Cue, ...]
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.language.strip():
            raise MediaInvariantViolation(
                "a caption track requires a language", code="media.captions.language"
            )
        if not self.cues:
            raise MediaInvariantViolation(
                "a caption track requires at least one cue", code="media.captions.cues"
            )

    def cue_at(self, at: float) -> Cue | None:
        """Return the caption cue addressable at timecode ``at`` (seconds), if any."""
        for cue in self.cues:
            if cue.time.contains(at):
                return cue
        return None

    def selectors(self) -> tuple[TimeSelector, ...]:
        return tuple(cue.time for cue in self.cues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "label": self.label,
            "cues": [cue.to_dict() for cue in self.cues],
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> CaptionTrack:
        return CaptionTrack(
            language=str(raw["language"]),
            label=raw.get("label"),
            cues=tuple(Cue.from_dict(item) for item in raw.get("cues", ())),
        )


@dataclass(frozen=True, slots=True)
class MediaAsset:
    """A validated media asset with accessible alternatives (the aggregate root)."""

    asset_id: str
    organization_id: str
    media_type: MediaType
    content_type: str
    blob_ref: str
    byte_size: int
    created_by: Actor
    created_at: datetime
    state: MediaState = MediaState.DRAFT
    title: str | None = None
    transcript: Transcript | None = None
    captions: tuple[CaptionTrack, ...] = ()
    alt_text: str | None = None
    decorative: bool = False
    duration_seconds: float | None = None
    policy_decision_id: str | None = None

    def __post_init__(self) -> None:
        if not self.blob_ref:
            raise MediaInvariantViolation(
                "a media asset requires a stored blob reference", code="media.blob_ref"
            )
        if self.byte_size < 1:
            raise MediaInvariantViolation(
                "a media asset requires validated bytes", code="media.byte_size"
            )

    @property
    def is_time_based(self) -> bool:
        return self.media_type in TIME_BASED_TYPES

    def _require_draft(self) -> None:
        if self.state is not MediaState.DRAFT:
            raise MediaStateError("accessible alternatives can only be attached to a draft asset")

    def _require_time_based(self, alternative: str) -> None:
        if not self.is_time_based:
            raise MediaInvariantViolation(
                f"{alternative} apply only to time-based (video/audio) media",
                code="media.alternative.type_mismatch",
            )

    def with_transcript(self, transcript: Transcript) -> MediaAsset:
        """Attach/replace the transcript (time-based media only, draft only)."""
        self._require_draft()
        self._require_time_based("transcripts")
        return replace(self, transcript=transcript)

    def with_caption_track(self, track: CaptionTrack) -> MediaAsset:
        """Append a caption track (time-based media only, draft only)."""
        self._require_draft()
        self._require_time_based("captions")
        return replace(self, captions=(*self.captions, track))

    def with_alt_text(self, *, text: str | None, decorative: bool = False) -> MediaAsset:
        """Set alt text or mark the image decorative (images only, draft only).

        Deny-by-default: a non-decorative image must carry non-empty alt text; a decorative image
        carries no alt text (WCAG 2.2 AA — a decorative image is hidden from assistive tech).
        """
        self._require_draft()
        if self.media_type is not MediaType.IMAGE:
            raise MediaInvariantViolation(
                "alt text applies only to image media", code="media.alternative.type_mismatch"
            )
        if decorative and text:
            raise MediaInvariantViolation(
                "a decorative image must not also carry alt text",
                code="media.alt_text.decorative_conflict",
            )
        if not decorative and not (text and text.strip()):
            raise MediaInvariantViolation(
                "a non-decorative image requires non-empty alt text",
                code="media.alt_text.empty",
            )
        return replace(self, alt_text=text, decorative=decorative)

    def missing_accessibility(self) -> tuple[str, ...]:
        """Return the accessible alternatives this asset's type requires but lacks (the gate).

        Video/audio require a transcript AND at least one caption track; an image requires alt text
        OR the decorative flag. An empty tuple means the asset satisfies the accessibility gate.
        """
        missing: list[str] = []
        if self.is_time_based:
            if self.transcript is None:
                missing.append(ALT_TRANSCRIPT)
            if not self.captions:
                missing.append(ALT_CAPTIONS)
        elif self.media_type is MediaType.IMAGE:
            if not self.decorative and not (self.alt_text and self.alt_text.strip()):
                missing.append(ALT_ALT_TEXT)
        return tuple(missing)

    def assert_publishable(self) -> None:
        """Raise :class:`AccessibilityRequirementNotMet` when a required alternative is missing."""
        missing = self.missing_accessibility()
        if missing:
            raise AccessibilityRequirementNotMet(media_type=self.media_type.value, missing=missing)

    def publish(self) -> MediaAsset:
        """Return a PUBLISHED copy after enforcing the accessibility gate (EVAL-MED-002)."""
        if self.state is MediaState.PUBLISHED:
            return self
        self.assert_publishable()
        return replace(self, state=MediaState.PUBLISHED)

    def time_selectors(self) -> tuple[TimeSelector, ...]:
        """All addressable time-selectors (caption cues + transcript segments), FR-CNT-010."""
        selectors: list[TimeSelector] = []
        for track in self.captions:
            selectors.extend(track.selectors())
        if self.transcript is not None:
            selectors.extend(self.transcript.selectors())
        return tuple(selectors)

    def resolve_at(self, at: float) -> dict[str, Any]:
        """Resolve the caption cues + transcript segment addressable at timecode ``at`` (seconds).

        Used by annotation/citation to anchor to an exact moment; images have no time-selectors.
        """
        cues = [
            {"language": track.language, **track.cue_at(at).to_dict()}
            for track in self.captions
            if track.cue_at(at) is not None
        ]
        segment = self.transcript.segment_at(at) if self.transcript is not None else None
        return {
            "at": at,
            "fragment": TimeSelector(start=at).fragment,
            "caption_cues": cues,
            "transcript_segment": segment.to_dict() if segment is not None else None,
        }


def summarize_accessibility(asset: MediaAsset) -> dict[str, Any]:
    """Project the accessibility state of an asset (for API views / studio surfaces)."""
    return {
        "media_type": asset.media_type.value,
        "has_transcript": asset.transcript is not None,
        "caption_tracks": len(asset.captions),
        "alt_text": asset.alt_text,
        "decorative": asset.decorative,
        "missing": list(asset.missing_accessibility()),
        "publishable": not asset.missing_accessibility(),
    }


def all_time_fragments(assets: Iterable[MediaAsset]) -> tuple[str, ...]:
    """Return every media-fragment string exposed across ``assets`` (helper for callers)."""
    fragments: list[str] = []
    for asset in assets:
        fragments.extend(selector.fragment for selector in asset.time_selectors())
    return tuple(fragments)


__all__ = [
    "ALT_ALT_TEXT",
    "ALT_CAPTIONS",
    "ALT_TRANSCRIPT",
    "RES_MEDIA",
    "TIME_BASED_TYPES",
    "CaptionTrack",
    "MediaAsset",
    "MediaState",
    "MediaType",
    "Transcript",
    "all_time_fragments",
    "summarize_accessibility",
]
