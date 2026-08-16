"""Ports (abstractions) for the learning application layer (rule 10/20, DIP).

Five seams keep the capabilities infrastructure-free and hold no ambient authority (rule 50):

* :class:`LearningRepositoryPort` — the module's OWN tenant-scoped persistence for the hierarchy,
  progress, overlays, assessment items/attempts, completion rules, credentials and inferred profiles
  (LAW-13). Progress lives here, NOT in analytics (FR-LRN-002).
* :class:`PublishedContentPort` — a READ-ONLY seam onto the knowledge module used to confirm a
  composed revision is PUBLISHED and to resolve its stable block ids (FR-LRN-001). No cross-module
  writes (LAW-13).
* :class:`ConsentPort` / :class:`EntitlementPort` — the seams a recommendation honours; personalized
  recommendations require consent, and gated courses require an entitlement (FR-LRN-007).
* :class:`AiTutorPort` — the seam onto the SINGLE governed ``ai.answer`` pipeline (LAW-09). The
  tutor runs as a scoped AI actor; the answer key is never in its context, so it cannot leak one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain.model import (
    AnonymousProgress,
    AssessmentItem,
    Attempt,
    CompletionRule,
    Course,
    Credential,
    Domain,
    InferredProfile,
    LearningPath,
    Overlay,
    ProgressRecord,
)


@dataclass(frozen=True, slots=True)
class PublishedRevision:
    """A confirmed-PUBLISHED knowledge revision + its stable block ids (FR-LRN-001)."""

    object_id: str
    revision_id: str
    block_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TutorCitation:
    """A citation carried through from the governed ``ai.answer`` pipeline."""

    object_id: str
    revision_id: str
    block_id: str
    chunk_id: str
    claim: str


@dataclass(frozen=True, slots=True)
class TutorAnswer:
    """The tutor answer surfaced from the governed ``ai.answer`` pipeline (locale-preserving)."""

    answer: str
    refused: bool
    locale: str
    citations: tuple[TutorCitation, ...] = field(default_factory=tuple)
    trace_id: str = ""
    provider: str = ""
    model: str = ""


@runtime_checkable
class PublishedContentPort(Protocol):
    """Confirms a composed revision is PUBLISHED and resolves its block ids (FR-LRN-001)."""

    def published_revision(
        self, *, organization_id: str, object_id: str, revision_id: str
    ) -> PublishedRevision | None:
        """Return the published revision (with block ids) or ``None`` if it is not published."""
        ...


@runtime_checkable
class ConsentPort(Protocol):
    """Whether the learner has consented to personalized learning recommendations (FR-LRN-007)."""

    def has_personalization_consent(self, *, organization_id: str, subject_id: str) -> bool: ...


@runtime_checkable
class EntitlementPort(Protocol):
    """Whether the learner is entitled to a (possibly gated) course (FR-LRN-007, LAW-19)."""

    def is_entitled_to_course(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> bool: ...


@runtime_checkable
class AiTutorPort(Protocol):
    """The seam onto the ONE governed ``ai.answer`` pipeline (LAW-09, multilingual EVAL-AI-011)."""

    def ask(
        self,
        *,
        organization_id: str,
        subject_id: str,
        correlation_id: str,
        question: str,
        locale: str,
        package_id: str,
        version: str,
    ) -> TutorAnswer: ...


@runtime_checkable
class LearningRepositoryPort(Protocol):
    """Persists/reads the learning module's OWN tenant-scoped data (rule 50, LAW-13)."""

    # Hierarchy ----------------------------------------------------------
    def add_domain(self, *, organization_id: str, domain: Domain) -> None: ...

    def add_path(self, *, organization_id: str, path: LearningPath) -> None: ...

    def upsert_course(
        self, *, organization_id: str, course: Course, published: bool = False
    ) -> None: ...

    def get_course(self, *, organization_id: str, course_id: str) -> Course | None: ...

    def is_course_published(self, *, organization_id: str, course_id: str) -> bool: ...

    def list_courses(self, *, organization_id: str) -> Sequence[Course]: ...

    # Progress (own state, not analytics-derived) ------------------------
    def save_progress(self, *, organization_id: str, progress: ProgressRecord) -> None: ...

    def get_progress(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> ProgressRecord | None: ...

    # Anonymous progress (device/session scoped, merged on sign-in — UX-010) ---
    def save_anonymous_progress(
        self, *, organization_id: str, anonymous: AnonymousProgress
    ) -> None: ...

    def get_anonymous_progress(
        self, *, organization_id: str, anonymous_id: str, course_id: str
    ) -> AnonymousProgress | None: ...

    def list_anonymous_progress(
        self, *, organization_id: str, anonymous_id: str
    ) -> Sequence[AnonymousProgress]: ...

    def claim_anonymous_progress(
        self, *, organization_id: str, anonymous_id: str, course_id: str, subject_id: str
    ) -> None: ...

    # Personal overlay (private to learner) ------------------------------
    def add_overlay(self, *, organization_id: str, overlay: Overlay) -> None: ...

    def list_overlays(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> Sequence[Overlay]: ...

    # Assessment ---------------------------------------------------------
    def get_item(
        self, *, organization_id: str, item_id: str, version: str
    ) -> AssessmentItem | None: ...

    def is_item_sealed(self, *, organization_id: str, item_id: str, version: str) -> bool: ...

    def upsert_item(self, *, organization_id: str, item: AssessmentItem, sealed: bool) -> None: ...

    def seal_item(self, *, organization_id: str, item_id: str, version: str) -> None: ...

    def add_attempt(self, *, organization_id: str, attempt: Attempt) -> None: ...

    def count_attempts(self, *, organization_id: str, subject_id: str, item_id: str) -> int: ...

    def passed_attempts(self, *, organization_id: str, subject_id: str) -> dict[str, Attempt]: ...

    # Completion + credential -------------------------------------------
    def add_rule(self, *, organization_id: str, rule: CompletionRule) -> None: ...

    def get_rule(self, *, organization_id: str, rule_id: str) -> CompletionRule | None: ...

    def add_credential(self, *, organization_id: str, credential: Credential) -> None: ...

    def get_credential(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> Credential | None: ...

    # Inferred profile transparency -------------------------------------
    def get_profile(self, *, organization_id: str, subject_id: str) -> InferredProfile: ...

    def save_profile(self, *, organization_id: str, profile: InferredProfile) -> None: ...


__all__ = [
    "AiTutorPort",
    "ConsentPort",
    "EntitlementPort",
    "LearningRepositoryPort",
    "PublishedContentPort",
    "PublishedRevision",
    "TutorAnswer",
    "TutorCitation",
]
