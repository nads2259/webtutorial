"""Consent + entitlement seams for recommendations (FR-LRN-007, LAW-19).

Recommendations require the learner's consent and respect entitlements. These reference adapters are
deliberately small and swappable:

* :class:`InMemoryConsentStore` records per-learner personalization consent (deny-by-default: a
  learner who has not opted in has no consent).
* :class:`InMemoryEntitlementDirectory` records which courses a learner is entitled to; unknown
  learners are entitled to non-gated courses only. A real deployment swaps in an adapter backed by
  the entitlement engine behind the SAME :class:`EntitlementPort` (LAW-19: never branch on plan
  names).
"""

from __future__ import annotations


class InMemoryConsentStore:
    """Deny-by-default personalization consent store (FR-LRN-007)."""

    def __init__(self) -> None:
        self._consented: set[tuple[str, str]] = set()

    def grant(self, *, organization_id: str, subject_id: str) -> None:
        self._consented.add((organization_id, subject_id))

    def revoke(self, *, organization_id: str, subject_id: str) -> None:
        self._consented.discard((organization_id, subject_id))

    def has_personalization_consent(self, *, organization_id: str, subject_id: str) -> bool:
        return (organization_id, subject_id) in self._consented


class InMemoryEntitlementDirectory:
    """Reference entitlement seam: gate specific courses; everything else is free/entitled."""

    def __init__(self, *, gated_courses: frozenset[str] = frozenset()) -> None:
        self._gated = set(gated_courses)
        self._grants: set[tuple[str, str, str]] = set()

    def gate(self, *, course_id: str) -> None:
        self._gated.add(course_id)

    def grant(self, *, organization_id: str, subject_id: str, course_id: str) -> None:
        self._grants.add((organization_id, subject_id, course_id))

    def is_entitled_to_course(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> bool:
        if course_id not in self._gated:
            return True  # non-gated (free) content is entitled to everyone
        return (organization_id, subject_id, course_id) in self._grants


__all__ = ["InMemoryConsentStore", "InMemoryEntitlementDirectory"]
