"""Reference caption/transcript generator seam (behind CaptionGeneratorPort, LAW-12).

Transcoding/captioning is an infrastructure concern kept behind a port so the domain and
capabilities never depend on an ASR/transcoding SDK. :class:`ReferenceCaptionGenerator` is a
deterministic, dependency-light seam: it emits a single-segment transcript and a single-cue caption
track derived from a fixed reference phrase, which is enough to prove the wiring and time-selector
addressing offline. A production build swaps in a real transcription engine behind the same port —
no domain or capability change (LAW-12).
"""

from __future__ import annotations

from ..application.ports import GeneratedAlternatives
from ..domain.model import CaptionTrack, Transcript
from ..domain.time_selectors import Cue, TimeSelector, TranscriptSegment

_REFERENCE_PHRASE = "Reference generated caption."


class ReferenceCaptionGenerator:
    """A deterministic reference transcription/captioning seam (``CaptionGeneratorPort``)."""

    def __init__(self, *, phrase: str = _REFERENCE_PHRASE) -> None:
        self._phrase = phrase

    def generate(self, *, blob_ref: str, language: str) -> GeneratedAlternatives:
        selector = TimeSelector(start=0.0, end=5.0)
        transcript = Transcript(
            language=language,
            segments=(TranscriptSegment(time=selector, text=self._phrase),),
        )
        captions = CaptionTrack(
            language=language,
            cues=(Cue(time=selector, text=self._phrase),),
            label=f"{language} (generated)",
        )
        return GeneratedAlternatives(transcript=transcript, captions=captions)


__all__ = ["ReferenceCaptionGenerator"]
