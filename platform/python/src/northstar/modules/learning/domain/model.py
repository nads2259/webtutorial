"""Learning & assessment domain model (docs/04 §5, FR-LRN-001..007). Pure and infra-free.

Every type here enforces the learning invariants by construction and never learns about
infrastructure (rule 10, LAW-02):

* the hierarchy is ``Domain -> LearningPath -> Course -> Section`` and a :class:`Section` references
  a PUBLISHED knowledge revision + stable block ids (the course composes revisions, it never copies
  their bodies — FR-LRN-001);
* :class:`ProgressRecord` carries a stable :class:`Position` resume point and is the module's OWN
  state (never derived from analytics events — FR-LRN-002);
* an :class:`Overlay` (bookmark/note/highlight) is anchored at a stable position and is private to
  its learner (FR-LRN-003);
* an :class:`AssessmentItem` is versioned and content-hashed; :func:`score_attempt` is a
  DETERMINISTIC pure scorer (FR-LRN-004);
* :func:`evaluate_completion` derives completion from EXPLICIT rules over auditable evidence, and a
  :class:`Credential` records the rule + evidence and a deterministic verification hash (LRN-005);
* a :class:`Recommendation` always carries an :class:`Explanation` and any inferred difficulty is an
  :class:`InferredSignal` flagged non-authoritative (FR-LRN-007);
* an :class:`InferredProfile` exposes its feature inventory and supports correct/reset transparency
  (EVAL-PRIV-004).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .errors import (
    LearningValidationError,
    PositionNotInCourse,
)

# Stable resource vocabulary (contract): learning surfaces are tenant-scoped resources.
RES_LEARNING = "learning.catalog"

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")


def _require(condition: bool, message: str, code: str = "learning.invalid") -> None:
    if not condition:
        raise LearningValidationError(message, code=code)


# ---------------------------------------------------------------------------
# Hierarchy (FR-LRN-001)
# ---------------------------------------------------------------------------


class Modality(StrEnum):
    """How a learner progresses through a course (FR-LRN-006)."""

    GUIDED = "guided"  # path-sequenced: the next section follows the course order
    SELF_DIRECTED = "self_directed"  # free reading: any section may be visited in any order


@dataclass(frozen=True, slots=True)
class Domain:
    """A top-level learning domain (docs/04 §5 Learning Design)."""

    domain_id: str
    title: str
    slug: str

    def __post_init__(self) -> None:
        _require(bool(self.domain_id), "domain_id must be non-empty", code="learning.domain.id")
        _require(
            bool(self.title.strip()), "domain title must be non-empty", code="learning.domain.title"
        )


@dataclass(frozen=True, slots=True)
class Section:
    """An ordered course section that composes ONE published knowledge revision (FR-LRN-001).

    ``block_ids`` are the stable knowledge block ids this section surfaces; the compose capability
    validates each id exists in the referenced published revision. The section stores references,
    never content bodies (docs/04 §5 invariant).
    """

    section_id: str
    title: str
    object_id: str
    revision_id: str
    block_ids: tuple[str, ...]
    ordinal: int

    def __post_init__(self) -> None:
        _require(bool(self.section_id), "section_id must be non-empty", code="learning.section.id")
        _require(
            bool(self.revision_id),
            "section must reference a revision_id",
            code="learning.section.rev",
        )
        _require(
            len(self.block_ids) >= 1,
            "section must reference >=1 block",
            code="learning.section.blocks",
        )
        _require(self.ordinal >= 0, "section ordinal must be >= 0", code="learning.section.ordinal")


@dataclass(frozen=True, slots=True)
class Course:
    """A course that composes PUBLISHED knowledge revisions into ordered sections (FR-LRN-001)."""

    course_id: str
    domain_id: str
    title: str
    sections: tuple[Section, ...]
    path_id: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.course_id), "course_id must be non-empty", code="learning.course.id")
        _require(
            bool(self.title.strip()), "course title must be non-empty", code="learning.course.title"
        )
        _require(
            len(self.sections) >= 1,
            "a course must have >=1 section",
            code="learning.course.sections",
        )
        ids = [s.section_id for s in self.sections]
        _require(
            len(ids) == len(set(ids)), "section ids must be unique", code="learning.course.dup"
        )

    @property
    def ordered_sections(self) -> tuple[Section, ...]:
        """Sections in guided (path-sequenced) order (FR-LRN-006)."""
        return tuple(sorted(self.sections, key=lambda s: s.ordinal))

    def section(self, section_id: str) -> Section | None:
        return next((s for s in self.sections if s.section_id == section_id), None)

    def contains(self, position: Position) -> bool:
        """Return whether ``position`` resolves to a real section + block of this course."""
        if position.course_id != self.course_id:
            return False
        section = self.section(position.section_id)
        return section is not None and position.block_id in section.block_ids

    def next_section(self, section_id: str) -> Section | None:
        """The next section in guided order after ``section_id`` (``None`` at the end)."""
        ordered = self.ordered_sections
        for index, section in enumerate(ordered):
            if section.section_id == section_id:
                return ordered[index + 1] if index + 1 < len(ordered) else None
        return None

    def rank(self, position: Position) -> tuple[int, int]:
        """A total-order rank of ``position`` along the course (``(section ordinal, block index)``).

        A higher rank is FURTHER through the course; a position that does not resolve to a real
        section/block ranks ``(-1, -1)`` (before the start). Used by :func:`merge_progress` to pick
        the furthest resume point deterministically (FR-LRN-002, UX-010).
        """
        section = self.section(position.section_id)
        if section is None or position.block_id not in section.block_ids:
            return (-1, -1)
        return (section.ordinal, section.block_ids.index(position.block_id))

    def start_position(self) -> Position:
        first = self.ordered_sections[0]
        return Position(
            course_id=self.course_id, section_id=first.section_id, block_id=first.block_ids[0]
        )


@dataclass(frozen=True, slots=True)
class LearningPath:
    """A guided sequence of courses in a domain (FR-LRN-001/006)."""

    path_id: str
    domain_id: str
    title: str
    course_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(bool(self.path_id), "path_id must be non-empty", code="learning.path.id")
        _require(bool(self.domain_id), "path must reference a domain", code="learning.path.domain")


# ---------------------------------------------------------------------------
# Progress + resume (FR-LRN-002) — own state, never analytics-derived
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Position:
    """A STABLE section/block reading position (the resume anchor, FR-LRN-002/003)."""

    course_id: str
    section_id: str
    block_id: str

    def __post_init__(self) -> None:
        _require(
            bool(self.course_id),
            "position must reference a course",
            code="learning.position.course",
        )
        _require(
            bool(self.section_id),
            "position must reference a section",
            code="learning.position.section",
        )
        _require(
            bool(self.block_id), "position must reference a block", code="learning.position.block"
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "course_id": self.course_id,
            "section_id": self.section_id,
            "block_id": self.block_id,
        }


@dataclass(frozen=True, slots=True)
class ProgressRecord:
    """A learner's progress in a course — the module's OWN durable state (FR-LRN-002).

    ``resume`` is a stable :class:`Position` restored across sessions; ``completed_sections`` is the
    audited evidence set completion rules read. This is NEVER derived from analytics events.
    """

    subject_id: str
    course_id: str
    resume: Position
    modality: Modality
    completed_sections: frozenset[str] = field(default_factory=frozenset)
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _require(
            bool(self.subject_id),
            "progress must reference a subject",
            code="learning.progress.subject",
        )
        _require(
            self.resume.course_id == self.course_id,
            "resume position must belong to this course",
            code="learning.progress.resume",
        )

    def advanced(
        self, *, resume: Position, completed_section_id: str | None, now: datetime
    ) -> ProgressRecord:
        completed = set(self.completed_sections)
        if completed_section_id:
            completed.add(completed_section_id)
        return ProgressRecord(
            subject_id=self.subject_id,
            course_id=self.course_id,
            resume=resume,
            modality=self.modality,
            completed_sections=frozenset(completed),
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class AnonymousProgress:
    """Progress captured under an anonymous device/session id, tenant-scoped (FR-LRN-002, UX-010).

    Identical in shape to :class:`ProgressRecord` but keyed by an opaque ``anonymous_id`` (the
    anonymous session/device identity) instead of an authenticated subject. ``claimed_by`` records
    the authenticated subject that has merged this record into their account; once claimed it may
    NEVER be re-claimed by a different subject (cross-owner refusal, LAW-08). This is the module's
    OWN state, never derived from analytics.
    """

    anonymous_id: str
    course_id: str
    resume: Position
    modality: Modality
    completed_sections: frozenset[str] = field(default_factory=frozenset)
    updated_at: datetime | None = None
    claimed_by: str | None = None

    def __post_init__(self) -> None:
        _require(
            bool(self.anonymous_id),
            "anonymous progress must reference an anonymous id",
            code="learning.progress.anonymous",
        )
        _require(
            self.resume.course_id == self.course_id,
            "resume position must belong to this course",
            code="learning.progress.resume",
        )

    def as_progress_of(self, subject_id: str) -> ProgressRecord:
        """Project this anonymous record as a :class:`ProgressRecord` owned by ``subject_id``."""
        return ProgressRecord(
            subject_id=subject_id,
            course_id=self.course_id,
            resume=self.resume,
            modality=self.modality,
            completed_sections=self.completed_sections,
            updated_at=self.updated_at,
        )


def merge_progress(
    *,
    course: Course,
    subject_id: str,
    sources: Sequence[ProgressRecord],
    now: datetime,
) -> ProgressRecord:
    """Merge one or more progress ``sources`` for the same course into ``subject_id`` (UX-010).

    Pure and side-effect-free. The merge is:

    * **no-loss / most-complete** — ``completed_sections`` is the UNION of every source's completed
      sections, so no progress is ever dropped;
    * **furthest-wins** — the resume position is the FURTHEST position across all sources by
      :meth:`Course.rank`, with a deterministic ``(section_id, block_id)`` tie-break;
    * **deterministic** — the result depends only on the set of sources, never their order;
    * **idempotent** — re-running with the already-merged record among the sources yields the same
      completed-set and resume position (a stable fixed point);
    * **cross-device coherent** — two anonymous sources (different devices) union into one coherent
      record with no duplicate section.

    ``sources`` must be non-empty and every source must belong to ``course``.
    """
    records = list(sources)
    _require(
        bool(records), "a progress merge requires at least one source", code="learning.merge.empty"
    )
    for record in records:
        _require(
            record.course_id == course.course_id,
            "every merge source must belong to the merged course",
            code="learning.merge.course",
        )
    completed: frozenset[str] = frozenset().union(*(r.completed_sections for r in records))

    def _key(record: ProgressRecord) -> tuple[int, int, str, str]:
        section_ordinal, block_index = course.rank(record.resume)
        return (section_ordinal, block_index, record.resume.section_id, record.resume.block_id)

    furthest = max(records, key=_key)
    return ProgressRecord(
        subject_id=subject_id,
        course_id=course.course_id,
        resume=furthest.resume,
        modality=furthest.modality,
        completed_sections=completed,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Personal overlay (FR-LRN-003) — private to learner + tenant
# ---------------------------------------------------------------------------


class OverlayKind(StrEnum):
    """A personal overlay annotation kind (FR-LRN-003)."""

    BOOKMARK = "bookmark"
    NOTE = "note"
    HIGHLIGHT = "highlight"


@dataclass(frozen=True, slots=True)
class Overlay:
    """A learner's private bookmark/note/highlight anchored at a stable position (FR-LRN-003)."""

    overlay_id: str
    subject_id: str
    position: Position
    kind: OverlayKind
    body: str = ""
    quote: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        _require(bool(self.overlay_id), "overlay_id must be non-empty", code="learning.overlay.id")
        _require(
            bool(self.subject_id),
            "overlay must reference a subject",
            code="learning.overlay.subject",
        )
        if self.kind is OverlayKind.NOTE:
            _require(
                bool(self.body.strip()),
                "a note overlay must carry a body",
                code="learning.overlay.body",
            )


