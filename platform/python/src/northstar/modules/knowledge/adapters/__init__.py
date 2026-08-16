"""Knowledge adapters (infra allowed here, rule 10): persistence, projection, object storage.

Concrete SQLAlchemy repositories, a strict-allowlist HTML/Markdown projector and the object-storage
reference adapters implement the pure application ports behind the boundary.
"""

from __future__ import annotations

from .projector import project_html, project_markdown
from .repositories import (
    InMemoryKnowledgeRepository,
    SqlAlchemyKnowledgeRepository,
)
from .tables import KNOWLEDGE_SCHEMA, KnowledgeTables, build_knowledge_tables

__all__ = [
    "KNOWLEDGE_SCHEMA",
    "InMemoryKnowledgeRepository",
    "KnowledgeTables",
    "SqlAlchemyKnowledgeRepository",
    "build_knowledge_tables",
    "project_html",
    "project_markdown",
]
