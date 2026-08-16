"""Retrieval capabilities: one authoritative implementation per action (LAW-04).

Two capabilities run through the kernel command/query buses (deny-by-default authorized + audited,
rule 50/LAW-14):

* ``retrieval.revision.index`` (command) builds the FTS + chunk + embedding projections for a
  PUBLISHED revision. It is the seam the publish flow (or a ``document-published`` subscriber)
  calls; it never reads another module's tables — the publisher supplies the block passages, so
  retrieval owns only its own derived projections (LAW-13).
* ``retrieval.search`` (query) runs hybrid FTS + EXACT vector retrieval, applies the tenant/
  visibility ACL INSIDE the query, fuses with reciprocal-rank fusion and RE-CHECKS the ACL before
  returning any passage (FR-RET-006). Every result carries stable source/revision/block identity
  (FR-RET-007).

Tenant scope and the acting subject are taken from the authenticated :class:`RequestContext`,
never from the payload (rule 50). Handlers depend only on :mod:`.ports` and the pure
:mod:`..domain`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.acl import AclPredicate
from ..domain.cutover import select_serving_profile
from ..domain.errors import RetrievalInvariantViolation, TenantScopeMissing
from ..domain.fusion import DEFAULT_RRF_K, reciprocal_rank_fusion
from ..domain.model import (
    Candidate,
    Chunk,
    EmbeddingProfileRef,
    PassageSource,
    RetrievedPassage,
    SearchQuery,
    Visibility,
)
from .ports import EmbeddingPort, RetrievalRepositoryPort
from .rebuild import REBUILD_CAPABILITIES

CAP_VERSION = "1.0.0"

CAP_INDEX_REVISION = "retrieval.revision.index"
CAP_SEARCH = "retrieval.search"

RETRIEVAL_CAPABILITIES: tuple[str, ...] = (
    CAP_INDEX_REVISION,
    CAP_SEARCH,
    *REBUILD_CAPABILITIES,
)

RES_CORPUS = "retrieval.corpus"

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]

_DEFAULT_LOCALE = "en"


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PassageInput:
    """A block's citable text to index: stable ``block_id`` + ``ordinal`` + the ``text``."""

    block_id: str
    ordinal: int
    text: str


@dataclass(frozen=True, slots=True)
class IndexRevisionCommand:
    """Index a PUBLISHED revision's passages (supplied by the publisher, LAW-13)."""

    object_id: str
    revision_id: str
    visibility: str
    passages: tuple[PassageInput, ...]
    locale: str = _DEFAULT_LOCALE
    owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class IndexRevisionResult:
    object_id: str
    revision_id: str
    indexed_chunks: int
    profile_id: str
    profile_version: str
    dimensions: int


@dataclass(frozen=True, slots=True)
class SearchParameters:
    """A hybrid-search request. ``locale`` selects the FTS language configuration."""

    text: str
    top_k: int = 10
    locale: str = _DEFAULT_LOCALE


@dataclass(frozen=True, slots=True)
class PassageView:
    """The wire/return shape of one result — identity-bearing for citation (FR-RET-007).

    ``matched_profile_version`` is the profile version of the embedding row that actually produced
    this passage's semantic hit (``None`` when only the lexical component matched) — real
    provenance, not the serving-profile label (FR-RET-004).
    """

    object_id: str
    revision_id: str
    block_id: str
    ordinal: int
    chunk_id: str
    text: str
    score: float
    lexical_rank: int | None
    semantic_rank: int | None
    matched_profile_version: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResultView:
    """A hybrid-search result set.

    ``profile_id``/``profile_version`` are the SERVING-profile label (which single profile the
    active-pointer selection served this query). ``matched_profile_versions`` is the DISTINCT set of
    profile versions of the embedding rows that actually matched — the honest no-mix evidence: a
    correctly cut-over query yields exactly one version, and any cross-version leak (e.g. a dropped
    ``profile_id``/``profile_version`` filter in semantic search) makes this set size > 1
    (FR-RET-004).
    """

    query: str
    profile_id: str
    profile_version: str
    results: tuple[PassageView, ...] = field(default_factory=tuple)
    matched_profile_versions: tuple[str, ...] = field(default_factory=tuple)


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


