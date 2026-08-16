"""Analytics capabilities: one authoritative implementation per action (LAW-04, docs/17).

Every handler runs through the kernel command/query bus, so each invocation is authorized
deny-by-default and audited (rule 50, LAW-14). Tenant scope + acting subject come from the
authenticated :class:`RequestContext`, NEVER from the payload (rule 50). Handlers depend only on
:mod:`.ports` and the pure :mod:`..domain`.

The analytics invariants are enforced here by construction and are never weakened:

* ``analytics.catalog.register`` registers an event type ONLY if it declares a purpose and validates
  against the catalog schema; a purpose-less / malformed type is rejected (FR-ANL-003).
* ``analytics.event.ingest`` VALIDATES each event against its catalog definition and REJECTS an
  unknown / malformed / purpose-less-type event, then persists a valid event as the AUTHORITATIVE
  first-party record (FR-ANL-001/002/007).
* ``analytics.identity.stitch`` links identities ONLY with the required consent; without consent no
  linkage is created (FR-ANL-004).
* ``analytics.report.reach`` computes complete first-party content intelligence with NO external
  dependency (FR-ANL-005; GA independence, EVAL-ANL-002).
* ``analytics.ga4.import`` returns GA4 figures LABELLED non-authoritative with source freshness +
  mapping — never authoritative learner state (FR-ANL-006, EVAL-ANL-006).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.errors import ConsentNotGranted, TenantScopeMissing, UnknownEventType
from ..domain.model import (
    RES_ANALYTICS,
    AnalyticsEvent,
    AnalyticsEventDefinition,
    ConsentSnapshot,
    EventCatalog,
    Ga4Mapping,
    build_reach_report,
    link_identities,
)
from .ports import AnalyticsRepositoryPort, Ga4AdapterPort

CAP_VERSION = "1.0.0"

CAP_CATALOG_REGISTER = "analytics.catalog.register"
CAP_EVENT_INGEST = "analytics.event.ingest"
CAP_IDENTITY_STITCH = "analytics.identity.stitch"
CAP_REPORT_REACH = "analytics.report.reach"
CAP_GA4_IMPORT = "analytics.ga4.import"

ANALYTICS_CAPABILITIES: tuple[str, ...] = (
    CAP_CATALOG_REGISTER,
    CAP_EVENT_INGEST,
    CAP_IDENTITY_STITCH,
    CAP_REPORT_REACH,
    CAP_GA4_IMPORT,
)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Command / query payloads and result DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegisterEventDefinitionCommand:
    definition: dict[str, object]


@dataclass(frozen=True, slots=True)
class RegisterEventDefinitionResult:
    event_name: str
    version: int
    purpose: str
    consent_category: str


@dataclass(frozen=True, slots=True)
class IngestEventCommand:
    event_name: str
    event_version: int
    actor_type: str
    actor_id: str
    properties: dict[str, object] = field(default_factory=dict)
    anonymous_id: str | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IngestEventResult:
    event_id: str
    event_name: str
    accepted: bool
    authoritative: bool
    source: str


@dataclass(frozen=True, slots=True)
class StitchIdentityCommand:
    anonymous_id: str
    user_id: str
    consent_categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StitchIdentityResult:
    anonymous_id: str
    user_id: str
    linked: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReportReachQuery:
    event_name: str
    content_property: str = "content_id"


@dataclass(frozen=True, slots=True)
class ReachEntryView:
    content_id: str
    reach: int
    occurrences: int


@dataclass(frozen=True, slots=True)
class ReportReachResult:
    event_name: str
    entries: tuple[ReachEntryView, ...]
    total_events: int
    source: str
    authoritative: bool


@dataclass(frozen=True, slots=True)
class ImportGa4Command:
    northstar_event: str
    ga4_event: str
    metric_name: str


@dataclass(frozen=True, slots=True)
class ImportGa4Result:
    metric_name: str
    value: float
    source: str
    authoritative: bool
    as_of: str
    retrieved_at: str
    mapping: dict[str, str]


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


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


class RegisterEventDefinition:
    """``analytics.catalog.register`` — register a purpose-governed, schema-valid event type."""

    def __init__(self, *, repository: AnalyticsRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> RegisterEventDefinitionResult:
        command = _typed(request, RegisterEventDefinitionCommand)
        organization_id = _tenant(request)
        # from_dict enforces the catalog invariants: a purpose-less/malformed type raises here.
        definition = AnalyticsEventDefinition.from_dict(dict(command.definition))
        self._repo.add_definition(organization_id=organization_id, definition=definition)
        return RegisterEventDefinitionResult(
            event_name=definition.event_name,
            version=definition.version,
            purpose=definition.purpose,
            consent_category=definition.consent_category.value,
        )


class IngestEvent:
    """``analytics.event.ingest`` — validate against the catalog then persist authoritatively."""

    def __init__(
        self,
        *,
        repository: AnalyticsRepositoryPort,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repo = repository
        self._clock = clock
        self._id_factory = id_factory

    def handle(self, request: object) -> IngestEventResult:
        command = _typed(request, IngestEventCommand)
        organization_id = _tenant(request)
        definition = self._repo.get_definition(
            organization_id=organization_id, event_name=command.event_name
        )
        if definition is None:
            # Unknown / unregistered (hence purpose-less) type is rejected, never accepted.
            raise UnknownEventType(command.event_name)
        catalog = EventCatalog((definition,))
        event = AnalyticsEvent(
            event_name=command.event_name,
            event_version=command.event_version,
            occurred_at=command.occurred_at or self._clock(),
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            properties=dict(command.properties),
            anonymous_id=command.anonymous_id,
            event_id=self._id_factory(),
        )
        # Deny-by-default: a malformed / unknown-property / wrong-type event is rejected here.
        catalog.validate(event)
        self._repo.record_event(organization_id=organization_id, event=event)
        return IngestEventResult(
            event_id=event.event_id or "",
            event_name=event.event_name,
            accepted=True,
            authoritative=True,
            source="first_party",
        )


class StitchIdentity:
    """``analytics.identity.stitch`` — link identities ONLY with required consent (FR-ANL-004)."""

    def __init__(self, *, repository: AnalyticsRepositoryPort, clock: Clock) -> None:
        self._repo = repository
        self._clock = clock

    def handle(self, request: object) -> StitchIdentityResult:
        command = _typed(request, StitchIdentityCommand)
        organization_id = _tenant(request)
        consent = ConsentSnapshot.of(command.consent_categories)
        try:
            stitch = link_identities(
                anonymous_id=command.anonymous_id,
                user_id=command.user_id,
                consent=consent,
                created_at=self._clock(),
            )
        except ConsentNotGranted as exc:
            # Without consent, no linkage is created (deny-by-default) — reported, not persisted.
            return StitchIdentityResult(
                anonymous_id=command.anonymous_id,
                user_id=command.user_id,
                linked=False,
                reason=exc.required_category,
            )
        self._repo.add_stitch(organization_id=organization_id, stitch=stitch)
        return StitchIdentityResult(
            anonymous_id=stitch.anonymous_id,
            user_id=stitch.user_id,
            linked=True,
            reason=None,
        )


class ReportReach:
    """``analytics.report.reach`` (query) — authoritative first-party content intelligence.

    Computed entirely from persisted first-party events; it never reads GA4, so the report is
    complete with GA4 disabled/absent (GA independence, FR-ANL-002/005; EVAL-ANL-002).
    """

    def __init__(self, *, repository: AnalyticsRepositoryPort) -> None:
        self._repo = repository

    def handle(self, request: object) -> ReportReachResult:
        query = _typed(request, ReportReachQuery)
        organization_id = _tenant(request)
        events = tuple(
            self._repo.list_events(organization_id=organization_id, event_name=query.event_name)
        )
        report = build_reach_report(
            events, event_name=query.event_name, content_property=query.content_property
        )
        return ReportReachResult(
            event_name=report.event_name,
            entries=tuple(
                ReachEntryView(content_id=e.content_id, reach=e.reach, occurrences=e.occurrences)
                for e in report.entries
            ),
            total_events=report.total_events,
            source=report.source,
            authoritative=report.authoritative,
        )


class ImportGa4:
    """``analytics.ga4.import`` — import GA4 figures LABELLED non-authoritative (FR-ANL-006).

    The GA4 adapter is optional and behind a port; the returned figure is non-authoritative by
    construction and carries source freshness + the mapping that produced it. It is never returned
    as authoritative learner state (EVAL-ANL-006).
    """

    def __init__(
        self, *, repository: AnalyticsRepositoryPort, ga4_adapter: Ga4AdapterPort, clock: Clock
    ) -> None:
        self._repo = repository
        self._ga4 = ga4_adapter
        self._clock = clock

    def handle(self, request: object) -> ImportGa4Result:
        command = _typed(request, ImportGa4Command)
        organization_id = _tenant(request)
        mapping = Ga4Mapping(northstar_event=command.northstar_event, ga4_event=command.ga4_event)
        metric = self._ga4.fetch_reach(
            organization_id=organization_id,
            mapping=mapping,
            metric_name=command.metric_name,
            now=self._clock(),
        )
        # Defense-in-depth: the domain forbids an authoritative GA4 metric; assert the label here.
        assert metric.authoritative is False  # noqa: S101 non-authoritative invariant
        return ImportGa4Result(
            metric_name=metric.metric_name,
            value=metric.value,
            source=metric.source,
            authoritative=metric.authoritative,
            as_of=metric.freshness.as_of.isoformat(),
            retrieved_at=metric.freshness.retrieved_at.isoformat(),
            mapping=metric.mapping.to_dict(),
        )


__all__ = [
    "ANALYTICS_CAPABILITIES",
    "CAP_CATALOG_REGISTER",
    "CAP_EVENT_INGEST",
    "CAP_GA4_IMPORT",
    "CAP_IDENTITY_STITCH",
    "CAP_REPORT_REACH",
    "CAP_VERSION",
    "RES_ANALYTICS",
    "ImportGa4",
    "ImportGa4Command",
    "ImportGa4Result",
    "IngestEvent",
    "IngestEventCommand",
    "IngestEventResult",
    "ReachEntryView",
    "RegisterEventDefinition",
    "RegisterEventDefinitionCommand",
    "RegisterEventDefinitionResult",
    "ReportReach",
    "ReportReachQuery",
    "ReportReachResult",
    "StitchIdentity",
    "StitchIdentityCommand",
    "StitchIdentityResult",
]
