"""Retrieval adapters (infra allowed here, rule 10): FTS/pgvector persistence + local embedding.

Concrete SQLAlchemy repositories (``tsvector`` + pgvector ``vector``) and the deterministic local
reference embedding implement the pure application ports behind the boundary. pgvector is imported
ONLY here (never in the domain), satisfying the architecture gate.
"""

from __future__ import annotations

from .embedding import LOCAL_EMBEDDING_DIMENSIONS, LocalHashEmbedding
from .repositories import InMemoryRetrievalRepository, SqlAlchemyRetrievalRepository
from .tables import (
    RETRIEVAL_SCHEMA,
    RETRIEVAL_TENANT_TABLES,
    RETRIEVAL_VECTOR_DIMENSIONS,
    RetrievalTables,
    build_retrieval_tables,
)

__all__ = [
    "LOCAL_EMBEDDING_DIMENSIONS",
    "RETRIEVAL_SCHEMA",
    "RETRIEVAL_TENANT_TABLES",
    "RETRIEVAL_VECTOR_DIMENSIONS",
    "InMemoryRetrievalRepository",
    "LocalHashEmbedding",
    "RetrievalTables",
    "SqlAlchemyRetrievalRepository",
    "build_retrieval_tables",
]
