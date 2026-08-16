"""Structure + citation preserving export adapter (FR-RSH-006).

Serializes the canonical ``research-document`` envelope (produced by the pure
:mod:`..domain.interchange`) to a deterministic open format (canonical JSON with sorted keys) and
parses it back, so an export preserves document structure, citations and stable identifiers and is
round-trippable. The domain does the projection; this adapter only handles the concrete byte
encoding (rule 10) — keeping canonical structure + provenance inside Northstar (docs/37 §8).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..domain.interchange import from_research_document, to_research_document
from ..domain.model import ResearchDocumentBundle


class JsonResearchExporter:
    """Deterministic JSON exporter/importer for the canonical research-document envelope."""

    def export_bundle(self, bundle: ResearchDocumentBundle) -> str:
        """Serialize a bundle to canonical JSON (sorted keys ⇒ byte-stable across runs)."""
        return self.export_document(to_research_document(bundle))

    def export_document(self, document: Mapping[str, Any]) -> str:
        """Serialize an already-projected research-document envelope to canonical JSON."""
        return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def import_document(
        self, payload: str, *, organization_id: str = "imported"
    ) -> ResearchDocumentBundle:
        """Parse canonical JSON back into a bundle (inverse of :meth:`export_bundle`).

        ``organization_id`` is the importing tenant's scope (never trusted from the payload).
        """
        return from_research_document(json.loads(payload), organization_id=organization_id)
