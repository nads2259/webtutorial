"""Canonical research-document interchange (export/import) — structure + citations (FR-RSH-006).

Pure, deterministic projection between a :class:`ResearchDocumentBundle` and the canonical
``research-document.schema.json`` envelope. Exports preserve document STRUCTURE (the typed block
tree) and CITATIONS (evidence records projected as the envelope's ``citations`` array, with each
claim's ``evidence_refs`` pointing at them) plus provenance and stable identifiers, and the pair is
round-trippable: ``from_research_document(to_research_document(bundle)) == bundle``.

Infrastructure-free (rule 10): only stdlib + the shared knowledge block model are used.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from northstar.modules.knowledge.domain.blocks import ContentTree

from .errors import ResearchInvariantViolation
from .model import (
    Claim,
    DocumentStatus,
    EvidenceKind,
    EvidenceRecord,
    ResearchDocumentBundle,
)


def _content_block_to_document_block(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a ``content-block`` shape back to the ``content-document`` block shape (parsing)."""
    children = raw.get("children") or []
    return {
        "id": raw["block_id"],
        "type": raw["block_type"],
        "version": 1,
        "data": {"attributes": raw.get("attributes") or {}, "content": raw.get("content")},
        "children": [_content_block_to_document_block(child) for child in children],
    }


def _evidence_to_citation(evidence: EvidenceRecord) -> dict[str, Any]:
    """Project one evidence record to a schema ``citations`` entry (citation == cited evidence)."""
    return {
        "citation_id": evidence.evidence_id,
        "source": {
            "kind": evidence.kind.value,
            "excerpt": evidence.excerpt,
            "object_id": evidence.object_id,
            "revision_id": evidence.revision_id,
            "block_id": evidence.block_id,
            "chunk_id": evidence.chunk_id,
            "source_uri": evidence.source_uri,
            "version_hash": evidence.version_hash,
        },
        "locator": evidence.chunk_id or evidence.block_id,
        "verified": evidence.verified,
    }


def _claim_to_dict(claim: Claim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "statement": claim.statement,
        "evidence_refs": list(claim.evidence_ids),
        "confidence": claim.confidence,
    }


def to_research_document(bundle: ResearchDocumentBundle) -> dict[str, Any]:
    """Project a bundle to the canonical ``research-document`` envelope (deterministic)."""
    created_at = bundle.created_at or datetime(1970, 1, 1, tzinfo=UTC)
    return {
        "document_id": bundle.document_id,
        "revision_id": bundle.revision_id,
        "title": bundle.title,
        "status": bundle.status.value,
        "blocks": bundle.tree.to_content_blocks(),
        "claims": [_claim_to_dict(claim) for claim in bundle.claims],
        "citations": [_evidence_to_citation(evidence) for evidence in bundle.evidence],
        "datasets": [dataset.dataset_ref_id for dataset in bundle.datasets],
        "run_refs": [],
        "provenance": {
            "created_by": bundle.created_by,
            "created_at": created_at.isoformat(),
            "ai_contributions": list(bundle.ai_contributions),
        },
    }


def from_research_document(
    raw: Mapping[str, Any], *, organization_id: str = "imported"
) -> ResearchDocumentBundle:
    """Parse a canonical ``research-document`` envelope back into a bundle (inverse of export).

    ``organization_id`` is supplied by the importing tenant context (the schema envelope carries no
    tenant field, by design — tenant scope is never trusted from a payload, rule 50).
    """
    if not isinstance(raw, Mapping):
        raise ResearchInvariantViolation(
            "research document must be an object", code="research.interchange.shape"
        )
    document_id = str(raw["document_id"])
    revision_id = str(raw["revision_id"])
    provenance = raw.get("provenance") or {}
    created_by = str(provenance.get("created_by", ""))
    created_at = _parse_dt(provenance.get("created_at"))

    raw_blocks = raw.get("blocks") or []
    if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)):
        raise ResearchInvariantViolation(
            "blocks must be an array", code="research.interchange.blocks"
        )
    tree = ContentTree.from_document_blocks(
        [_content_block_to_document_block(block) for block in raw_blocks]
    )

    evidence = tuple(
        _citation_to_evidence(
            citation,
            organization_id=organization_id,
            document_id=document_id,
            created_at=created_at,
        )
        for citation in raw.get("citations") or []
    )
    claims = tuple(
        _dict_to_claim(
            claim,
            organization_id=organization_id,
            document_id=document_id,
            created_by=created_by,
            created_at=created_at,
        )
        for claim in raw.get("claims") or []
    )
    return ResearchDocumentBundle(
        document_id=document_id,
        revision_id=revision_id,
        title=str(raw["title"]),
        status=DocumentStatus(str(raw["status"])),
        tree=tree,
        claims=claims,
        evidence=evidence,
        datasets=(),
        created_by=created_by,
        created_at=created_at,
        ai_contributions=tuple(str(c) for c in provenance.get("ai_contributions") or ()),
    )


def _citation_to_evidence(
    citation: Mapping[str, Any],
    *,
    organization_id: str,
    document_id: str,
    created_at: datetime,
) -> EvidenceRecord:
    source = citation.get("source") or {}
    return EvidenceRecord(
        evidence_id=str(citation["citation_id"]),
        organization_id=organization_id,
        document_id=document_id,
        kind=EvidenceKind(str(source.get("kind", EvidenceKind.CITATION.value))),
        excerpt=str(source.get("excerpt", "")),
        version_hash=str(source.get("version_hash", "")),
        created_at=created_at,
        object_id=_opt(source.get("object_id")),
        revision_id=_opt(source.get("revision_id")),
        block_id=_opt(source.get("block_id")),
        chunk_id=_opt(source.get("chunk_id")),
        source_uri=_opt(source.get("source_uri")),
        verified=bool(citation.get("verified", False)),
    )


def _dict_to_claim(
    claim: Mapping[str, Any],
    *,
    organization_id: str,
    document_id: str,
    created_by: str,
    created_at: datetime,
) -> Claim:
    confidence = claim.get("confidence")
    return Claim(
        claim_id=str(claim["claim_id"]),
        organization_id=organization_id,
        document_id=document_id,
        statement=str(claim["statement"]),
        evidence_ids=tuple(str(ref) for ref in claim.get("evidence_refs") or ()),
        created_by=created_by,
        created_at=created_at,
        confidence=float(confidence) if confidence is not None else None,
    )


def _opt(value: object) -> str | None:
    return str(value) if value is not None else None


def _parse_dt(value: object) -> datetime:
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime(1970, 1, 1, tzinfo=UTC)
