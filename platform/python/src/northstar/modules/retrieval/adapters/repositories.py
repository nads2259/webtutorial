"""Retrieval repositories (in-memory + SQLAlchemy) implementing :class:`RetrievalRepositoryPort`.

The SQLAlchemy repository builds language-aware FTS (``tsvector``) and EXACT pgvector search over
the ``northstar_retrieval`` projections. ACL is applied INSIDE every query — a tenant predicate on
``organization_id`` plus a visibility/owner predicate — AND the per-transaction tenant GUC is set
so PostgreSQL RLS denies foreign-tenant rows as defense-in-depth (FR-RET-006, rule 50). Semantic
search is EXACT (``ORDER BY vector <-> query``) with no ANN index: the exact baseline is
established before any HNSW/IVFFlat index is considered (FR-RET-005). Every value is a bound
parameter; only fixed internal identifiers (schema/table names, the allowlisted FTS
configuration) are interpolated.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.acl import AclPredicate
from ..domain.model import (
    Candidate,
    Chunk,
    EmbeddingProfile,
    EmbeddingProfileRef,
    PassageSource,
    Visibility,
)
from ..domain.vectors import l2_distance
from .tables import RetrievalTables

# Allowlisted FTS text-search configurations (never user input). Locale prefixes map to a
# PostgreSQL configuration; anything else falls back to the language-agnostic ``simple`` config.
_FTS_CONFIGS: dict[str, str] = {
    "en": "english",
    "es": "spanish",
    "fr": "french",
    "de": "german",
    "pt": "portuguese",
    "it": "italian",
    "nl": "dutch",
}
_DEFAULT_FTS_CONFIG = "simple"


def _regconfig(language: str) -> str:
    """Map a locale (e.g. ``en``/``en-US``) to an allowlisted FTS configuration name."""
    return _FTS_CONFIGS.get(language.split("-")[0].lower(), _DEFAULT_FTS_CONFIG)


def _vector_literal(vector: Sequence[float]) -> str:
    """Render a vector as the pgvector text literal ``[v1,v2,...]`` (bound + cast in SQL)."""
    return "[" + ",".join(format(float(component), ".8g") for component in vector) + "]"


def _visibility_clause(param: str) -> str:
    """The ACL visibility/owner predicate applied INSIDE every retrieval query (FR-RET-006)."""
    return (
        "(visibility IN ('public', 'organization') "
        f"OR (visibility = 'private' AND owner_id = :{param}))"
    )


class InMemoryRetrievalRepository:
    """In-memory repository for fast, deterministic unit tests (mirrors ACL-inside-query)."""

    def __init__(self) -> None:
        self._profiles: dict[tuple[str, str], EmbeddingProfile] = {}
        self._chunks: dict[str, Chunk] = {}
        # Embeddings are keyed by (chunk_id, profile_id, version) so multiple profile versions
        # coexist for the same chunk during a build-then-cutover (FR-RET-004).
        self._embeddings: dict[tuple[str, str, str], tuple[float, ...]] = {}
        self._active: EmbeddingProfileRef | None = None

    def register_profile(self, profile: EmbeddingProfile) -> None:
        self._profiles[(profile.profile_id, profile.version)] = profile

    def clear_revision(self, *, organization_id: str, object_id: str, revision_id: str) -> None:
        to_drop = [
            chunk_id
            for chunk_id, chunk in self._chunks.items()
            if chunk.organization_id == organization_id
            and chunk.source.object_id == object_id
            and chunk.source.revision_id == revision_id
        ]
        for chunk_id in to_drop:
            self._chunks.pop(chunk_id, None)
            for key in [k for k in self._embeddings if k[0] == chunk_id]:
                self._embeddings.pop(key, None)

    def index_chunk(
        self, *, chunk: Chunk, vector: tuple[float, ...], profile: EmbeddingProfile
    ) -> None:
        self._chunks[chunk.chunk_id] = chunk
        self._embeddings[(chunk.chunk_id, profile.profile_id, profile.version)] = vector

    def all_chunks(self, *, organization_id: str) -> Sequence[Chunk]:
        return [c for c in self._chunks.values() if c.organization_id == organization_id]

    def add_embedding(
        self, *, chunk: Chunk, vector: tuple[float, ...], profile: EmbeddingProfile
    ) -> None:
        self._embeddings[(chunk.chunk_id, profile.profile_id, profile.version)] = vector

    def active_profile(self) -> EmbeddingProfileRef | None:
        return self._active

    def activate_profile(self, profile: EmbeddingProfile | EmbeddingProfileRef) -> None:
        self._active = profile.ref if isinstance(profile, EmbeddingProfile) else profile

    def retire_profile(
        self, *, organization_id: str, profile: EmbeddingProfile | EmbeddingProfileRef
    ) -> None:
        ref = profile.ref if isinstance(profile, EmbeddingProfile) else profile
        for key in [
            k
            for k in self._embeddings
            if k[1] == ref.profile_id
            and k[2] == ref.version
            and self._chunks.get(k[0], None) is not None
            and self._chunks[k[0]].organization_id == organization_id
        ]:
            self._embeddings.pop(key, None)

    def _visible(self, predicate: AclPredicate) -> list[Chunk]:
        return [chunk for chunk in self._chunks.values() if predicate.permits_chunk(chunk)]

    def search_lexical(
        self, *, predicate: AclPredicate, query_text: str, language: str, top_k: int
    ) -> Sequence[Candidate]:
        terms = {t for t in query_text.lower().split() if t}
        scored: list[tuple[float, str, Chunk]] = []
        for chunk in self._visible(predicate):
            tokens = chunk.text.lower().split()
            overlap = sum(1 for token in tokens if token.strip(".,!?;:").lower() in terms)
            if overlap > 0:
                scored.append((float(overlap), chunk.chunk_id, chunk))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [_candidate(chunk, score) for score, _cid, chunk in scored[:top_k]]

    def search_semantic(
        self,
        *,
        predicate: AclPredicate,
        query_vector: tuple[float, ...],
        profile: EmbeddingProfile,
        top_k: int,
    ) -> Sequence[Candidate]:
        scored: list[tuple[float, str, Chunk]] = []
        for chunk in self._visible(predicate):
            # Only this profile version's embeddings — never a mix across versions (FR-RET-004).
            vector = self._embeddings.get((chunk.chunk_id, profile.profile_id, profile.version))
            if vector is None:
                continue
            scored.append((l2_distance(query_vector, vector), chunk.chunk_id, chunk))
        scored.sort(key=lambda item: (item[0], item[1]))
        # Lower distance is a better hit; expose it as a negative score so higher == better. Carry
        # the profile of the embedding row ACTUALLY used (the exact key that returned a vector), so
        # returning a non-serving profile's embedding would surface as a cross-version mix.
        return [
            _candidate(
                chunk, -distance, profile_id=profile.profile_id, profile_version=profile.version
            )
            for distance, _cid, chunk in scored[:top_k]
        ]


class SqlAlchemyRetrievalRepository:
    """PostgreSQL repository; queries filter by ``organization_id`` + visibility and set the GUC."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[SaSession],
        tables: RetrievalTables,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._tables = tables
        self._clock = clock
        self._schema = tables.schema

    # -- fully-qualified table names (internal constants, safe to interpolate) --
    @property
    def _profile_table(self) -> str:
        return f'"{self._schema}".embedding_profile'

    @property
    def _chunk_table(self) -> str:
        return f'"{self._schema}".knowledge_chunk'

    @property
    def _embedding_table(self) -> str:
        return f'"{self._schema}".chunk_embedding'

    def register_profile(self, profile: EmbeddingProfile) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                text(
                    f"INSERT INTO {self._profile_table} "  # noqa: S608 internal identifiers
                    "(profile_id, version, provider, model, dimensions, distance_metric, "
                    " chunker_version, active, created_at) "
                    "VALUES (:profile_id, :version, :provider, :model, :dimensions, "
                    " :distance_metric, :chunker_version, false, :created_at) "
                    "ON CONFLICT (profile_id, version) DO NOTHING"
                ),
                {
                    "profile_id": profile.profile_id,
                    "version": profile.version,
                    "provider": profile.provider,
                    "model": profile.model,
                    "dimensions": profile.dimensions,
                    "distance_metric": profile.distance_metric,
                    "chunker_version": profile.chunker_version,
                    "created_at": self._clock(),
                },
            )
            uow.commit()

    def clear_revision(self, *, organization_id: str, object_id: str, revision_id: str) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            params = {"org": organization_id, "object_id": object_id, "revision_id": revision_id}
            uow.session.execute(
                text(
                    f"DELETE FROM {self._embedding_table} "  # noqa: S608 internal identifiers
                    "WHERE organization_id = :org AND chunk_id IN "
                    f"(SELECT chunk_id FROM {self._chunk_table} "
                    " WHERE organization_id = :org AND object_id = :object_id "
                    "   AND revision_id = :revision_id)"
                ),
                params,
            )
            uow.session.execute(
                text(
                    f"DELETE FROM {self._chunk_table} "  # noqa: S608 internal identifiers
                    "WHERE organization_id = :org "
                    "AND object_id = :object_id AND revision_id = :revision_id"
                ),
                params,
            )
            uow.commit()

    def index_chunk(
        self, *, chunk: Chunk, vector: tuple[float, ...], profile: EmbeddingProfile
    ) -> None:
        config = _regconfig(chunk.language)
        now = self._clock()
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, chunk.organization_id)
            uow.session.execute(
                text(
                    f"INSERT INTO {self._chunk_table} "  # noqa: S608 internal identifiers
                    "(chunk_id, organization_id, object_id, revision_id, block_id, ordinal, "
                    " text_content, language, visibility, owner_id, content_sha256, tsv, "
                    " metadata, created_at) "
                    "VALUES (:chunk_id, :org, :object_id, :revision_id, :block_id, :ordinal, "
                    f" CAST(:text AS text), :language, :visibility, :owner_id, :content_hash, "
                    f" to_tsvector('{config}', CAST(:fts_text AS text)), NULL, :created_at)"
                ),
                {
                    "chunk_id": chunk.chunk_id,
                    "org": chunk.organization_id,
                    "object_id": chunk.source.object_id,
                    "revision_id": chunk.source.revision_id,
                    "block_id": chunk.source.block_id,
                    "ordinal": chunk.source.ordinal,
                    "text": chunk.text,
                    "fts_text": chunk.text,
                    "language": chunk.language,
                    "visibility": chunk.visibility.value,
                    "owner_id": chunk.owner_id,
                    "content_hash": chunk.content_hash,
                    "created_at": now,
                },
            )
            uow.session.execute(
                text(
                    f"INSERT INTO {self._embedding_table} "  # noqa: S608 internal identifiers
                    "(chunk_id, profile_id, profile_version, organization_id, vector, "
                    " input_hash, generated_at) "
                    "VALUES (:chunk_id, :profile_id, :profile_version, :org, "
                    " CAST(:vector AS vector), :input_hash, :generated_at)"
                ),
                {
                    "chunk_id": chunk.chunk_id,
                    "profile_id": profile.profile_id,
                    "profile_version": profile.version,
                    "org": chunk.organization_id,
                    "vector": _vector_literal(vector),
                    "input_hash": chunk.content_hash,
                    "generated_at": now,
                },
            )
            uow.commit()

    def search_lexical(
        self, *, predicate: AclPredicate, query_text: str, language: str, top_k: int
    ) -> Sequence[Candidate]:
        config = _regconfig(language)
        sql = (
            "SELECT chunk_id, organization_id, object_id, revision_id, block_id, ordinal, "  # noqa: S608
            "       text_content, visibility, owner_id, "
            f"       ts_rank_cd(tsv, plainto_tsquery('{config}', :q)) AS rank "
            f"FROM {self._chunk_table} "
            "WHERE organization_id = :org "
            f"  AND {_visibility_clause('subject')} "
            f"  AND tsv @@ plainto_tsquery('{config}', :q) "
            "ORDER BY rank DESC, chunk_id "
            "LIMIT :k"
        )
        params = {
            "q": query_text,
            "org": predicate.organization_id,
            "subject": predicate.subject_id,
            "k": top_k,
        }
        with self._session_factory() as session:
            set_tenant_guc(session, predicate.organization_id)
            rows = session.execute(text(sql), params).all()
        return [_row_candidate(row, float(row.rank)) for row in rows]

    def search_semantic(
        self,
        *,
        predicate: AclPredicate,
        query_vector: tuple[float, ...],
        profile: EmbeddingProfile,
        top_k: int,
    ) -> Sequence[Candidate]:
        sql = (
            "SELECT c.chunk_id, c.organization_id, c.object_id, c.revision_id, c.block_id, "  # noqa: S608
            "       c.ordinal, c.text_content, c.visibility, c.owner_id, "
            "       e.profile_id AS matched_profile_id, "
            "       e.profile_version AS matched_profile_version, "
            "       (e.vector <-> CAST(:vec AS vector)) AS distance "
            f"FROM {self._embedding_table} e "
            f"JOIN {self._chunk_table} c ON c.chunk_id = e.chunk_id "
            "WHERE c.organization_id = :org "
            "  AND e.profile_id = :pid AND e.profile_version = :pver "
            f"  AND {_visibility_clause('subject')} "
            "ORDER BY distance ASC, c.chunk_id "
            "LIMIT :k"
        )
        params = {
            "vec": _vector_literal(query_vector),
            "org": predicate.organization_id,
            "subject": predicate.subject_id,
            "pid": profile.profile_id,
            "pver": profile.version,
            "k": top_k,
        }
        with self._session_factory() as session:
            set_tenant_guc(session, predicate.organization_id)
            rows = session.execute(text(sql), params).all()
        # Lower distance is a better hit; expose it as a negative score so higher == better. Each
        # candidate carries the profile of the embedding row that ACTUALLY matched (e.profile_*),
        # so a dropped profile filter surfaces as a cross-version mix, not a silent pass.
        return [
            _row_candidate(
                row,
                -float(row.distance),
                profile_id=row.matched_profile_id,
                profile_version=row.matched_profile_version,
            )
            for row in rows
        ]

    # -- embedding rebuild-and-cutover seam (FR-RET-004) --------------------------------------

    def all_chunks(self, *, organization_id: str) -> Sequence[Chunk]:
        sql = (
            "SELECT chunk_id, organization_id, object_id, revision_id, block_id, ordinal, "  # noqa: S608
            "       text_content, language, visibility, owner_id, content_sha256 "
            f"FROM {self._chunk_table} "
            "WHERE organization_id = :org "
            "ORDER BY chunk_id"
        )
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(text(sql), {"org": organization_id}).all()
        return [
            Chunk(
                chunk_id=row.chunk_id,
                organization_id=row.organization_id,
                source=PassageSource(
                    object_id=row.object_id,
                    revision_id=row.revision_id,
                    block_id=row.block_id,
                    ordinal=row.ordinal,
                ),
                text=row.text_content,
                language=row.language,
                visibility=Visibility(row.visibility),
                content_hash=row.content_sha256,
                owner_id=row.owner_id,
            )
            for row in rows
        ]

    def add_embedding(
        self, *, chunk: Chunk, vector: tuple[float, ...], profile: EmbeddingProfile
    ) -> None:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, chunk.organization_id)
            uow.session.execute(
                text(
                    f"INSERT INTO {self._embedding_table} "  # noqa: S608 internal identifiers
                    "(chunk_id, profile_id, profile_version, organization_id, vector, "
                    " input_hash, generated_at) "
                    "VALUES (:chunk_id, :profile_id, :profile_version, :org, "
                    " CAST(:vector AS vector), :input_hash, :generated_at) "
                    "ON CONFLICT (chunk_id, profile_id, profile_version) DO NOTHING"
                ),
                {
                    "chunk_id": chunk.chunk_id,
                    "profile_id": profile.profile_id,
                    "profile_version": profile.version,
                    "org": chunk.organization_id,
                    "vector": _vector_literal(vector),
                    "input_hash": chunk.content_hash,
                    "generated_at": self._clock(),
                },
            )
            uow.commit()

    def active_profile(self) -> EmbeddingProfileRef | None:
        sql = (
            "SELECT profile_id, version "  # noqa: S608 internal identifiers
            f"FROM {self._profile_table} WHERE active = true "
            "ORDER BY profile_id, version LIMIT 1"
        )
        with self._session_factory() as session:
            row = session.execute(text(sql)).first()
        if row is None:
            return None
        return EmbeddingProfileRef(profile_id=row.profile_id, version=row.version)

    def activate_profile(self, profile: EmbeddingProfile | EmbeddingProfileRef) -> None:
        ref = profile.ref if isinstance(profile, EmbeddingProfile) else profile
        # ONE statement flips the pointer atomically: exactly the target row becomes active and
        # every other profile becomes inactive, so search reads a single profile at any instant.
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            uow.session.execute(
                text(
                    f"UPDATE {self._profile_table} "  # noqa: S608 internal identifiers
                    "SET active = (profile_id = :pid AND version = :pver)"
                ),
                {"pid": ref.profile_id, "pver": ref.version},
            )
            uow.commit()

    def retire_profile(
        self, *, organization_id: str, profile: EmbeddingProfile | EmbeddingProfileRef
    ) -> None:
        ref = profile.ref if isinstance(profile, EmbeddingProfile) else profile
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            set_tenant_guc(uow.session, organization_id)
            uow.session.execute(
                text(
                    f"DELETE FROM {self._embedding_table} "  # noqa: S608 internal identifiers
                    "WHERE organization_id = :org "
                    "AND profile_id = :pid AND profile_version = :pver"
                ),
                {"org": organization_id, "pid": ref.profile_id, "pver": ref.version},
            )
            uow.commit()