def _subject(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    actor = getattr(context, "actor", None)
    subject = getattr(actor, "id", None)
    if not subject:
        raise TenantScopeMissing()
    return subject


def _visibility(value: str) -> Visibility:
    try:
        return Visibility(value)
    except ValueError as exc:
        raise RetrievalInvariantViolation(
            f"unknown visibility {value!r}", code="retrieval.index.visibility"
        ) from exc


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class IndexRevision:
    """``retrieval.revision.index`` — build FTS + chunk + embedding projections for a revision.

    Idempotent per revision: existing projections for the revision are cleared first, so a
    re-publish (or replay) rebuilds cleanly rather than duplicating chunks.
    """

    def __init__(
        self,
        *,
        repository: RetrievalRepositoryPort,
        embedding: EmbeddingPort,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._embedding = embedding
        self._id_factory = id_factory

    def handle(self, request: object) -> IndexRevisionResult:
        command = _typed(request, IndexRevisionCommand)
        organization_id = _tenant(request)
        visibility = _visibility(command.visibility)
        owner_id = command.owner_id
        if visibility is Visibility.PRIVATE and not owner_id:
            raise RetrievalInvariantViolation(
                "a private revision must carry an owner_id to index", code="retrieval.index.owner"
            )
        profile = self._embedding.profile
        self._repo.register_profile(profile)
        self._repo.clear_revision(
            organization_id=organization_id,
            object_id=command.object_id,
            revision_id=command.revision_id,
        )
        indexed = 0
        for passage in command.passages:
            text = passage.text
            if not text.strip():
                continue
            source = PassageSource(
                object_id=command.object_id,
                revision_id=command.revision_id,
                block_id=passage.block_id,
                ordinal=passage.ordinal,
            )
            chunk = Chunk(
                chunk_id=self._id_factory(),
                organization_id=organization_id,
                source=source,
                text=text,
                language=command.locale,
                visibility=visibility,
                content_hash=_content_hash(text),
                owner_id=owner_id if visibility is Visibility.PRIVATE else None,
            )
            vector = self._embedding.embed(text)
            self._repo.index_chunk(chunk=chunk, vector=vector, profile=profile)
            indexed += 1
        return IndexRevisionResult(
            object_id=command.object_id,
            revision_id=command.revision_id,
            indexed_chunks=indexed,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            dimensions=profile.dimensions,
        )


class Search:
    """``retrieval.search`` — hybrid FTS + EXACT vector, ACL-filtered + re-checked, RRF-fused.

    The query bus authorizes the action deny-by-default before this handler runs. Inside, the ACL
    predicate is derived from the authenticated context and pushed INTO both component queries; the
    fused result is then re-checked passage-by-passage before disclosure (defense in depth,
    FR-RET-006). ``rrf_k`` is injectable for tuning without code changes.

    Cutover-aware: when more than one embedding profile version is available (``embeddings``), the
    handler serves the ONE version the repository marks active, embedding the query under it, so a
    single query never mixes old+new-profile embeddings and a rebuild's atomic flip switches the
    served version with no downtime (FR-RET-004). With a single embedding it behaves exactly as
    before (the injected profile serves, no active pointer required).
    """

    def __init__(
        self,
        *,
        repository: RetrievalRepositoryPort,
        embedding: EmbeddingPort,
        embeddings: Sequence[EmbeddingPort] | None = None,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        self._repo = repository
        self._default_embedding = embedding
        self._by_ref: dict[EmbeddingProfileRef, EmbeddingPort] = {
            e.profile.ref: e for e in (embeddings or (embedding,))
        }
        self._rrf_k = rrf_k

    def _serving_embedding(self) -> EmbeddingPort:
        """Resolve the ONE embedding whose profile version search must serve right now."""
        active = self._repo.active_profile()
        ref = select_serving_profile(
            active=active,
            available=tuple(self._by_ref),
            fallback=self._default_embedding.profile.ref,
        )
        return self._by_ref[ref]

    def handle(self, request: object) -> SearchResultView:
        params = _typed(request, SearchParameters)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        query = SearchQuery(text=params.text, top_k=params.top_k)
        predicate = AclPredicate(organization_id=organization_id, subject_id=subject_id)
        embedding = self._serving_embedding()
        profile = embedding.profile

        # A generous candidate window per component so fusion sees enough overlap before the cut.
        window = max(query.top_k * 4, query.top_k)
        lexical = list(
            self._repo.search_lexical(
                predicate=predicate,
                query_text=query.text,
                language=params.locale,
                top_k=window,
            )
        )
        query_vector = embedding.embed(query.text)
        semantic = list(
            self._repo.search_semantic(
                predicate=predicate,
                query_vector=query_vector,
                profile=profile,
                top_k=window,
            )
        )

        results = _fuse_and_recheck(
            predicate=predicate,
            lexical=lexical,
            semantic=semantic,
            top_k=query.top_k,
            rrf_k=self._rrf_k,
        )
        # The DISTINCT set of profile versions of the embedding rows that actually matched (after
        # the ACL re-check). This is the honest no-mix signal — it is derived from every permitted
        # semantic candidate, NOT from the serving-profile label, so a cross-version leak shows up
        # as a set of size > 1 (FR-RET-004).
        matched_versions = sorted(
            {
                candidate.profile_version
                for candidate in semantic
                if candidate.profile_version is not None and predicate.permits(candidate)
            }
        )
        return SearchResultView(
            query=query.text,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            results=tuple(_to_view(passage) for passage in results),
            matched_profile_versions=tuple(matched_versions),
        )


def _fuse_and_recheck(
    *,
    predicate: AclPredicate,
    lexical: Sequence[Candidate],
    semantic: Sequence[Candidate],
    top_k: int,
    rrf_k: int,
) -> list[RetrievedPassage]:
    """Fuse the two component rankings and RE-CHECK the ACL before returning any passage."""
    by_chunk: dict[str, Candidate] = {}
    for candidate in (*lexical, *semantic):
        by_chunk.setdefault(candidate.chunk_id, candidate)
    lexical_rank = {c.chunk_id: i for i, c in enumerate(lexical, start=1)}
    semantic_rank = {c.chunk_id: i for i, c in enumerate(semantic, start=1)}
    # The embedding profile that actually produced each chunk's semantic hit (best/nearest wins),
    # so a passage carries the real matched profile as provenance — never the serving label.
    semantic_profile: dict[str, tuple[str | None, str | None]] = {}
    for candidate in semantic:
        semantic_profile.setdefault(
            candidate.chunk_id, (candidate.profile_id, candidate.profile_version)
        )

    fused = reciprocal_rank_fusion(
        [[c.chunk_id for c in lexical], [c.chunk_id for c in semantic]], k=rrf_k
    )
    passages: list[RetrievedPassage] = []
    for entry in fused:
        candidate = by_chunk[entry.key]
        # Re-check ACL before disclosure: never emit a passage the caller may not read (FR-RET-006).
        if not predicate.permits(candidate):
            continue
        matched_pid, matched_pver = semantic_profile.get(candidate.chunk_id, (None, None))
        passages.append(
            RetrievedPassage(
                chunk_id=candidate.chunk_id,
                source=candidate.source,
                text=candidate.text,
                score=entry.score,
                lexical_rank=lexical_rank.get(candidate.chunk_id),
                semantic_rank=semantic_rank.get(candidate.chunk_id),
                matched_profile_id=matched_pid,
                matched_profile_version=matched_pver,
            )
        )
        if len(passages) >= top_k:
            break
    return passages


def _to_view(passage: RetrievedPassage) -> PassageView:
    return PassageView(
        object_id=passage.source.object_id,
        revision_id=passage.source.revision_id,
        block_id=passage.source.block_id,
        ordinal=passage.source.ordinal,
        chunk_id=passage.chunk_id,
        text=passage.text,
        score=passage.score,
        lexical_rank=passage.lexical_rank,
        semantic_rank=passage.semantic_rank,
        matched_profile_version=passage.matched_profile_version,
    )


def _content_hash(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