# ---------------------------------------------------------------------------
# Assessment (FR-LRN-004) — versioned items, deterministic scoring, immutability
# ---------------------------------------------------------------------------


class ItemKind(StrEnum):
    """Assessment item kind (deterministic scoring shape)."""

    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    SHORT_TEXT = "short_text"


@dataclass(frozen=True, slots=True)
class AssessmentItem:
    """A versioned assessment item (FR-LRN-004). Content-hashed for immutability enforcement.

    ``answer_key`` is the correct answer(s); it is NEVER surfaced to a learner or an AI tutor. The
    ``content_hash`` binds the prompt/choices/answer/points so a sealed version cannot be rewritten
    with different content.
    """

    item_id: str
    version: str
    kind: ItemKind
    prompt: str
    answer_key: tuple[str, ...]
    choices: tuple[str, ...] = ()
    points: int = 1
    pass_ratio: float = 1.0
    max_attempts: int = 3
    accommodations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(bool(self.item_id), "item_id must be non-empty", code="learning.item.id")
        _require(
            bool(_SEMVER.match(self.version)),
            "item version must be semantic",
            code="learning.item.version",
        )
        _require(bool(self.prompt.strip()), "item must carry a prompt", code="learning.item.prompt")
        _require(
            len(self.answer_key) >= 1,
            "item must declare an answer key",
            code="learning.item.answer",
        )
        _require(self.points >= 1, "item points must be >= 1", code="learning.item.points")
        _require(
            0.0 < self.pass_ratio <= 1.0, "pass_ratio must be in (0,1]", code="learning.item.pass"
        )
        _require(self.max_attempts >= 1, "max_attempts must be >= 1", code="learning.item.attempts")
        if self.kind in (ItemKind.SINGLE_CHOICE, ItemKind.MULTI_CHOICE):
            _require(
                len(self.choices) >= 2,
                "a choice item needs >=2 choices",
                code="learning.item.choices",
            )

    def content_hash(self) -> str:
        """Deterministic hash binding the item's scored content (immutability, FR-LRN-004)."""
        canonical = json.dumps(
            {
                "kind": self.kind.value,
                "prompt": self.prompt,
                "answer_key": sorted(self.answer_key),
                "choices": list(self.choices),
                "points": self.points,
                "pass_ratio": self.pass_ratio,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

    def public_view(self) -> dict[str, object]:
        """The learner-visible projection — WITHOUT the answer key (never disclosed)."""
        return {
            "item_id": self.item_id,
            "version": self.version,
            "kind": self.kind.value,
            "prompt": self.prompt,
            "choices": list(self.choices),
            "points": self.points,
            "accommodations": list(self.accommodations),
        }


@dataclass(frozen=True, slots=True)
class Score:
    """A deterministic assessment score with feedback (FR-LRN-004)."""

    raw: int
    max: int
    passed: bool
    feedback: str

    @property
    def ratio(self) -> float:
        return self.raw / self.max if self.max else 0.0


def _normalize(value: str) -> str:
    return value.strip().casefold()


def score_attempt(item: AssessmentItem, responses: tuple[str, ...]) -> Score:
    """DETERMINISTIC scorer: identical (item, responses) always yields an identical score.

    Pure and side-effect-free (FR-LRN-004). The answer key is compared structurally; short-text is
    matched case-insensitively after trimming. Feedback is deterministic and never echoes the key.
    """
    if item.kind is ItemKind.SINGLE_CHOICE:
        correct = len(responses) == 1 and _normalize(responses[0]) == _normalize(item.answer_key[0])
    elif item.kind is ItemKind.MULTI_CHOICE:
        correct = {_normalize(r) for r in responses} == {_normalize(k) for k in item.answer_key}
    else:  # SHORT_TEXT: any accepted answer matches
        accepted = {_normalize(k) for k in item.answer_key}
        correct = any(_normalize(r) in accepted for r in responses)
    raw = item.points if correct else 0
    passed = (raw / item.points) >= item.pass_ratio if item.points else False
    feedback = "Correct." if correct else "Not correct yet — review the section and try again."
    return Score(raw=raw, max=item.points, passed=passed, feedback=feedback)


@dataclass(frozen=True, slots=True)
class Attempt:
    """A scored assessment attempt (auditable completion evidence, FR-LRN-004/005)."""

    attempt_id: str
    item_id: str
    item_version: str
    subject_id: str
    responses: tuple[str, ...]
    score: Score
    accommodations: tuple[str, ...] = ()
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        _require(bool(self.attempt_id), "attempt_id must be non-empty", code="learning.attempt.id")
        _require(
            bool(self.subject_id),
            "attempt must reference a subject",
            code="learning.attempt.subject",
        )


# ---------------------------------------------------------------------------
# Completion + credential (FR-LRN-005) — explicit rules over auditable evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompletionRule:
    """An EXPLICIT, auditable completion rule (FR-LRN-005). Never an analytics heuristic."""

    rule_id: str
    course_id: str
    required_section_ids: tuple[str, ...] = ()
    required_item_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(bool(self.rule_id), "rule_id must be non-empty", code="learning.rule.id")
        _require(
            bool(self.required_section_ids) or bool(self.required_item_ids),
            "a completion rule must require at least one section or item",
            code="learning.rule.empty",
        )


@dataclass(frozen=True, slots=True)
class Evidence:
    """A single auditable evidence record a credential was derived from (FR-LRN-005)."""

    kind: str  # "section" | "attempt"
    ref_id: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "ref_id": self.ref_id, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class CompletionOutcome:
    """The result of evaluating a completion rule (satisfied? + the evidence it read)."""

    satisfied: bool
    evidence: tuple[Evidence, ...]
    missing: tuple[str, ...]


def evaluate_completion(
    rule: CompletionRule,
    *,
    completed_sections: frozenset[str],
    passed_attempts: dict[str, Attempt],
) -> CompletionOutcome:
    """Derive completion from the EXPLICIT rule over auditable evidence (FR-LRN-005).

    ``passed_attempts`` maps an item_id to the learner's PASSING attempt (auditable evidence).
    Completion is satisfied only when every required section is completed AND every required item
    has a passing attempt — a deterministic function of durable progress/attempt evidence, never a
    mutable analytics event.
    """
    evidence: list[Evidence] = []
    missing: list[str] = []
    for section_id in rule.required_section_ids:
        if section_id in completed_sections:
            evidence.append(Evidence(kind="section", ref_id=section_id, detail="completed"))
        else:
            missing.append(f"section:{section_id}")
    for item_id in rule.required_item_ids:
        attempt = passed_attempts.get(item_id)
        if attempt is not None and attempt.score.passed:
            evidence.append(
                Evidence(kind="attempt", ref_id=attempt.attempt_id, detail=f"item:{item_id} passed")
            )
        else:
            missing.append(f"item:{item_id}")
    return CompletionOutcome(
        satisfied=not missing, evidence=tuple(evidence), missing=tuple(missing)
    )


def _verification_hash(
    rule_id: str, subject_id: str, course_id: str, evidence: tuple[Evidence, ...]
) -> str:
    canonical = json.dumps(
        {
            "rule_id": rule_id,
            "subject_id": subject_id,
            "course_id": course_id,
            "evidence": [e.to_dict() for e in evidence],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class Credential:
    """A verifiable credential recording the RULE + EVIDENCE it was issued from (FR-LRN-005)."""

    credential_id: str
    subject_id: str
    course_id: str
    rule_id: str
    evidence: tuple[Evidence, ...]
    verification_hash: str
    issued_at: datetime | None = None

    def __post_init__(self) -> None:
        _require(
            bool(self.credential_id),
            "credential_id must be non-empty",
            code="learning.credential.id",
        )
        _require(
            len(self.evidence) >= 1,
            "a credential must record evidence",
            code="learning.credential.evidence",
        )

    def verify(self) -> bool:
        """Re-derive the verification hash from the recorded rule + evidence (tamper-evident)."""
        return self.verification_hash == _verification_hash(
            self.rule_id, self.subject_id, self.course_id, self.evidence
        )


def issue_credential(
    *,
    credential_id: str,
    subject_id: str,
    course_id: str,
    rule: CompletionRule,
    evidence: tuple[Evidence, ...],
    issued_at: datetime,
) -> Credential:
    """Mint a verifiable credential from an EXPLICIT rule + its auditable evidence (FR-LRN-005)."""
    return Credential(
        credential_id=credential_id,
        subject_id=subject_id,
        course_id=course_id,
        rule_id=rule.rule_id,
        evidence=evidence,
        verification_hash=_verification_hash(rule.rule_id, subject_id, course_id, evidence),
        issued_at=issued_at,
    )


# ---------------------------------------------------------------------------
# Recommendation (FR-LRN-007) — always explainable; inferred difficulty non-authoritative
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InferredSignal:
    """An inferred signal, ALWAYS flagged inferred + non-authoritative (FR-LRN-007)."""

    name: str
    value: str
    inferred: bool = True
    authoritative: bool = False

    def __post_init__(self) -> None:
        # Deny-by-default: an inferred signal can never be presented as authoritative truth.
        _require(
            self.inferred is True,
            "an inferred signal must be flagged inferred",
            code="learning.signal.inferred",
        )
        _require(
            self.authoritative is False,
            "inferred difficulty is never authoritative",
            code="learning.signal.auth",
        )


@dataclass(frozen=True, slots=True)
class Explanation:
    """A human-readable recommendation explanation (FR-LRN-007). Never empty."""

    reason: str
    factors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(
            bool(self.reason.strip()),
            "a recommendation must carry a reason",
            code="learning.reco.reason",
        )


@dataclass(frozen=True, slots=True)
class Recommendation:
    """An explainable next-course recommendation (FR-LRN-007)."""

    course_id: str
    explanation: Explanation
    inferred_difficulty: InferredSignal | None = None

    def __post_init__(self) -> None:
        _require(
            bool(self.course_id),
            "recommendation must reference a course",
            code="learning.reco.course",
        )


# ---------------------------------------------------------------------------
# Inferred learning profile transparency (EVAL-PRIV-004)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfileFeature:
    """A single inferred-profile feature (EVAL-PRIV-004). Inspectable, correctable, resettable."""

    name: str
    value: str
    inferred: bool
    source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "inferred": self.inferred,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class InferredProfile:
    """A learner's inferred profile with a transparent feature inventory (EVAL-PRIV-004)."""

    subject_id: str
    features: tuple[ProfileFeature, ...] = ()

    def inventory(self) -> tuple[dict[str, object], ...]:
        """The exposed feature inventory (every persisted inference is visible)."""
        return tuple(f.to_dict() for f in self.features)

    def corrected(self, *, name: str, value: str) -> InferredProfile:
        """Return a profile with ``name`` set to a learner-supplied (authoritative) value."""
        others = tuple(f for f in self.features if f.name != name)
        corrected = ProfileFeature(
            name=name, value=value, inferred=False, source="learner_correction"
        )
        return InferredProfile(subject_id=self.subject_id, features=(*others, corrected))

    def reset(self) -> InferredProfile:
        """Return a profile with every INFERRED feature removed (learner-authored ones kept)."""
        kept = tuple(f for f in self.features if not f.inferred)
        return InferredProfile(subject_id=self.subject_id, features=kept)

    def has(self, name: str) -> bool:
        return any(f.name == name for f in self.features)


def raise_position_not_in_course() -> None:
    """Helper so capabilities raise the canonical position error without importing errors twice."""
    raise PositionNotInCourse()


__all__ = [
    "RES_LEARNING",
    "AnonymousProgress",
    "AssessmentItem",
    "Attempt",
    "CompletionOutcome",
    "CompletionRule",
    "Course",
    "Credential",
    "Domain",
    "Evidence",
    "Explanation",
    "InferredProfile",
    "InferredSignal",
    "ItemKind",
    "LearningPath",
    "Modality",
    "Overlay",
    "OverlayKind",
    "Position",
    "ProfileFeature",
    "ProgressRecord",
    "Recommendation",
    "Score",
    "Section",
    "evaluate_completion",
    "issue_credential",
    "merge_progress",
    "score_attempt",
]
