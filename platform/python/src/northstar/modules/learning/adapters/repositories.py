"""Learning repositories (in-memory + SQLAlchemy) implementing :class:`LearningRepositoryPort`.

Every SQLAlchemy read/write is parameterised and filtered by ``organization_id`` (rule 50, tenant
isolation) and sets the per-transaction tenant GUC so PostgreSQL FORCED RLS applies as
defense-in-depth. No string interpolation of values.

Progress is stored in the module's OWN ``progress`` table (FR-LRN-002) — never derived from
analytics. The ``assessment_item.sealed`` flag makes an item version used in a scored attempt
immutable (FR-LRN-004). Overlays and progress are keyed by ``(organization_id, subject_id, ...)`` so
a learner only ever reads their own private overlay/progress (FR-LRN-003).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from northstar.adapters.persistence_sqlalchemy.tenancy import set_tenant_guc
from northstar.adapters.persistence_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

from ..domain.model import (
    AnonymousProgress,
    AssessmentItem,
    Attempt,
    CompletionRule,
    Course,
    Credential,
    Domain,
    Evidence,
    InferredProfile,
    ItemKind,
    Modality,
    Overlay,
    OverlayKind,
    Position,
    ProfileFeature,
    ProgressRecord,
    Score,
    Section,
)
from ..domain.model import (
    LearningPath as LearningPathModel,
)
from .tables import LearningTables


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _section_to_dict(section: Section) -> dict[str, object]:
    return {
        "section_id": section.section_id,
        "title": section.title,
        "object_id": section.object_id,
        "revision_id": section.revision_id,
        "block_ids": list(section.block_ids),
        "ordinal": section.ordinal,
    }


def _section_from_dict(raw: dict[str, object]) -> Section:
    return Section(
        section_id=str(raw["section_id"]),
        title=str(raw["title"]),
        object_id=str(raw["object_id"]),
        revision_id=str(raw["revision_id"]),
        block_ids=tuple(str(b) for b in raw.get("block_ids", []) or []),
        ordinal=int(raw.get("ordinal", 0)),
    )


def _course_from_row(row: object) -> Course:
    return Course(
        course_id=row.course_id,
        domain_id=row.domain_id,
        title=row.title,
        sections=tuple(_section_from_dict(dict(s)) for s in (row.sections or [])),
        path_id=row.path_id,
    )


# ---------------------------------------------------------------------------
# In-memory repository (fast, deterministic unit/security tests)
# ---------------------------------------------------------------------------


class InMemoryLearningRepository:
    """In-memory, tenant-scoped learning repository for fast, deterministic tests."""

    def __init__(self) -> None:
        self._domains: dict[tuple[str, str], Domain] = {}
        self._paths: dict[tuple[str, str], LearningPathModel] = {}
        self._courses: dict[tuple[str, str], Course] = {}
        self._published: set[tuple[str, str]] = set()
        self._progress: dict[tuple[str, str, str], ProgressRecord] = {}
        self._anonymous: dict[tuple[str, str, str], AnonymousProgress] = {}
        self._overlays: dict[tuple[str, str], Overlay] = {}
        self._items: dict[tuple[str, str, str], AssessmentItem] = {}
        self._sealed: set[tuple[str, str, str]] = set()
        self._attempts: dict[tuple[str, str], Attempt] = {}
        self._rules: dict[tuple[str, str], CompletionRule] = {}
        self._credentials: dict[tuple[str, str], Credential] = {}
        self._profiles: dict[tuple[str, str], InferredProfile] = {}

    # Hierarchy ----------------------------------------------------------
    def add_domain(self, *, organization_id: str, domain: Domain) -> None:
        self._domains[(organization_id, domain.domain_id)] = domain

    def add_path(self, *, organization_id: str, path: LearningPathModel) -> None:
        self._paths[(organization_id, path.path_id)] = path

    def upsert_course(
        self, *, organization_id: str, course: Course, published: bool = False
    ) -> None:
        self._courses[(organization_id, course.course_id)] = course
        if published:
            self._published.add((organization_id, course.course_id))

    def get_course(self, *, organization_id: str, course_id: str) -> Course | None:
        return self._courses.get((organization_id, course_id))

    def is_course_published(self, *, organization_id: str, course_id: str) -> bool:
        return (organization_id, course_id) in self._published

    def list_courses(self, *, organization_id: str) -> Sequence[Course]:
        return [c for (org, _cid), c in self._courses.items() if org == organization_id]

    # Progress -----------------------------------------------------------
    def save_progress(self, *, organization_id: str, progress: ProgressRecord) -> None:
        self._progress[(organization_id, progress.subject_id, progress.course_id)] = progress

    def get_progress(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> ProgressRecord | None:
        return self._progress.get((organization_id, subject_id, course_id))

    # Anonymous progress -------------------------------------------------
    def save_anonymous_progress(
        self, *, organization_id: str, anonymous: AnonymousProgress
    ) -> None:
        self._anonymous[(organization_id, anonymous.anonymous_id, anonymous.course_id)] = anonymous

    def get_anonymous_progress(
        self, *, organization_id: str, anonymous_id: str, course_id: str
    ) -> AnonymousProgress | None:
        return self._anonymous.get((organization_id, anonymous_id, course_id))

    def list_anonymous_progress(
        self, *, organization_id: str, anonymous_id: str
    ) -> Sequence[AnonymousProgress]:
        return [
            record
            for (org, anon, _cid), record in self._anonymous.items()
            if org == organization_id and anon == anonymous_id
        ]

    def claim_anonymous_progress(
        self, *, organization_id: str, anonymous_id: str, course_id: str, subject_id: str
    ) -> None:
        key = (organization_id, anonymous_id, course_id)
        record = self._anonymous.get(key)
        if record is not None:
            self._anonymous[key] = AnonymousProgress(
                anonymous_id=record.anonymous_id,
                course_id=record.course_id,
                resume=record.resume,
                modality=record.modality,
                completed_sections=record.completed_sections,
                updated_at=record.updated_at,
                claimed_by=subject_id,
            )

    # Overlay ------------------------------------------------------------
    def add_overlay(self, *, organization_id: str, overlay: Overlay) -> None:
        self._overlays[(organization_id, overlay.overlay_id)] = overlay

    def list_overlays(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> Sequence[Overlay]:
        return [
            o
            for (org, _oid), o in self._overlays.items()
            if org == organization_id
            and o.subject_id == subject_id
            and o.position.course_id == course_id
        ]

    # Assessment ---------------------------------------------------------
    def get_item(
        self, *, organization_id: str, item_id: str, version: str
    ) -> AssessmentItem | None:
        return self._items.get((organization_id, item_id, version))

    def is_item_sealed(self, *, organization_id: str, item_id: str, version: str) -> bool:
        return (organization_id, item_id, version) in self._sealed

    def upsert_item(self, *, organization_id: str, item: AssessmentItem, sealed: bool) -> None:
        self._items[(organization_id, item.item_id, item.version)] = item
        if sealed:
            self._sealed.add((organization_id, item.item_id, item.version))

    def seal_item(self, *, organization_id: str, item_id: str, version: str) -> None:
        self._sealed.add((organization_id, item_id, version))

    def add_attempt(self, *, organization_id: str, attempt: Attempt) -> None:
        self._attempts[(organization_id, attempt.attempt_id)] = attempt

    def count_attempts(self, *, organization_id: str, subject_id: str, item_id: str) -> int:
        return sum(
            1
            for (org, _aid), a in self._attempts.items()
            if org == organization_id and a.subject_id == subject_id and a.item_id == item_id
        )

    def passed_attempts(self, *, organization_id: str, subject_id: str) -> dict[str, Attempt]:
        result: dict[str, Attempt] = {}
        for (org, _aid), a in self._attempts.items():
            if org == organization_id and a.subject_id == subject_id and a.score.passed:
                result[a.item_id] = a
        return result

    # Completion + credential -------------------------------------------
    def add_rule(self, *, organization_id: str, rule: CompletionRule) -> None:
        self._rules[(organization_id, rule.rule_id)] = rule

    def get_rule(self, *, organization_id: str, rule_id: str) -> CompletionRule | None:
        return self._rules.get((organization_id, rule_id))

    def add_credential(self, *, organization_id: str, credential: Credential) -> None:
        self._credentials[(organization_id, credential.credential_id)] = credential

    def get_credential(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> Credential | None:
        for (org, _cid), c in self._credentials.items():
            if org == organization_id and c.subject_id == subject_id and c.course_id == course_id:
                return c
        return None

    # Inferred profile ---------------------------------------------------
    def get_profile(self, *, organization_id: str, subject_id: str) -> InferredProfile:
        return self._profiles.get(
            (organization_id, subject_id), InferredProfile(subject_id=subject_id)
        )

    def save_profile(self, *, organization_id: str, profile: InferredProfile) -> None:
        self._profiles[(organization_id, profile.subject_id)] = profile


# ---------------------------------------------------------------------------
# SQLAlchemy repository (PostgreSQL; RLS-forced schema)
# ---------------------------------------------------------------------------


class SqlAlchemyLearningRepository:
    """PostgreSQL learning repository; every query filters by ``organization_id`` + sets the GUC."""

    def __init__(self, *, session_factory: sessionmaker[SaSession], tables: LearningTables) -> None:
        self._session_factory = session_factory
        self._tables = tables

    # Hierarchy ----------------------------------------------------------
    def add_domain(self, *, organization_id: str, domain: Domain) -> None:
        table = self._tables.domain
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.domain_id).where(
                    table.c.organization_id == organization_id,
                    table.c.domain_id == domain.domain_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        domain_id=domain.domain_id,
                        title=domain.title,
                        slug=domain.slug,
                        created_at=_now(),
                    )
                )
            uow.commit()

    def add_path(self, *, organization_id: str, path: LearningPathModel) -> None:
        table = self._tables.learning_path
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.path_id).where(
                    table.c.organization_id == organization_id,
                    table.c.path_id == path.path_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        path_id=path.path_id,
                        domain_id=path.domain_id,
                        title=path.title,
                        course_ids=list(path.course_ids),
                        created_at=_now(),
                    )
                )
            uow.commit()

    def upsert_course(
        self, *, organization_id: str, course: Course, published: bool = False
    ) -> None:
        table = self._tables.course
        sections = [_section_to_dict(s) for s in course.sections]
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.course_id).where(
                    table.c.organization_id == organization_id,
                    table.c.course_id == course.course_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        course_id=course.course_id,
                        domain_id=course.domain_id,
                        path_id=course.path_id,
                        title=course.title,
                        sections=sections,
                        published=published,
                        created_at=_now(),
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == organization_id,
                        table.c.course_id == course.course_id,
                    )
                    .values(
                        domain_id=course.domain_id,
                        path_id=course.path_id,
                        title=course.title,
                        sections=sections,
                        published=published,
                    )
                )
            uow.commit()

    def get_course(self, *, organization_id: str, course_id: str) -> Course | None:
        table = self._tables.course
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.course_id == course_id,
                )
            ).first()
        return _course_from_row(row) if row is not None else None

    def is_course_published(self, *, organization_id: str, course_id: str) -> bool:
        table = self._tables.course
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table.c.published).where(
                    table.c.organization_id == organization_id,
                    table.c.course_id == course_id,
                )
            ).first()
        return bool(row and row.published)

    def list_courses(self, *, organization_id: str) -> Sequence[Course]:
        table = self._tables.course
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(table.c.organization_id == organization_id)
            ).all()
        return [_course_from_row(row) for row in rows]

    # Progress -----------------------------------------------------------
    def save_progress(self, *, organization_id: str, progress: ProgressRecord) -> None:
        table = self._tables.progress
        values = {
            "resume": progress.resume.to_dict(),
            "modality": progress.modality.value,
            "completed_sections": sorted(progress.completed_sections),
            "updated_at": progress.updated_at,
        }
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.subject_id).where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == progress.subject_id,
                    table.c.course_id == progress.course_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        subject_id=progress.subject_id,
                        course_id=progress.course_id,
                        **values,
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == organization_id,
                        table.c.subject_id == progress.subject_id,
                        table.c.course_id == progress.course_id,
                    )
                    .values(**values)
                )
            uow.commit()

    def get_progress(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> ProgressRecord | None:
        table = self._tables.progress
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                    table.c.course_id == course_id,
                )
            ).first()
        if row is None:
            return None
        resume = dict(row.resume)
        return ProgressRecord(
            subject_id=row.subject_id,
            course_id=row.course_id,
            resume=Position(
                course_id=resume["course_id"],
                section_id=resume["section_id"],
                block_id=resume["block_id"],
            ),
            modality=Modality(row.modality),
            completed_sections=frozenset(row.completed_sections or ()),
            updated_at=(_aware(row.updated_at) if row.updated_at else None),
        )

    # Anonymous progress -------------------------------------------------
    def _anonymous_from_row(self, row: object) -> AnonymousProgress:
        resume = dict(row.resume)
        return AnonymousProgress(
            anonymous_id=row.anonymous_id,
            course_id=row.course_id,
            resume=Position(
                course_id=resume["course_id"],
                section_id=resume["section_id"],
                block_id=resume["block_id"],
            ),
            modality=Modality(row.modality),
            completed_sections=frozenset(row.completed_sections or ()),
            updated_at=(_aware(row.updated_at) if row.updated_at else None),
            claimed_by=row.claimed_by,
        )

    def save_anonymous_progress(
        self, *, organization_id: str, anonymous: AnonymousProgress
    ) -> None:
        table = self._tables.anonymous_progress
        values = {
            "resume": anonymous.resume.to_dict(),
            "modality": anonymous.modality.value,
            "completed_sections": sorted(anonymous.completed_sections),
            "claimed_by": anonymous.claimed_by,
            "updated_at": anonymous.updated_at,
        }
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.anonymous_id).where(
                    table.c.organization_id == organization_id,
                    table.c.anonymous_id == anonymous.anonymous_id,
                    table.c.course_id == anonymous.course_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        anonymous_id=anonymous.anonymous_id,
                        course_id=anonymous.course_id,
                        **values,
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == organization_id,
                        table.c.anonymous_id == anonymous.anonymous_id,
                        table.c.course_id == anonymous.course_id,
                    )
                    .values(**values)
                )
            uow.commit()

    def get_anonymous_progress(
        self, *, organization_id: str, anonymous_id: str, course_id: str
    ) -> AnonymousProgress | None:
        table = self._tables.anonymous_progress
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.anonymous_id == anonymous_id,
                    table.c.course_id == course_id,
                )
            ).first()
        return self._anonymous_from_row(row) if row is not None else None

    def list_anonymous_progress(
        self, *, organization_id: str, anonymous_id: str
    ) -> Sequence[AnonymousProgress]:
        table = self._tables.anonymous_progress
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.anonymous_id == anonymous_id,
                )
            ).all()
        return [self._anonymous_from_row(row) for row in rows]

    def claim_anonymous_progress(
        self, *, organization_id: str, anonymous_id: str, course_id: str, subject_id: str
    ) -> None:
        table = self._tables.anonymous_progress
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                update(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.anonymous_id == anonymous_id,
                    table.c.course_id == course_id,
                )
                .values(claimed_by=subject_id)
            )
            uow.commit()

    # Overlay ------------------------------------------------------------
    def add_overlay(self, *, organization_id: str, overlay: Overlay) -> None:
        table = self._tables.overlay
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    overlay_id=overlay.overlay_id,
                    subject_id=overlay.subject_id,
                    course_id=overlay.position.course_id,
                    section_id=overlay.position.section_id,
                    block_id=overlay.position.block_id,
                    kind=overlay.kind.value,
                    body=overlay.body,
                    quote=overlay.quote,
                    created_at=_aware(overlay.created_at) if overlay.created_at else _now(),
                )
            )
            uow.commit()

    def list_overlays(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> Sequence[Overlay]:
        table = self._tables.overlay
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                    table.c.course_id == course_id,
                )
            ).all()
        return [
            Overlay(
                overlay_id=row.overlay_id,
                subject_id=row.subject_id,
                position=Position(
                    course_id=row.course_id, section_id=row.section_id, block_id=row.block_id
                ),
                kind=OverlayKind(row.kind),
                body=row.body,
                quote=row.quote,
                created_at=_aware(row.created_at) if row.created_at else None,
            )
            for row in rows
        ]

    # Assessment ---------------------------------------------------------
    def get_item(
        self, *, organization_id: str, item_id: str, version: str
    ) -> AssessmentItem | None:
        table = self._tables.assessment_item
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.item_id == item_id,
                    table.c.version == version,
                )
            ).first()
        if row is None:
            return None
        return AssessmentItem(
            item_id=row.item_id,
            version=row.version,
            kind=ItemKind(row.kind),
            prompt=row.prompt,
            answer_key=tuple(row.answer_key or ()),
            choices=tuple(row.choices or ()),
            points=row.points,
            pass_ratio=row.pass_ratio,
            max_attempts=row.max_attempts,
            accommodations=tuple(row.accommodations or ()),
        )

    def is_item_sealed(self, *, organization_id: str, item_id: str, version: str) -> bool:
        table = self._tables.assessment_item
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table.c.sealed).where(
                    table.c.organization_id == organization_id,
                    table.c.item_id == item_id,
                    table.c.version == version,
                )
            ).first()
        return bool(row and row.sealed)

    def upsert_item(self, *, organization_id: str, item: AssessmentItem, sealed: bool) -> None:
        table = self._tables.assessment_item
        values = {
            "kind": item.kind.value,
            "prompt": item.prompt,
            "answer_key": list(item.answer_key),
            "choices": list(item.choices),
            "points": item.points,
            "pass_ratio": item.pass_ratio,
            "max_attempts": item.max_attempts,
            "accommodations": list(item.accommodations),
            "content_hash": item.content_hash(),
            "sealed": sealed,
        }
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.item_id).where(
                    table.c.organization_id == organization_id,
                    table.c.item_id == item.item_id,
                    table.c.version == item.version,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        item_id=item.item_id,
                        version=item.version,
                        created_at=_now(),
                        **values,
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == organization_id,
                        table.c.item_id == item.item_id,
                        table.c.version == item.version,
                    )
                    .values(**values)
                )
            uow.commit()

    def seal_item(self, *, organization_id: str, item_id: str, version: str) -> None:
        table = self._tables.assessment_item
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                update(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.item_id == item_id,
                    table.c.version == version,
                )
                .values(sealed=True)
            )
            uow.commit()

    def add_attempt(self, *, organization_id: str, attempt: Attempt) -> None:
        table = self._tables.attempt
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    attempt_id=attempt.attempt_id,
                    item_id=attempt.item_id,
                    item_version=attempt.item_version,
                    subject_id=attempt.subject_id,
                    responses=list(attempt.responses),
                    raw=attempt.score.raw,
                    max=attempt.score.max,
                    passed=attempt.score.passed,
                    feedback=attempt.score.feedback,
                    accommodations=list(attempt.accommodations),
                    created_at=_aware(attempt.created_at) if attempt.created_at else _now(),
                )
            )
            uow.commit()

    def count_attempts(self, *, organization_id: str, subject_id: str, item_id: str) -> int:
        from sqlalchemy import func

        table = self._tables.attempt
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(func.count())
                .select_from(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                    table.c.item_id == item_id,
                )
            ).first()
        return int(row[0]) if row else 0

    def passed_attempts(self, *, organization_id: str, subject_id: str) -> dict[str, Attempt]:
        table = self._tables.attempt
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                    table.c.passed.is_(True),
                )
            ).all()
        result: dict[str, Attempt] = {}
        for row in rows:
            result[row.item_id] = Attempt(
                attempt_id=row.attempt_id,
                item_id=row.item_id,
                item_version=row.item_version,
                subject_id=row.subject_id,
                responses=tuple(row.responses or ()),
                score=Score(raw=row.raw, max=row.max, passed=row.passed, feedback=row.feedback),
                accommodations=tuple(row.accommodations or ()),
                created_at=_aware(row.created_at) if row.created_at else None,
            )
        return result

    # Completion + credential -------------------------------------------
    def add_rule(self, *, organization_id: str, rule: CompletionRule) -> None:
        table = self._tables.completion_rule
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing = session.execute(
                select(table.c.rule_id).where(
                    table.c.organization_id == organization_id,
                    table.c.rule_id == rule.rule_id,
                )
            ).first()
            if existing is None:
                session.execute(
                    insert(table).values(
                        organization_id=organization_id,
                        rule_id=rule.rule_id,
                        course_id=rule.course_id,
                        required_section_ids=list(rule.required_section_ids),
                        required_item_ids=list(rule.required_item_ids),
                        created_at=_now(),
                    )
                )
            else:
                session.execute(
                    update(table)
                    .where(
                        table.c.organization_id == organization_id,
                        table.c.rule_id == rule.rule_id,
                    )
                    .values(
                        course_id=rule.course_id,
                        required_section_ids=list(rule.required_section_ids),
                        required_item_ids=list(rule.required_item_ids),
                    )
                )
            uow.commit()

    def get_rule(self, *, organization_id: str, rule_id: str) -> CompletionRule | None:
        table = self._tables.completion_rule
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.rule_id == rule_id,
                )
            ).first()
        if row is None:
            return None
        return CompletionRule(
            rule_id=row.rule_id,
            course_id=row.course_id,
            required_section_ids=tuple(row.required_section_ids or ()),
            required_item_ids=tuple(row.required_item_ids or ()),
        )

    def add_credential(self, *, organization_id: str, credential: Credential) -> None:
        table = self._tables.credential
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            session.execute(
                insert(table).values(
                    organization_id=organization_id,
                    credential_id=credential.credential_id,
                    subject_id=credential.subject_id,
                    course_id=credential.course_id,
                    rule_id=credential.rule_id,
                    evidence=[e.to_dict() for e in credential.evidence],
                    verification_hash=credential.verification_hash,
                    issued_at=_aware(credential.issued_at) if credential.issued_at else None,
                )
            )
            uow.commit()

    def get_credential(
        self, *, organization_id: str, subject_id: str, course_id: str
    ) -> Credential | None:
        table = self._tables.credential
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            row = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                    table.c.course_id == course_id,
                )
            ).first()
        if row is None:
            return None
        return Credential(
            credential_id=row.credential_id,
            subject_id=row.subject_id,
            course_id=row.course_id,
            rule_id=row.rule_id,
            evidence=tuple(
                Evidence(kind=e["kind"], ref_id=e["ref_id"], detail=e.get("detail", ""))
                for e in (row.evidence or [])
            ),
            verification_hash=row.verification_hash,
            issued_at=_aware(row.issued_at) if row.issued_at else None,
        )

    # Inferred profile ---------------------------------------------------
    def get_profile(self, *, organization_id: str, subject_id: str) -> InferredProfile:
        table = self._tables.profile_feature
        with self._session_factory() as session:
            set_tenant_guc(session, organization_id)
            rows = session.execute(
                select(table).where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == subject_id,
                )
            ).all()
        return InferredProfile(
            subject_id=subject_id,
            features=tuple(
                ProfileFeature(
                    name=row.name, value=row.value, inferred=row.inferred, source=row.source
                )
                for row in rows
            ),
        )

    def save_profile(self, *, organization_id: str, profile: InferredProfile) -> None:
        table = self._tables.profile_feature
        keep = {f.name for f in profile.features}
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            session = uow.session
            set_tenant_guc(session, organization_id)
            existing_rows = session.execute(
                select(table.c.name).where(
                    table.c.organization_id == organization_id,
                    table.c.subject_id == profile.subject_id,
                )
            ).all()
            existing_names = {row.name for row in existing_rows}
            # Delete any feature the new profile dropped (a reset removes inferred features).
            from sqlalchemy import delete as _delete

            for name in existing_names - keep:
                session.execute(
                    _delete(table).where(
                        table.c.organization_id == organization_id,
                        table.c.subject_id == profile.subject_id,
                        table.c.name == name,
                    )
                )
            for feature in profile.features:
                values = {
                    "value": feature.value,
                    "inferred": feature.inferred,
                    "source": feature.source,
                    "updated_at": _now(),
                }
                if feature.name in existing_names:
                    session.execute(
                        update(table)
                        .where(
                            table.c.organization_id == organization_id,
                            table.c.subject_id == profile.subject_id,
                            table.c.name == feature.name,
                        )
                        .values(**values)
                    )
                else:
                    session.execute(
                        insert(table).values(
                            organization_id=organization_id,
                            subject_id=profile.subject_id,
                            name=feature.name,
                            **values,
                        )
                    )
            uow.commit()


__all__ = [
    "InMemoryLearningRepository",
    "SqlAlchemyLearningRepository",
]
