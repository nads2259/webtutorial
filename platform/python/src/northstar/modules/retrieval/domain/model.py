"""Retrieval value objects (docs/06 §4/§6/§7): identity, profile, chunk, query and result.

Pure and infrastructure-free (rule 10, LAW-02). Every retrieved passage carries stable
source/revision/block identity so a caller or model can generate inspectable citations
(FR-RET-007). Embeddings are versioned derived artifacts described by an
:class:`EmbeddingProfile` recording provider/model/dimensions/metric/chunker (FR-RET-003).

Retrieval defines its OWN ``Visibility`` enum rather than importing the knowledge module's, so the
domain dependency graph stays a DAG (rule 10, LAW-13); the string values are kept identical to the
knowledge publication visibility so projections line up 1:1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .errors import RetrievalInvariantViolation

# ``simple`` is the language-agnostic default; language-specific configurations refine stemming.
DISTANCE_L2 = "l2"


class Visibility(StrEnum):
    """Publication visibility of an indexed passage (mirrors knowledge publication visibility)."""

    PRIVATE = "private"
    ORGANIZATION = "organization"
    PUBLIC = "public"


@dataclass(frozen=True, slots=True)
class PassageSource:
    """Stable citation identity of a retrieved passage (FR-RET-007).

    ``object_id``/``revision_id``/``block_id`` bind a passage back to the immutable published
    knowledge revision and the exact block within it; ``ordinal`` orders chunks derived from the
    same block.
    """

    object_id: str
    revision_id: str
    block_id: str
    ordinal: int

    def __post_init__(self) -> None:
        for name in ("object_id", "revision_id", "block_id"):
            if not getattr(self, name):
                raise RetrievalInvariantViolation(
                    f"{name} must be non-empty", code="retrieval.source.identity"
                )
        if self.ordinal < 0:
            raise RetrievalInvariantViolation(
                "ordinal must be >= 0", code="retrieval.source.ordinal"
            )


@dataclass(frozen=True, slots=True)
class EmbeddingProfileRef:
    """The stable identity of an embedding profile version — ``(profile_id, version)``.

    The retrieval projections coexist per profile version (``chunk_embedding`` is keyed by
    ``chunk_id`` + this ref), so a rebuild can build a NEW version's embeddings while the prior
    version keeps serving; the "active" pointer selects exactly one ref at any instant (FR-RET-004).
    """

    profile_id: str
    version: str

    def __post_init__(self) -> None:
        if not self.profile_id or not self.version:
            raise RetrievalInvariantViolation(
                "an embedding profile ref requires a profile_id and version",
                code="retrieval.profile.ref",
            )


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    """A versioned embedding profile: provider/model/dimensions/metric/chunker (FR-RET-003).

    Model name alone is insufficient (docs/06 §6): the profile records the ``provider``, ``model``,
    vector ``dimensions``, ``distance_metric`` and ``chunker_version`` so every stored embedding is
    reproducible and a re-embed can build a NEW versioned index (FR-RET-004 seam).
    """

    profile_id: str
    version: str
    provider: str
    model: str
    dimensions: int
    distance_metric: str = DISTANCE_L2
    chunker_version: str = "block-1.0.0"

    def __post_init__(self) -> None:
        if not self.profile_id or not self.version:
            raise RetrievalInvariantViolation(
                "embedding profile requires a profile_id and version",
                code="retrieval.profile.id",
            )
        if self.dimensions <= 0:
            raise RetrievalInvariantViolation(
                "embedding dimensions must be positive", code="retrieval.profile.dimensions"
            )

    @property
    def ref(self) -> EmbeddingProfileRef:
        """The ``(profile_id, version)`` identity of this profile version."""
        return EmbeddingProfileRef(profile_id=self.profile_id, version=self.version)


@dataclass(frozen=True, slots=True)
class Chunk:
    """A tenant-scoped, ACL-bearing text chunk projected from a published block (docs/06 §6).

    ``owner_id`` is the subject who may read a ``PRIVATE`` passage; it is ``None`` for
    organization/public passages. ``content_hash`` records provenance of the exact text embedded.
    """

    chunk_id: str
    organization_id: str
    source: PassageSource
    text: str
    language: str
    visibility: Visibility
    content_hash: str
    owner_id: str | None = None

    def __post_init__(self) -> None:
        if not self.chunk_id:
            raise RetrievalInvariantViolation(
                "chunk_id must be non-empty", code="retrieval.chunk.id"
            )
        if not self.organization_id:
            raise RetrievalInvariantViolation(
                "organization_id must be non-empty", code="retrieval.chunk.scope"
            )
        if self.visibility is Visibility.PRIVATE and not self.owner_id:
            raise RetrievalInvariantViolation(
                "a private chunk must record its owner_id", code="retrieval.chunk.owner"
            )


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A retrieval request: the natural-language ``text`` and how many results to return."""

    text: str
    top_k: int = 10

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise RetrievalInvariantViolation(
                "search text must be non-empty", code="retrieval.query.text"
            )
        if self.top_k < 1:
            raise RetrievalInvariantViolation("top_k must be >= 1", code="retrieval.query.top_k")


@dataclass(frozen=True, slots=True)
class Candidate:
    """A single component-retrieval hit carrying enough data to re-check ACL and fuse (docs/06 §7).

    Component retrievers (lexical FTS, exact vector) each return an ordered list of these; the
    ``organization_id``/``visibility``/``owner_id`` fields let the disclosure re-check run on the
    fused result without a second database round-trip.

    ``profile_id``/``profile_version`` record the identity of the embedding row that ACTUALLY
    matched, for a semantic candidate — the real provenance of the vector hit, NOT the serving-
    profile label. They stay ``None`` for a lexical (FTS) candidate, which has no embedding. This is
    what makes the no-mix invariant honest: a query that ever joins a foreign profile's embedding
    surfaces that version here (FR-RET-004).
    """

    chunk_id: str
    organization_id: str
    source: PassageSource
    text: str
    visibility: Visibility
    owner_id: str | None
    score: float
    profile_id: str | None = None
    profile_version: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    """A fused, ACL-cleared result with stable citation identity and component provenance.

    ``matched_profile_id``/``matched_profile_version`` carry the identity of the embedding row that
    actually produced this passage's semantic hit (``None`` when only the lexical component
    matched), so provenance is the real matched profile, not the serving label (FR-RET-004/007).
    """

    chunk_id: str
    source: PassageSource
    text: str
    score: float
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    matched_profile_id: str | None = None
    matched_profile_version: str | None = None


@dataclass(frozen=True, slots=True)
class Embedding:
    """A stored embedding record: the vector plus its profile + input provenance (FR-RET-003)."""

    chunk_id: str
    profile_id: str
    profile_version: str
    vector: tuple[float, ...]
    input_hash: str
    generated_at: datetime
