"""Learning & assessment capabilities: one authoritative implementation per action (LAW-04).

Every handler runs through the kernel command/query bus, so each invocation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope + acting subject come from the
authenticated :class:`RequestContext`, NEVER from the payload (rule 50). Handlers depend only on
:mod:`.ports` and the pure :mod:`..domain`.

The learning invariants are enforced here by construction and are never weakened:

* ``learning.course.compose`` composes only PUBLISHED knowledge revisions and validates every
  referenced block id (FR-LRN-001); ``learning.course.publish`` makes a composed course available.
* ``learning.progress.record`` writes progress to the module's OWN store (never analytics) with a
  stable resume position; ``learning.progress.resume`` restores it across sessions (FR-LRN-002).
* ``learning.overlay.add`` stores a private bookmark/note/highlight for the learner (FR-LRN-003).
* ``learning.assessment.item.publish`` versions items and REJECTS re-publishing a sealed version
  with different content; ``learning.assessment.attempt.submit`` scores DETERMINISTICALLY and seals
  item version (FR-LRN-004).
* ``learning.credential.evaluate`` derives completion from an EXPLICIT rule over auditable evidence
  and issues a verifiable credential (FR-LRN-005) — never from analytics events.
* ``learning.recommend.next`` is EXPLAINABLE, requires consent, respects entitlements and never
  presents inferred difficulty as authoritative (FR-LRN-007).
* ``learning.profile.inspect/correct/reset`` make the inferred profile transparent (EVAL-PRIV-004).
* ``learning.tutor.ask`` REUSES the ONE governed ``ai.answer`` pipeline as a scoped actor,
  locale-aware, applies the deterministic pedagogical rubric and NEVER discloses an answer key.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.errors import (
    AnonymousProgressClaimed,
    AttemptRejected,
    ConsentRequired,
    CourseNotFound,
    ItemImmutableError,
    ItemNotFound,
    PositionNotInCourse,
    ProfileFeatureNotFound,
    TenantScopeMissing,
    UnknownBlockError,
    UnpublishedRevisionError,
)
from ..domain.model import (
    RES_LEARNING,
    AnonymousProgress,
    AssessmentItem,
    Attempt,
    CompletionRule,
    Course,
    Domain,
    Explanation,
    InferredSignal,
    ItemKind,
    LearningPath,
    Modality,
    Overlay,
    OverlayKind,
    Position,
    ProgressRecord,
    Recommendation,
    Section,
    evaluate_completion,
    issue_credential,
    merge_progress,
    score_attempt,
)
from ..domain.rubric import RubricResult, score_pedagogy
from .ports import (
    AiTutorPort,
    ConsentPort,
    EntitlementPort,
    LearningRepositoryPort,
    PublishedContentPort,
)

CAP_VERSION = "1.0.0"

CAP_COURSE_COMPOSE = "learning.course.compose"
CAP_COURSE_PUBLISH = "learning.course.publish"
CAP_PROGRESS_RECORD = "learning.progress.record"
CAP_PROGRESS_RECORD_ANON = "learning.progress.record.anonymous"
CAP_PROGRESS_MERGE = "learning.progress.merge"
CAP_PROGRESS_RESUME = "learning.progress.resume"
CAP_OVERLAY_ADD = "learning.overlay.add"
CAP_ITEM_PUBLISH = "learning.assessment.item.publish"
CAP_ATTEMPT_SUBMIT = "learning.assessment.attempt.submit"
CAP_CREDENTIAL_EVALUATE = "learning.credential.evaluate"
CAP_RECOMMEND_NEXT = "learning.recommend.next"
CAP_PROFILE_INSPECT = "learning.profile.inspect"
CAP_PROFILE_CORRECT = "learning.profile.correct"
CAP_PROFILE_RESET = "learning.profile.reset"
CAP_TUTOR_ASK = "learning.tutor.ask"

# Commands (state changes) and queries (reads), routed on the matching kernel bus.
LEARNING_COMMANDS: tuple[str, ...] = (
    CAP_COURSE_COMPOSE,
    CAP_COURSE_PUBLISH,
    CAP_PROGRESS_RECORD,
    CAP_PROGRESS_RECORD_ANON,
    CAP_PROGRESS_MERGE,
    CAP_OVERLAY_ADD,
    CAP_ITEM_PUBLISH,
    CAP_ATTEMPT_SUBMIT,
    CAP_CREDENTIAL_EVALUATE,
    CAP_PROFILE_CORRECT,
    CAP_PROFILE_RESET,
    CAP_TUTOR_ASK,
)
LEARNING_QUERIES: tuple[str, ...] = (
    CAP_PROGRESS_RESUME,
    CAP_RECOMMEND_NEXT,
    CAP_PROFILE_INSPECT,
)
LEARNING_CAPABILITIES: tuple[str, ...] = LEARNING_COMMANDS + LEARNING_QUERIES

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]

# The governed reference tutor prompt package (immutable, declares only the read search tool).
TUTOR_PACKAGE_ID = "learner_tutor_answer"
TUTOR_PACKAGE_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Invocation helpers (context is authoritative, never the payload — rule 50)
# ---------------------------------------------------------------------------


def _typed[PayloadT](invocation: object, expected: type[PayloadT]) -> PayloadT:
    payload = getattr(invocation, "payload", None)
    if payload is None:
        payload = getattr(invocation, "parameters", None)
    if not isinstance(payload, expected):
        raise TypeError(f"expected {expected.__name__} payload, got {type(payload).__name__}")
    return payload


def _tenant(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    scope = getattr(context, "tenant_scope", None)
    if not scope:
        raise TenantScopeMissing()
    return scope


def _subject(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    actor = getattr(context, "actor", None)
    subject = getattr(actor, "id", None)
    if not subject:
        raise TenantScopeMissing()
    return str(subject)


def _correlation(invocation: object) -> str:
    context = getattr(invocation, "context", None)
    return str(getattr(context, "correlation_id", "-"))


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SectionSpec:
    section_id: str
    title: str
    object_id: str
    revision_id: str
    block_ids: tuple[str, ...]
    ordinal: int


@dataclass(frozen=True, slots=True)
class CompletionRuleSpec:
    rule_id: str
    required_section_ids: tuple[str, ...] = ()
    required_item_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComposeCourseCommand:
    course_id: str
    domain_id: str
    title: str
    sections: tuple[SectionSpec, ...]
    path_id: str | None = None
    domain_title: str | None = None
    domain_slug: str | None = None
    completion_rule: CompletionRuleSpec | None = None


@dataclass(frozen=True, slots=True)
class ComposeCourseResult:
    course_id: str
    section_count: int
    published: bool


@dataclass(frozen=True, slots=True)
class PublishCourseCommand:
    course_id: str


@dataclass(frozen=True, slots=True)
class PublishCourseResult:
    course_id: str
    published: bool


@dataclass(frozen=True, slots=True)
class RecordProgressCommand:
    course_id: str
    section_id: str
    block_id: str
    modality: str = "guided"
    complete_section: bool = False


@dataclass(frozen=True, slots=True)
class ProgressView:
    subject_id: str
    course_id: str
    resume: dict[str, str]
    modality: str
    completed_sections: tuple[str, ...]
    next_section_id: str | None


@dataclass(frozen=True, slots=True)
class ResumeQuery:
    course_id: str


@dataclass(frozen=True, slots=True)
class RecordAnonymousProgressCommand:
    course_id: str
    section_id: str
    block_id: str
    modality: str = "guided"
    complete_section: bool = False


@dataclass(frozen=True, slots=True)
class AnonymousProgressView:
    anonymous_id: str
    course_id: str
    resume: dict[str, str]
    modality: str
    completed_sections: tuple[str, ...]
    next_section_id: str | None


@dataclass(frozen=True, slots=True)
class MergeAnonymousProgressCommand:
    """Merge the anonymous progress held under ``anonymous_ids`` into the authenticated subject.

    ``anonymous_ids`` are the opaque anonymous session/device ids the signing-in client presents (a
    cross-device sign-in presents more than one). The tenant and the authenticated subject come from
    the request context, never the payload (rule 50).
    """

    anonymous_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MergeProgressResult:
    merged: tuple[ProgressView, ...]
    courses_merged: int


@dataclass(frozen=True, slots=True)
class AddOverlayCommand:
    course_id: str
    section_id: str
    block_id: str
    kind: str
    body: str = ""
    quote: str | None = None


@dataclass(frozen=True, slots=True)
class OverlayView:
    overlay_id: str
    kind: str
    position: dict[str, str]


@dataclass(frozen=True, slots=True)
class PublishItemCommand:
    item_id: str
    version: str
    kind: str
    prompt: str
    answer_key: tuple[str, ...]
    choices: tuple[str, ...] = ()
    points: int = 1
    pass_ratio: float = 1.0
    max_attempts: int = 3
    accommodations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishItemResult:
    item_id: str
    version: str
    content_hash: str
    sealed: bool
    item: dict[str, object]  # learner-visible view, WITHOUT the answer key


@dataclass(frozen=True, slots=True)
class SubmitAttemptCommand:
    item_id: str
    version: str
    responses: tuple[str, ...]
    accommodations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SubmitAttemptResult:
    attempt_id: str
    item_id: str
    item_version: str
    raw: int
    max: int
    passed: bool
    feedback: str
    attempt_number: int


@dataclass(frozen=True, slots=True)
class EvaluateCredentialCommand:
    course_id: str
    rule_id: str


@dataclass(frozen=True, slots=True)
class EvaluateCredentialResult:
    satisfied: bool
    credential_id: str | None
    rule_id: str
    evidence: tuple[dict[str, str], ...]
    missing: tuple[str, ...]
    verification_hash: str | None
    verified: bool
    already_issued: bool


@dataclass(frozen=True, slots=True)
class RecommendNextQuery:
    limit: int = 3


@dataclass(frozen=True, slots=True)
class RecommendationView:
    course_id: str
    reason: str
    factors: tuple[str, ...]
    inferred_difficulty: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class RecommendNextResult:
    recommendations: tuple[RecommendationView, ...]
    consented: bool


@dataclass(frozen=True, slots=True)
class InspectProfileQuery:
    pass


@dataclass(frozen=True, slots=True)
class ProfileView:
    subject_id: str
    features: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class CorrectProfileCommand:
    feature: str
    value: str


@dataclass(frozen=True, slots=True)
class ResetProfileCommand:
    pass


@dataclass(frozen=True, slots=True)
class TutorAskCommand:
    question: str
    locale: str = "en"


@dataclass(frozen=True, slots=True)
class TutorCitationView:
    object_id: str
    revision_id: str
    block_id: str
    chunk_id: str
    claim: str


@dataclass(frozen=True, slots=True)
class TutorAskResult:
    answer: str
    refused: bool
    locale: str
    citations: tuple[TutorCitationView, ...]
    rubric: dict[str, object]
    disclosed_answer_key: bool
    human_review_required: bool
    trace_id: str = field(default="")


# ---------------------------------------------------------------------------
# Hierarchy (FR-LRN-001)
# ---------------------------------------------------------------------------


class ComposeCourse:
    """``learning.course.compose`` — compose a course from PUBLISHED knowledge revisions."""

    def __init__(
        self, *, repository: LearningRepositoryPort, content: PublishedContentPort
    ) -> None:
        self._repo = repository
        self._content = content

    def handle(self, request: object) -> ComposeCourseResult:
        command = _typed(request, ComposeCourseCommand)
        organization_id = _tenant(request)
        sections: list[Section] = []
        for spec in command.sections:
            published = self._content.published_revision(
                organization_id=organization_id,
                object_id=spec.object_id,
                revision_id=spec.revision_id,
            )
            if published is None:
                raise UnpublishedRevisionError(spec.revision_id)
            available = set(published.block_ids)
            for block_id in spec.block_ids:
                if block_id not in available:
                    raise UnknownBlockError(block_id, spec.revision_id)
            sections.append(
                Section(
                    section_id=spec.section_id,
                    title=spec.title,
                    object_id=spec.object_id,
                    revision_id=spec.revision_id,
                    block_ids=tuple(spec.block_ids),
                    ordinal=spec.ordinal,
                )
            )
        if command.domain_title:
            self._repo.add_domain(
                organization_id=organization_id,
                domain=Domain(
                    domain_id=command.domain_id,
                    title=command.domain_title,
                    slug=command.domain_slug or command.domain_id,
                ),
            )
        if command.path_id:
            self._repo.add_path(
                organization_id=organization_id,
                path=LearningPath(
                    path_id=command.path_id,
                    domain_id=command.domain_id,
                    title=command.title,
                    course_ids=(command.course_id,),
                ),
            )
        course = Course(
            course_id=command.course_id,
            domain_id=command.domain_id,
            title=command.title,
            sections=tuple(sections),
            path_id=command.path_id,
        )
        self._repo.upsert_course(organization_id=organization_id, course=course, published=False)
        if command.completion_rule is not None:
            # A course carries its EXPLICIT completion rule (FR-LRN-005), never an analytics guess.
            self._repo.add_rule(
                organization_id=organization_id,
                rule=CompletionRule(
                    rule_id=command.completion_rule.rule_id,
                    course_id=course.course_id,
                    required_section_ids=command.completion_rule.required_section_ids,
                    required_item_ids=command.completion_rule.required_item_ids,
                ),
            )
        return ComposeCourseResult(
            course_id=course.course_id, section_count=len(course.sections), published=False
        )


class PublishCourse:
    """``learning.course.publish`` — make a composed course available to learners."""

    def __init__(self, *, repository: LearningRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> PublishCourseResult:
        command = _typed(request, PublishCourseCommand)
        organization_id = _tenant(request)
        course = self._repo.get_course(organization_id=organization_id, course_id=command.course_id)
        if course is None:
            raise CourseNotFound(command.course_id)
        self._repo.upsert_course(organization_id=organization_id, course=course, published=True)
        return PublishCourseResult(course_id=course.course_id, published=True)


# ---------------------------------------------------------------------------
# Progress + resume (FR-LRN-002) — module-owned state, not analytics-derived
# ---------------------------------------------------------------------------


class RecordProgress:
    """``learning.progress.record`` — persist progress + stable resume in the module's OWN store."""

    def __init__(self, *, repository: LearningRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> ProgressView:
        command = _typed(request, RecordProgressCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        course = self._repo.get_course(organization_id=organization_id, course_id=command.course_id)
        if course is None:
            raise CourseNotFound(command.course_id)
        position = Position(
            course_id=command.course_id,
            section_id=command.section_id,
            block_id=command.block_id,
        )
        if not course.contains(position):
            raise PositionNotInCourse()
        modality = Modality(command.modality)
        existing = self._repo.get_progress(
            organization_id=organization_id, subject_id=subject_id, course_id=command.course_id
        )
        now = self._clock()
        completed_id = command.section_id if command.complete_section else None
        if existing is None:
            progress = ProgressRecord(
                subject_id=subject_id,
                course_id=command.course_id,
                resume=position,
                modality=modality,
                completed_sections=frozenset({command.section_id}) if completed_id else frozenset(),
                updated_at=now,
            )
        else:
            progress = ProgressRecord(
                subject_id=subject_id,
                course_id=command.course_id,
                resume=position,
                modality=modality,
                completed_sections=existing.completed_sections,
                updated_at=now,
            ).advanced(resume=position, completed_section_id=completed_id, now=now)
        self._repo.save_progress(organization_id=organization_id, progress=progress)
        return _progress_view(progress, course=course)


class ResumeProgress:
    """``learning.progress.resume`` — restore the stable resume position across sessions."""

    def __init__(self, *, repository: LearningRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ProgressView:
        query = _typed(request, ResumeQuery)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        course = self._repo.get_course(organization_id=organization_id, course_id=query.course_id)
        if course is None:
            raise CourseNotFound(query.course_id)
        progress = self._repo.get_progress(
            organization_id=organization_id, subject_id=subject_id, course_id=query.course_id
        )
        if progress is None:
            # No prior progress: resume at the course start (a stable, well-defined position).
            progress = ProgressRecord(
                subject_id=subject_id,
                course_id=query.course_id,
                resume=course.start_position(),
                modality=Modality.GUIDED,
            )
        return _progress_view(progress, course=course)


def _progress_view(progress: ProgressRecord, *, course: Course) -> ProgressView:
    nxt = (
        course.next_section(progress.resume.section_id)
        if progress.modality is Modality.GUIDED
        else None
    )
    return ProgressView(
        subject_id=progress.subject_id,
        course_id=progress.course_id,
        resume=progress.resume.to_dict(),
        modality=progress.modality.value,
        completed_sections=tuple(sorted(progress.completed_sections)),
        next_section_id=nxt.section_id if nxt else None,
    )


class RecordAnonymousProgress:
    """``learning.progress.record.anonymous`` — record progress under an anonymous device/session.

    The anonymous id is the acting anonymous session identity taken from the request CONTEXT (never
    the payload, rule 50); progress is written to the module's OWN ``anonymous_progress`` store,
    tenant-scoped, at a stable resume position (FR-LRN-002, UX-010). It is merged into an
    authenticated account later via ``learning.progress.merge``.
    """

    def __init__(self, *, repository: LearningRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> AnonymousProgressView:
        command = _typed(request, RecordAnonymousProgressCommand)
        organization_id = _tenant(request)
        anonymous_id = _subject(request)
        course = self._repo.get_course(organization_id=organization_id, course_id=command.course_id)
        if course is None:
            raise CourseNotFound(command.course_id)
        position = Position(
            course_id=command.course_id,
            section_id=command.section_id,
            block_id=command.block_id,
        )
        if not course.contains(position):
            raise PositionNotInCourse()
        modality = Modality(command.modality)
        now = self._clock()
        completed_id = command.section_id if command.complete_section else None
        existing = self._repo.get_anonymous_progress(
            organization_id=organization_id,
            anonymous_id=anonymous_id,
            course_id=command.course_id,
        )
        completed = set(existing.completed_sections) if existing is not None else set()
        if completed_id:
            completed.add(completed_id)
        anonymous = AnonymousProgress(
            anonymous_id=anonymous_id,
            course_id=command.course_id,
            resume=position,
            modality=modality,
            completed_sections=frozenset(completed),
            updated_at=now,
            claimed_by=existing.claimed_by if existing is not None else None,
        )
        self._repo.save_anonymous_progress(organization_id=organization_id, anonymous=anonymous)
        nxt = course.next_section(position.section_id) if modality is Modality.GUIDED else None
        return AnonymousProgressView(
            anonymous_id=anonymous_id,
            course_id=command.course_id,
            resume=position.to_dict(),
            modality=modality.value,
            completed_sections=tuple(sorted(completed)),
            next_section_id=nxt.section_id if nxt else None,
        )


class MergeAnonymousProgress:
    """``learning.progress.merge`` — merge anonymous progress into the authenticated subject.

    On sign-in the client presents the anonymous session/device id(s) it holds. For every course
    those anonymous records touch, the authoritative :func:`merge_progress` combines the existing
    authenticated progress with the anonymous sources: furthest position wins, completed sections
    union (no loss, no duplicate), deterministic and IDEMPOTENT. The tenant + authenticated subject
    come from the context (rule 50): a source in another tenant is invisible (org-scoped read + RLS,
    cross-tenant leakage == 0), and an anonymous record already claimed by a DIFFERENT subject is
    refused (cross-owner refusal, LAW-08). Each merged record is then bound to the subject so the
    merge is a stable fixed point. Runs on the audited command bus (LAW-14).
    """

    def __init__(self, *, repository: LearningRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> MergeProgressResult:
        command = _typed(request, MergeAnonymousProgressCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        now = self._clock()
        # Gather the tenant-scoped anonymous records for every presented id, grouped per course.
        by_course: dict[str, list[AnonymousProgress]] = {}
        for anonymous_id in command.anonymous_ids:
            for anon in self._repo.list_anonymous_progress(
                organization_id=organization_id, anonymous_id=anonymous_id
            ):
                if anon.claimed_by is not None and anon.claimed_by != subject_id:
                    # Deny-by-default: never absorb another subject's anonymous progress.
                    raise AnonymousProgressClaimed(anonymous_id)
                by_course.setdefault(anon.course_id, []).append(anon)
        merged_views: list[ProgressView] = []
        for course_id in sorted(by_course):
            anon_records = by_course[course_id]
            course = self._repo.get_course(organization_id=organization_id, course_id=course_id)
            if course is None:
                raise CourseNotFound(course_id)
            existing = self._repo.get_progress(
                organization_id=organization_id, subject_id=subject_id, course_id=course_id
            )
            sources: list[ProgressRecord] = []
            if existing is not None:
                sources.append(existing)
            sources.extend(anon.as_progress_of(subject_id) for anon in anon_records)
            merged = merge_progress(course=course, subject_id=subject_id, sources=sources, now=now)
            self._repo.save_progress(organization_id=organization_id, progress=merged)
            for anon in anon_records:
                self._repo.claim_anonymous_progress(
                    organization_id=organization_id,
                    anonymous_id=anon.anonymous_id,
                    course_id=course_id,
                    subject_id=subject_id,
                )
            merged_views.append(_progress_view(merged, course=course))
        return MergeProgressResult(merged=tuple(merged_views), courses_merged=len(merged_views))


# ---------------------------------------------------------------------------
# Personal overlay (FR-LRN-003) — private to learner + tenant
# ---------------------------------------------------------------------------


class AddOverlay:
    """``learning.overlay.add`` — store a private bookmark/note/highlight at a stable position."""

    def __init__(
        self, *, repository: LearningRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> OverlayView:
        command = _typed(request, AddOverlayCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        course = self._repo.get_course(organization_id=organization_id, course_id=command.course_id)
        if course is None:
            raise CourseNotFound(command.course_id)
        position = Position(
            course_id=command.course_id,
            section_id=command.section_id,
            block_id=command.block_id,
        )
        if not course.contains(position):
            raise PositionNotInCourse()
        overlay = Overlay(
            overlay_id=self._id_factory(),
            subject_id=subject_id,
            position=position,
            kind=OverlayKind(command.kind),
            body=command.body,
            quote=command.quote,
            created_at=self._clock(),
        )
        self._repo.add_overlay(organization_id=organization_id, overlay=overlay)
        return OverlayView(
            overlay_id=overlay.overlay_id, kind=overlay.kind.value, position=position.to_dict()
        )


# ---------------------------------------------------------------------------
# Assessment (FR-LRN-004)
# ---------------------------------------------------------------------------


class PublishAssessmentItem:
    """``learning.assessment.item.publish`` — version an item; reject a sealed-version rewrite."""

    def __init__(self, *, repository: LearningRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> PublishItemResult:
        command = _typed(request, PublishItemCommand)
        organization_id = _tenant(request)
        item = AssessmentItem(
            item_id=command.item_id,
            version=command.version,
            kind=ItemKind(command.kind),
            prompt=command.prompt,
            answer_key=tuple(command.answer_key),
            choices=tuple(command.choices),
            points=command.points,
            pass_ratio=command.pass_ratio,
            max_attempts=command.max_attempts,
            accommodations=tuple(command.accommodations),
        )
        existing = self._repo.get_item(
            organization_id=organization_id, item_id=command.item_id, version=command.version
        )
        sealed = self._repo.is_item_sealed(
            organization_id=organization_id, item_id=command.item_id, version=command.version
        )
        if sealed and existing is not None and existing.content_hash() != item.content_hash():
            # An item version used in a scored attempt is IMMUTABLE (FR-LRN-004).
            raise ItemImmutableError(command.item_id, command.version)
        self._repo.upsert_item(organization_id=organization_id, item=item, sealed=sealed)
        return PublishItemResult(
            item_id=item.item_id,
            version=item.version,
            content_hash=item.content_hash(),
            sealed=sealed,
            item=item.public_view(),
        )


class SubmitAttempt:
    """``learning.assessment.attempt.submit`` — deterministic scoring; seal the item version."""

    def __init__(
        self, *, repository: LearningRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> SubmitAttemptResult:
        command = _typed(request, SubmitAttemptCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        item = self._repo.get_item(
            organization_id=organization_id, item_id=command.item_id, version=command.version
        )
        if item is None:
            raise ItemNotFound(command.item_id, command.version)
        prior = self._repo.count_attempts(
            organization_id=organization_id, subject_id=subject_id, item_id=command.item_id
        )
        if prior >= item.max_attempts:
            raise AttemptRejected(f"maximum of {item.max_attempts} attempts reached")
        score = score_attempt(item, tuple(command.responses))
        attempt = Attempt(
            attempt_id=self._id_factory(),
            item_id=item.item_id,
            item_version=item.version,
            subject_id=subject_id,
            responses=tuple(command.responses),
            score=score,
            accommodations=tuple(command.accommodations or item.accommodations),
            created_at=self._clock(),
        )
        self._repo.add_attempt(organization_id=organization_id, attempt=attempt)
        # Seal the exact item version now that a scored attempt depends on it (immutability).
        self._repo.seal_item(
            organization_id=organization_id, item_id=item.item_id, version=item.version
        )
        return SubmitAttemptResult(
            attempt_id=attempt.attempt_id,
            item_id=item.item_id,
            item_version=item.version,
            raw=score.raw,
            max=score.max,
            passed=score.passed,
            feedback=score.feedback,
            attempt_number=prior + 1,
        )


# ---------------------------------------------------------------------------
# Completion + credential (FR-LRN-005)
# ---------------------------------------------------------------------------


class EvaluateCredential:
    """``learning.credential.evaluate`` — derive completion from an EXPLICIT rule + evidence."""

    def __init__(
        self, *, repository: LearningRepositoryPort, clock: Clock, id_factory: IdFactory
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> EvaluateCredentialResult:
        command = _typed(request, EvaluateCredentialCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        rule = self._repo.get_rule(organization_id=organization_id, rule_id=command.rule_id)
        if rule is None or rule.course_id != command.course_id:
            raise CourseNotFound(command.course_id)
        progress = self._repo.get_progress(
            organization_id=organization_id, subject_id=subject_id, course_id=command.course_id
        )
        completed = progress.completed_sections if progress else frozenset()
        passed = self._repo.passed_attempts(organization_id=organization_id, subject_id=subject_id)
        outcome = evaluate_completion(rule, completed_sections=completed, passed_attempts=passed)
        if not outcome.satisfied:
            return EvaluateCredentialResult(
                satisfied=False,
                credential_id=None,
                rule_id=rule.rule_id,
                evidence=tuple(e.to_dict() for e in outcome.evidence),
                missing=outcome.missing,
                verification_hash=None,
                verified=False,
                already_issued=False,
            )
        existing = self._repo.get_credential(
            organization_id=organization_id, subject_id=subject_id, course_id=command.course_id
        )
        if existing is not None:
            # Idempotent: re-evaluating a satisfied course returns the same verifiable credential.
            return EvaluateCredentialResult(
                satisfied=True,
                credential_id=existing.credential_id,
                rule_id=existing.rule_id,
                evidence=tuple(e.to_dict() for e in existing.evidence),
                missing=(),
                verification_hash=existing.verification_hash,
                verified=existing.verify(),
                already_issued=True,
            )
        credential = issue_credential(
            credential_id=self._id_factory(),
            subject_id=subject_id,
            course_id=command.course_id,
            rule=rule,
            evidence=outcome.evidence,
            issued_at=self._clock(),
        )
        self._repo.add_credential(organization_id=organization_id, credential=credential)
        return EvaluateCredentialResult(
            satisfied=True,
            credential_id=credential.credential_id,
            rule_id=credential.rule_id,
            evidence=tuple(e.to_dict() for e in credential.evidence),
            missing=(),
            verification_hash=credential.verification_hash,
            verified=credential.verify(),
            already_issued=False,
        )


# ---------------------------------------------------------------------------
# Recommendation (FR-LRN-007)
# ---------------------------------------------------------------------------


class RecommendNext:
    """``learning.recommend.next`` — explainable, consent- + entitlement-aware next courses."""

    def __init__(
        self,
        *,
        repository: LearningRepositoryPort,
        consent: ConsentPort,
        entitlements: EntitlementPort,
    ) -> None:
        self._repo = repository
        self._consent = consent
        self._entitlements = entitlements

    def handle(self, request: object) -> RecommendNextResult:
        query = _typed(request, RecommendNextQuery)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        # Consent is required before ANY personalized recommendation (FR-LRN-007).
        if not self._consent.has_personalization_consent(
            organization_id=organization_id, subject_id=subject_id
        ):
            raise ConsentRequired()
        profile = self._repo.get_profile(organization_id=organization_id, subject_id=subject_id)
        difficulty_value = next(
            (f.value for f in profile.features if f.name == "preferred_difficulty"), None
        )
        recommendations: list[Recommendation] = []
        for course in self._repo.list_courses(organization_id=organization_id):
            if not self._repo.is_course_published(
                organization_id=organization_id, course_id=course.course_id
            ):
                continue
            # Respect entitlements: never recommend a course the learner cannot access (LAW-19).
            if not self._entitlements.is_entitled_to_course(
                organization_id=organization_id, subject_id=subject_id, course_id=course.course_id
            ):
                continue
            already = self._repo.get_progress(
                organization_id=organization_id,
                subject_id=subject_id,
                course_id=course.course_id,
            )
            factors = ["published", "entitled"]
            if already is not None:
                factors.append("in_progress")
            inferred = (
                InferredSignal(name="difficulty", value=difficulty_value)
                if difficulty_value
                else None
            )
            recommendations.append(
                Recommendation(
                    course_id=course.course_id,
                    explanation=Explanation(
                        reason=f"Recommended because {course.title} is available to you.",
                        factors=tuple(factors),
                    ),
                    inferred_difficulty=inferred,
                )
            )
            if len(recommendations) >= query.limit:
                break
        views = tuple(
            RecommendationView(
                course_id=r.course_id,
                reason=r.explanation.reason,
                factors=r.explanation.factors,
                inferred_difficulty=(
                    {
                        "name": r.inferred_difficulty.name,
                        "value": r.inferred_difficulty.value,
                        "inferred": r.inferred_difficulty.inferred,
                        "authoritative": r.inferred_difficulty.authoritative,
                    }
                    if r.inferred_difficulty
                    else None
                ),
            )
            for r in recommendations
        )
        return RecommendNextResult(recommendations=views, consented=True)


# ---------------------------------------------------------------------------
# Inferred profile transparency (EVAL-PRIV-004)
# ---------------------------------------------------------------------------


class InspectProfile:
    """``learning.profile.inspect`` — expose the learner's inferred-profile feature inventory."""

    def __init__(self, *, repository: LearningRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ProfileView:
        _typed(request, InspectProfileQuery)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        profile = self._repo.get_profile(organization_id=organization_id, subject_id=subject_id)
        return ProfileView(subject_id=subject_id, features=profile.inventory())


class CorrectProfile:
    """``learning.profile.correct`` — the learner corrects an inferred feature (authoritative)."""

    def __init__(self, *, repository: LearningRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ProfileView:
        command = _typed(request, CorrectProfileCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        profile = self._repo.get_profile(organization_id=organization_id, subject_id=subject_id)
        if not profile.has(command.feature):
            raise ProfileFeatureNotFound(command.feature)
        corrected = profile.corrected(name=command.feature, value=command.value)
        self._repo.save_profile(organization_id=organization_id, profile=corrected)
        return ProfileView(subject_id=subject_id, features=corrected.inventory())


class ResetProfile:
    """``learning.profile.reset`` — delete every INFERRED feature (learner-authored ones kept)."""

    def __init__(self, *, repository: LearningRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ProfileView:
        _typed(request, ResetProfileCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        profile = self._repo.get_profile(organization_id=organization_id, subject_id=subject_id)
        reset = profile.reset()
        self._repo.save_profile(organization_id=organization_id, profile=reset)
        return ProfileView(subject_id=subject_id, features=reset.inventory())


# ---------------------------------------------------------------------------
# AI tutor (EVAL-AI-009/011) — reuse the ONE governed ai.answer pipeline
# ---------------------------------------------------------------------------


class AskTutor:
    """``learning.tutor.ask`` — a multilingual, pedagogy-scored tutor over the governed AI pipeline.

    Reuses ``ai.answer`` as a scoped actor (LAW-09): retrieval ACL, citation verification and the
    output guard are inherited from the ONE pipeline. The answer is produced in the learner's locale
    (EVAL-AI-011). A deterministic pedagogical rubric scores the answer (EVAL-AI-009 automatable
    slice) and honestly flags the human-graded remainder. By construction an assessment answer key
    is never placed in the tutor's context, so a red-team request for one returns nothing.
    """

    def __init__(self, *, tutor: AiTutorPort) -> None:
        self._tutor = tutor

    def handle(self, request: object) -> TutorAskResult:
        command = _typed(request, TutorAskCommand)
        organization_id = _tenant(request)
        subject_id = _subject(request)
        correlation_id = _correlation(request)
        answer = self._tutor.ask(
            organization_id=organization_id,
            subject_id=subject_id,
            correlation_id=correlation_id,
            question=command.question,
            locale=command.locale,
            package_id=TUTOR_PACKAGE_ID,
            version=TUTOR_PACKAGE_VERSION,
        )
        grounded = bool(answer.citations) and not answer.refused
        rubric: RubricResult = score_pedagogy(
            answer.answer, grounded=grounded, refused=answer.refused
        )
        return TutorAskResult(
            answer=answer.answer,
            refused=answer.refused,
            locale=answer.locale or command.locale,
            citations=tuple(
                TutorCitationView(
                    object_id=c.object_id,
                    revision_id=c.revision_id,
                    block_id=c.block_id,
                    chunk_id=c.chunk_id,
                    claim=c.claim,
                )
                for c in answer.citations
            ),
            rubric=rubric.to_dict(),
            # The tutor never receives an answer key, so it can never disclose one.
            disclosed_answer_key=False,
            human_review_required=rubric.human_review_required,
            trace_id=answer.trace_id,
        )


def registered_capabilities() -> Sequence[str]:
    """The full authoritative capability list (LAW-04)."""
    return LEARNING_CAPABILITIES


__all__ = [
    "CAP_ATTEMPT_SUBMIT",
    "CAP_COURSE_COMPOSE",
    "CAP_COURSE_PUBLISH",
    "CAP_CREDENTIAL_EVALUATE",
    "CAP_ITEM_PUBLISH",
    "CAP_OVERLAY_ADD",
    "CAP_PROFILE_CORRECT",
    "CAP_PROFILE_INSPECT",
    "CAP_PROFILE_RESET",
    "CAP_PROGRESS_MERGE",
    "CAP_PROGRESS_RECORD",
    "CAP_PROGRESS_RECORD_ANON",
    "CAP_PROGRESS_RESUME",
    "CAP_RECOMMEND_NEXT",
    "CAP_TUTOR_ASK",
    "CAP_VERSION",
    "LEARNING_CAPABILITIES",
    "LEARNING_COMMANDS",
    "LEARNING_QUERIES",
    "RES_LEARNING",
    "TUTOR_PACKAGE_ID",
    "TUTOR_PACKAGE_VERSION",
    "AddOverlay",
    "AddOverlayCommand",
    "AnonymousProgressView",
    "AskTutor",
    "CompletionRuleSpec",
    "ComposeCourse",
    "ComposeCourseCommand",
    "ComposeCourseResult",
    "CorrectProfile",
    "CorrectProfileCommand",
    "EvaluateCredential",
    "EvaluateCredentialCommand",
    "EvaluateCredentialResult",
    "InspectProfile",
    "InspectProfileQuery",
    "MergeAnonymousProgress",
    "MergeAnonymousProgressCommand",
    "MergeProgressResult",
    "OverlayView",
    "ProfileView",
    "ProgressView",
    "PublishAssessmentItem",
    "PublishCourse",
    "PublishCourseCommand",
    "PublishItemCommand",
    "PublishItemResult",
    "RecommendNext",
    "RecommendNextQuery",
    "RecommendNextResult",
    "RecommendationView",
    "RecordAnonymousProgress",
    "RecordAnonymousProgressCommand",
    "RecordProgress",
    "RecordProgressCommand",
    "ResetProfile",
    "ResetProfileCommand",
    "ResumeProgress",
    "ResumeQuery",
    "SectionSpec",
    "SubmitAttempt",
    "SubmitAttemptCommand",
    "SubmitAttemptResult",
    "TutorAskCommand",
    "TutorAskResult",
    "TutorCitationView",
]
