"""Embedding rebuild-and-cutover capabilities (FR-RET-004, docs/06 §12, docs/09 index migration).

One authoritative capability per action (LAW-04), run through the kernel command bus
(deny-by-default authorized + audited, rule 50/LAW-14):

* ``retrieval.embedding.rebuild`` — BUILD-then-CUTOVER. It registers a NEW ``EmbeddingProfile``
  version and builds that version's ``chunk_embedding`` projection for EVERY already-indexed chunk
  in the tenant WHILE the prior profile keeps serving, then atomically flips the "active profile"
  pointer so ``retrieval.search`` reads exactly ONE profile at any instant. Because embeddings are
  keyed by ``(chunk_id, profile_id, version)`` the old and new projections coexist during the build,
  so there is never a window where already-published content returns zero results (no downtime), and
  no query ever mixes old+new-profile embeddings. Re-running it for the prior profile — whose
  embeddings still exist — is therefore an idempotent ROLLBACK: it just re-activates that version.

* ``retrieval.embedding.retire`` — after cutover, delete a SUPERSEDED (non-active) profile version's
  embeddings to reclaim space. Refuses to retire the currently-active profile (fail closed).

Tenant scope is taken from the authenticated :class:`RequestContext`, never the payload (rule 50).
Chunk text/FTS is profile-independent, so a rebuild re-embeds existing chunks; provenance
(source/revision/block, content hash) is preserved unchanged (FR-RET-007).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.errors import RetrievalInvariantViolation, TenantScopeMissing
from ..domain.model import EmbeddingProfileRef
from .ports import EmbeddingPort, RetrievalRepositoryPort

CAP_VERSION = "1.0.0"

CAP_REBUILD_EMBEDDING = "retrieval.embedding.rebuild"
CAP_RETIRE_EMBEDDING = "retrieval.embedding.retire"

REBUILD_CAPABILITIES: tuple[str, ...] = (CAP_REBUILD_EMBEDDING, CAP_RETIRE_EMBEDDING)


@dataclass(frozen=True, slots=True)
class RebuildEmbeddingCommand:
    """Trigger a rebuild-and-cutover to the profile carried by the injected embedding.

    ``activate`` (default ``True``) performs the atomic cutover after the build; set it ``False`` to
    build the new projection and leave the prior profile serving (a staged/shadow rebuild whose
    cutover is a later, separate activation).
    """

    activate: bool = True


@dataclass(frozen=True, slots=True)
class RebuildEmbeddingResult:
    """Evidence of a rebuild-and-cutover: what was built and which profile now serves."""

    target_profile_id: str
    target_profile_version: str
    previous_profile_id: str | None
    previous_profile_version: str | None
    rebuilt_chunks: int
    activated: bool


@dataclass(frozen=True, slots=True)
class RetireEmbeddingCommand:
    """Retire a superseded profile version's embeddings after cutover."""

    profile_id: str
    profile_version: str


@dataclass(frozen=True, slots=True)
class RetireEmbeddingResult:
    profile_id: str
    profile_version: str
    retired: bool = field(default=True)


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


class RebuildEmbedding:
    """``retrieval.embedding.rebuild`` — build a new profile's projection then atomically cut over.

    The build is idempotent per chunk (``add_embedding`` upserts), so a retried or replayed rebuild
    converges; re-running it against an already-built prior profile is a rollback (re-activation).
    """

    def __init__(
        self,
        *,
        repository: RetrievalRepositoryPort,
        embedding: EmbeddingPort,
    ) -> None:
        self._repo = repository
        self._embedding = embedding

    def handle(self, request: object) -> RebuildEmbeddingResult:
        command = _typed(request, RebuildEmbeddingCommand)
        organization_id = _tenant(request)
        target = self._embedding.profile

        previous = self._repo.active_profile()

        # BUILD phase — the prior profile keeps serving throughout (never drop-then-build).
        self._repo.register_profile(target)
        rebuilt = 0
        for chunk in self._repo.all_chunks(organization_id=organization_id):
            vector = self._embedding.embed(chunk.text)
            self._repo.add_embedding(chunk=chunk, vector=vector, profile=target)
            rebuilt += 1

        # CUTOVER phase — one atomic flip so search reads exactly one profile at any instant.
        activated = False
        if command.activate:
            self._repo.activate_profile(target)
            activated = True

        return RebuildEmbeddingResult(
            target_profile_id=target.profile_id,
            target_profile_version=target.version,
            previous_profile_id=previous.profile_id if previous else None,
            previous_profile_version=previous.version if previous else None,
            rebuilt_chunks=rebuilt,
            activated=activated,
        )


class RetireEmbedding:
    """``retrieval.embedding.retire`` — drop a superseded profile version's embeddings post-cutover.

    Fails closed: refuses to retire the profile that is currently active (that would delete the
    serving projection and cause downtime).
    """

    def __init__(self, *, repository: RetrievalRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> RetireEmbeddingResult:
        command = _typed(request, RetireEmbeddingCommand)
        organization_id = _tenant(request)
        ref = EmbeddingProfileRef(profile_id=command.profile_id, version=command.profile_version)
        active = self._repo.active_profile()
        if active is not None and active == ref:
            raise RetrievalInvariantViolation(
                "refusing to retire the currently-active embedding profile",
                code="retrieval.retire.active",
            )
        self._repo.retire_profile(organization_id=organization_id, profile=ref)
        return RetireEmbeddingResult(
            profile_id=command.profile_id, profile_version=command.profile_version
        )
