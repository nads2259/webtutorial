"""``LocalHashEmbedding`` — the deterministic local reference embedding (FR-RET-003/008, rule 60).

A reproducible, dependency-free embedding so unit/integration tests never depend on an external
model or network. It hashes overlapping word tokens and character trigrams into a fixed-dimension
bag-of-features vector, then L2-normalises it, so:

* the same text always yields the identical vector (determinism — required for reproducible
  ACL-leakage and recall/precision evidence);
* texts sharing tokens/trigrams land near each other under L2/cosine distance (enough signal for a
  small exact-search gold-set baseline).

This is NOT a production semantic model; it is the reference adapter behind :class:`EmbeddingPort`.
A real provider (OpenAI/Cohere/local ST) is a straight adapter swap recording its own profile — no
domain or capability change (FR-RET-008). The hashing uses blake2b (stable across processes), never
Python's salted ``hash()``.
"""

from __future__ import annotations

import hashlib
import re

from ..domain.model import DISTANCE_L2, EmbeddingProfile
from ..domain.vectors import normalize

LOCAL_EMBEDDING_DIMENSIONS = 256

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _features(text: str) -> list[str]:
    """Deterministic feature set: lowercased word tokens plus character trigrams of each token."""
    features: list[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        features.append(f"w:{token}")
        padded = f"^{token}$"
        for i in range(len(padded) - 2):
            features.append(f"t:{padded[i : i + 3]}")
    return features


def _bucket_and_sign(feature: str, dimensions: int, seed: int) -> tuple[int, float]:
    """Map a feature to a (bucket, +/-1) pair via a stable, seeded hash (signed hashing trick).

    ``seed`` is mixed into the (deterministic, salt-free) blake2b digest so a NEW embedding profile
    version can be produced from the SAME text with a genuinely different vector space, without any
    external model — the seam an embedding rebuild-and-cutover drill needs (FR-RET-004).
    """
    salted = f"{seed}:{feature}" if seed else feature
    digest = hashlib.blake2b(salted.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    bucket = value % dimensions
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return bucket, sign


class LocalHashEmbedding:
    """Deterministic hashed-feature embedding behind :class:`EmbeddingPort` (reference adapter).

    ``seed`` (with a distinct profile ``version``) yields a second, reproducible embedding space
    over the same dimensions so a rebuild can build NEW versioned embeddings alongside the old ones
    and search can atomically cut over between them (FR-RET-004). It is still NOT a semantic model.
    """

    def __init__(
        self,
        *,
        dimensions: int = LOCAL_EMBEDDING_DIMENSIONS,
        profile_id: str = "local-hash-ngram",
        version: str = "1.0.0",
        seed: int = 0,
    ) -> None:
        self._seed = seed
        self._profile = EmbeddingProfile(
            profile_id=profile_id,
            version=version,
            provider="northstar-local",
            model="hashed-ngram",
            dimensions=dimensions,
            distance_metric=DISTANCE_L2,
            chunker_version="block-1.0.0",
        )

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    def embed(self, text: str) -> tuple[float, ...]:
        dimensions = self._profile.dimensions
        vector = [0.0] * dimensions
        for feature in _features(text):
            bucket, sign = _bucket_and_sign(feature, dimensions, self._seed)
            vector[bucket] += sign
        return normalize(vector)
