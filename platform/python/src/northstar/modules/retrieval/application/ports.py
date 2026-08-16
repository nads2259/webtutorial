"""Ports (abstractions) for the retrieval application layer (rule 10/20, DIP, FR-RET-008).

Two seams keep the domain infrastructure-free:

* :class:`EmbeddingPort` — the embedding model seam. The reference adapter is a DETERMINISTIC
  local hashed-n-gram embedding so tests are reproducible with no external model; a real provider
  is a straight adapter swap behind this port (FR-RET-003/008).
* :class:`RetrievalRepositoryPort` — the projection store. Every read/write is tenant-scoped and
  applies the :class:`AclPredicate` INSIDE the query (FR-RET-006). An external search/vector engine
  can replace this adapter without touching the domain or capabilities (FR-RET-008).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.acl import AclPredicate
from ..domain.model import Candidate, Chunk, EmbeddingProfile, EmbeddingProfileRef


@runtime_checkable
class EmbeddingPort(Protocol):
    """Turns text into a fixed-dimension vector under a recorded :class:`EmbeddingProfile`."""

    @property
    def profile(self) -> EmbeddingProfile:
        """The profile these embeddings are recorded under (provider/model/dimensions/metric)."""
        ...

    def embed(self, text: str) -> tuple[float, ...]:
        """Return the embedding vector for ``text`` (length == ``profile.dimensions``)."""
        ...


@runtime_checkable
class RetrievalRepositoryPort(Protocol):
    """Persists and searches the retrieval projections, always tenant-scoped and ACL-filtered."""

    def register_profile(self, profile: EmbeddingProfile) -> None:
        """Record the embedding profile (idempotent by ``profile_id``+``version``)."""
        ...

    def clear_revision(self, *, organization_id: str, object_id: str, revision_id: str) -> None:
        """Remove any existing chunk/embedding projections for a revision before re-indexing."""
        ...

    def index_chunk(
        self, *, chunk: Chunk, vector: tuple[float, ...], profile: EmbeddingProfile
    ) -> None:
        """Store a chunk's FTS + embedding projection (tenant-scoped write)."""
        ...

    def search_lexical(
        self, *, predicate: AclPredicate, query_text: str, language: str, top_k: int
    ) -> Sequence[Candidate]:
        """Language-aware FTS candidates, ACL-filtered INSIDE the query, best-first (FR-RET-002)."""
        ...

    def search_semantic(
        self,
        *,
        predicate: AclPredicate,
        query_vector: tuple[float, ...],
        profile: EmbeddingProfile,
        top_k: int,
    ) -> Sequence[Candidate]:
        """EXACT vector candidates, ACL-filtered INSIDE the query, nearest-first (FR-RET-005)."""
        ...

    # -- embedding rebuild-and-cutover seam (FR-RET-004) --------------------------------------

    def all_chunks(self, *, organization_id: str) -> Sequence[Chunk]:
        """Every indexed chunk for a tenant, so a rebuild can re-embed them under a NEW profile."""
        ...

    def add_embedding(
        self, *, chunk: Chunk, vector: tuple[float, ...], profile: EmbeddingProfile
    ) -> None:
        """Store ONE chunk's embedding under ``profile`` (idempotent) without touching the chunk.

        Used by a rebuild to build a new profile version's projection alongside the currently
        serving one (build-then-cutover, never drop-then-build).
        """
        ...

    def active_profile(self) -> EmbeddingProfileRef | None:
        """Return the profile version search must serve, or ``None`` if none is active yet."""
        ...

    def activate_profile(self, profile: EmbeddingProfile | EmbeddingProfileRef) -> None:
        """Atomically mark exactly ``profile`` active (all others inactive) — the cutover flip."""
        ...

    def retire_profile(
        self, *, organization_id: str, profile: EmbeddingProfile | EmbeddingProfileRef
    ) -> None:
        """Delete a superseded (non-active) profile version's embeddings after cutover."""
        ...
