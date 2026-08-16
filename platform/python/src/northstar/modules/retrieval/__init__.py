"""Northstar retrieval module: hybrid FTS + pgvector search over PUBLISHED knowledge.

A hexagonal module (LAW-02/04/10/12/13). The pure :mod:`.domain` holds the retrieval value
objects (query/result with stable source/revision/block identity, embedding profile), the pure
reciprocal-rank fusion and the ACL predicate model. The :mod:`.application` layer exposes the two
authoritative capabilities — ``retrieval.revision.index`` (build FTS + chunk + embedding
projections when content is published) and ``retrieval.search`` (hybrid FTS+vector, ACL-filtered
INSIDE the query and re-checked before disclosure, fused). Infrastructure (SQLAlchemy ``tsvector``
+ pgvector ``vector`` columns, the deterministic local reference embedding) lives only in
:mod:`.adapters` behind ports (FR-RET-001..003/005/006/007).
"""
