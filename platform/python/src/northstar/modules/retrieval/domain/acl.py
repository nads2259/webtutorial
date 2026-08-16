"""The ACL predicate model — retrieval's authoritative disclosure rule (FR-RET-006, rule 50/60).

Access control is applied INSIDE the retrieval query (the repository translates this predicate to
a ``WHERE`` clause and the PostgreSQL RLS tenant GUC) AND re-checked here before any passage is
returned to a caller or model. Post-filtering alone is unsafe (docs/06 §7), so this pure predicate
is the single source of truth both layers agree on.

Rule (deny-by-default): a passage is disclosable only when it is in the caller's tenant AND either
its visibility is public/organization, or it is private and owned by the caller. A caller in tenant
A can therefore never receive a tenant-B passage or another subject's private passage.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Candidate, Chunk, Visibility


@dataclass(frozen=True, slots=True)
class AclPredicate:
    """The caller's retrieval authorization context, derived from the authenticated request.

    ``organization_id`` is the tenant scope (never taken from a request payload, rule 50) and
    ``subject_id`` is the acting subject, used to authorize that subject's own private passages.
    """

    organization_id: str
    subject_id: str

    def permits_attributes(
        self, *, organization_id: str, visibility: Visibility, owner_id: str | None
    ) -> bool:
        """Return ``True`` iff a passage with these ACL attributes is disclosable to this caller."""
        if organization_id != self.organization_id:
            return False
        if visibility is Visibility.PRIVATE:
            return owner_id is not None and owner_id == self.subject_id
        return True

    def permits(self, candidate: Candidate) -> bool:
        """Return ``True`` iff ``candidate`` is disclosable to this caller (re-check helper)."""
        return self.permits_attributes(
            organization_id=candidate.organization_id,
            visibility=candidate.visibility,
            owner_id=candidate.owner_id,
        )

    def permits_chunk(self, chunk: Chunk) -> bool:
        """Return ``True`` iff ``chunk`` is disclosable to this caller."""
        return self.permits_attributes(
            organization_id=chunk.organization_id,
            visibility=chunk.visibility,
            owner_id=chunk.owner_id,
        )
