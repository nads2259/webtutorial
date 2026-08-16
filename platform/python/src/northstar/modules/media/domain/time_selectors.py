"""Addressable media time-selectors and timed text (FR-CNT-010, EVAL-MED-002).

Time-based media (video/audio) exposes its caption cues and transcript segments as timecode-
addressable :class:`TimeSelector` fragments so an annotation or citation can point at an exact
moment/interval (docs/12 §"Addressable block UX"). A :class:`TimeSelector` projects to the same
``MediaFragmentSelector`` shape the annotation module already uses (``t=start,end``) and aligns with
the ``media_time`` selector capability of ``content-block.schema.json`` — the selector concept is
reused, not duplicated. Pure and stdlib-only (rule 10).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import MediaInvariantViolation

# The content-block / annotation selector capability name for time-based media (LAW-06).
MEDIA_TIME_CAPABILITY = "media_time"


def _format_seconds(value: float) -> str:
    """Render a timecode compactly (integers without a trailing ``.0``)."""
    if value == int(value):
        return str(int(value))
    return repr(round(value, 3))


@dataclass(frozen=True, slots=True)
class TimeSelector:
    """A timecode point or ``[start, end)`` interval addressable via a media fragment.

    ``start``/``end`` are seconds from the media origin. ``end`` is optional (a point cue);
    when present it must be strictly greater than ``start``. Deny-by-default validation refuses a
    negative or inverted interval so an addressable cue is always well-formed.
    """

    start: float
    end: float | None = None

    def __post_init__(self) -> None:
        if self.start < 0:
            raise MediaInvariantViolation(
                "TimeSelector.start must be >= 0", code="media.time_selector.start"
            )
        if self.end is not None and self.end <= self.start:
            raise MediaInvariantViolation(
                "TimeSelector.end must be greater than start",
                code="media.time_selector.end",
            )

    @property
    def fragment(self) -> str:
        """The W3C media-fragment value (``t=start`` or ``t=start,end``)."""
        if self.end is None:
            return f"t={_format_seconds(self.start)}"
        return f"t={_format_seconds(self.start)},{_format_seconds(self.end)}"

    def contains(self, at: float) -> bool:
        """Return whether timecode ``at`` (seconds) falls within this selector."""
        if self.end is None:
            return at == self.start
        return self.start <= at < self.end

    def to_media_fragment(self) -> dict[str, Any]:
        """Project to the annotation ``MediaFragmentSelector`` object shape (reused, not new)."""
        return {"type": "MediaFragmentSelector", "value": self.fragment}

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end}

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> TimeSelector:
        start = raw.get("start")
        end = raw.get("end")
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            raise MediaInvariantViolation(
                "TimeSelector.start must be a number", code="media.time_selector.start"
            )
        if end is not None and (isinstance(end, bool) or not isinstance(end, (int, float))):
            raise MediaInvariantViolation(
                "TimeSelector.end must be a number or null", code="media.time_selector.end"
            )
        return TimeSelector(start=float(start), end=None if end is None else float(end))


@dataclass(frozen=True, slots=True)
class Cue:
    """One caption cue: a timecode-addressable line of caption text."""

    time: TimeSelector
    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise MediaInvariantViolation("a caption cue requires text", code="media.cue.text")

    def to_dict(self) -> dict[str, Any]:
        return {"time": self.time.to_dict(), "text": self.text}

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> Cue:
        time = raw.get("time")
        if not isinstance(time, Mapping):
            raise MediaInvariantViolation("a caption cue requires a time", code="media.cue.time")
        text = raw.get("text")
        if not isinstance(text, str):
            raise MediaInvariantViolation("a caption cue requires text", code="media.cue.text")
        return Cue(time=TimeSelector.from_dict(time), text=text)


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One transcript segment: a timecode-addressable passage, optionally with a speaker."""

    time: TimeSelector
    text: str
    speaker: str | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise MediaInvariantViolation(
                "a transcript segment requires text", code="media.segment.text"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"time": self.time.to_dict(), "text": self.text, "speaker": self.speaker}

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> TranscriptSegment:
        time = raw.get("time")
        if not isinstance(time, Mapping):
            raise MediaInvariantViolation(
                "a transcript segment requires a time", code="media.segment.time"
            )
        text = raw.get("text")
        if not isinstance(text, str):
            raise MediaInvariantViolation(
                "a transcript segment requires text", code="media.segment.text"
            )
        speaker = raw.get("speaker")
        if speaker is not None and not isinstance(speaker, str):
            raise MediaInvariantViolation(
                "a transcript segment speaker must be a string or null",
                code="media.segment.speaker",
            )
        return TranscriptSegment(time=TimeSelector.from_dict(time), text=text, speaker=speaker)
