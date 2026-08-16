"""Pure citation verification — the anti-fabrication grounding rule (docs/10 §5, FR-AI-007).

A citation is trustworthy only when BOTH hold:

1. the cited ``chunk_id`` was actually in the set of passages retrieval returned for this
   authenticated actor (so it cannot reference another tenant's or a fabricated passage), and
2. the passage text SUPPORTS the claim — approximated deterministically by requiring the claim's
   content words to be substantially covered by the passage (entailment proxy).

The model emitting an id is never sufficient (docs/10 §5). This module is pure and infrastructure
free; it backs the non-waivable ``citation_correctness >= 0.95`` /
``citation_fabrication_rate <= 0.01`` thresholds.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .model import Citation, PassageRef

# Support threshold: fraction of the claim's content words that must appear in the cited passage
# for the claim to be considered grounded in that passage.
_SUPPORT_THRESHOLD = 0.6

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "with",
        "as",
        "by",
        "at",
        "it",
        "this",
        "that",
        "these",
        "those",
        "from",
        "into",
        "about",
        "can",
        "will",
        "using",
        "use",
        "how",
        "what",
        "which",
    }
)


def _content_words(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]


@dataclass(frozen=True, slots=True)
class CitationVerdict:
    """The verification outcome for one proposed citation."""

    citation: Citation
    valid: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class CitationReport:
    """The aggregate verification result over all proposed citations for an answer."""

    verdicts: tuple[CitationVerdict, ...]

    @property
    def valid(self) -> tuple[Citation, ...]:
        return tuple(v.citation for v in self.verdicts if v.valid)

    @property
    def rejected(self) -> tuple[CitationVerdict, ...]:
        return tuple(v for v in self.verdicts if not v.valid)

    @property
    def valid_count(self) -> int:
        return len(self.valid)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


def supports(claim: str, passage_text: str) -> bool:
    """Return ``True`` iff ``passage_text`` substantially supports ``claim`` (entailment proxy)."""
    claim_words = _content_words(claim)
    if not claim_words:
        return False
    passage_words = set(_content_words(passage_text))
    covered = sum(1 for w in claim_words if w in passage_words)
    return covered / len(claim_words) >= _SUPPORT_THRESHOLD


def verify_citation(citation: Citation, retrieved: Mapping[str, PassageRef]) -> CitationVerdict:
    """Verify one citation against the retrieved-passage set (by ``chunk_id``)."""
    passage = retrieved.get(citation.chunk_id)
    if passage is None:
        return CitationVerdict(citation=citation, valid=False, reason_code="ai.citation.fabricated")
    if (
        passage.object_id != citation.object_id
        or passage.revision_id != citation.revision_id
        or passage.block_id != citation.block_id
    ):
        return CitationVerdict(
            citation=citation, valid=False, reason_code="ai.citation.identity_mismatch"
        )
    if not supports(citation.claim, passage.text):
        return CitationVerdict(
            citation=citation, valid=False, reason_code="ai.citation.unsupported"
        )
    return CitationVerdict(citation=citation, valid=True, reason_code="ai.citation.verified")


def verify_citations(
    citations: Sequence[Citation], retrieved: Sequence[PassageRef]
) -> CitationReport:
    """Verify all proposed citations; fabricated/unsupported ones are rejected (never emitted)."""
    index = {passage.chunk_id: passage for passage in retrieved}
    return CitationReport(
        verdicts=tuple(verify_citation(citation, index) for citation in citations)
    )