def _candidate(
    chunk: Chunk,
    score: float,
    *,
    profile_id: str | None = None,
    profile_version: str | None = None,
) -> Candidate:
    return Candidate(
        chunk_id=chunk.chunk_id,
        organization_id=chunk.organization_id,
        source=chunk.source,
        text=chunk.text,
        visibility=chunk.visibility,
        owner_id=chunk.owner_id,
        score=score,
        profile_id=profile_id,
        profile_version=profile_version,
    )


def _row_candidate(
    row: object,
    score: float,
    *,
    profile_id: str | None = None,
    profile_version: str | None = None,
) -> Candidate:
    return Candidate(
        chunk_id=row.chunk_id,  # type: ignore[attr-defined]
        organization_id=row.organization_id,  # type: ignore[attr-defined]
        source=PassageSource(
            object_id=row.object_id,  # type: ignore[attr-defined]
            revision_id=row.revision_id,  # type: ignore[attr-defined]
            block_id=row.block_id,  # type: ignore[attr-defined]
            ordinal=row.ordinal,  # type: ignore[attr-defined]
        ),
        text=row.text_content,  # type: ignore[attr-defined]
        visibility=Visibility(row.visibility),  # type: ignore[attr-defined]
        owner_id=row.owner_id,  # type: ignore[attr-defined]
        score=score,
        profile_id=profile_id,
        profile_version=profile_version,
    )
