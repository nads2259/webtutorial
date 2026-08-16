"""Analytics domain model: event catalog, ingestion pipeline, identity stitching, reach and GA4.

Mirrors docs/17: §2 first-party authoritative principle, §3 event catalog, §4 ingestion/validation,
§5 consent-aware identity stitching, §8 content intelligence (reach) and §9 the GA4 adapter. Every
type here is pure and infrastructure-free (rule 10, LAW-02): the domain enforces the same invariants
as ``spec/contracts/schemas/analytics-event-definition.schema.json`` by construction, and a contract
test independently validates a produced definition dict against that JSON Schema.

Key invariants enforced by construction (never weakened):

* an :class:`AnalyticsEventDefinition` MUST declare a purpose and validate against the catalog
  schema; a purpose-less / malformed definition is rejected at registration (FR-ANL-003);
* the :class:`EventCatalog` VALIDATES each emitted :class:`AnalyticsEvent` against its definition
  and rejects an unknown-type / malformed / unknown-property / wrong-type event (FR-ANL-007);
* first-party events are the authoritative source; :func:`build_reach_report` computes complete
  content intelligence with NO external dependency (FR-ANL-001/002/005);
* :func:`link_identities` only links identities WITH the required consent (FR-ANL-004);
* a :class:`Ga4Metric` can NEVER be authoritative and always carries source freshness + mapping
  (FR-ANL-006).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .errors import (
    CatalogValidationError,
    ConsentNotGranted,
    Ga4AuthorityViolation,
    PipelineValidationError,
    PurposeRequired,
    StitchInvariantViolation,
    UnknownEventType,
)

# Resource vocabulary (stable contract): the analytics stream is the tenant-scoped resource.
RES_ANALYTICS = "analytics.stream"

_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]+$")
_MIN_PURPOSE_LEN = 10


# ---------------------------------------------------------------------------
# Enumerated vocabularies (mirror analytics-event-definition.schema.json)
# ---------------------------------------------------------------------------


class ConsentCategory(StrEnum):
    """Consent category an event definition (and stitch) is governed by (docs/17 §3/§5)."""

    NECESSARY = "necessary"
    PREFERENCES = "preferences"
    ANALYTICS = "analytics"
    PERSONALIZATION = "personalization"
    MARKETING = "marketing"


class PropertyType(StrEnum):
    """Allowed analytics property types (schema ``properties.*.type``)."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"
    ID = "id"


class DataClassification(StrEnum):
    """Data classification for a property (schema ``dataClassification``)."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


# Consent category REQUIRED before anonymous↔user identity stitching may link (docs/17 §5).
REQUIRED_STITCH_CONSENT = ConsentCategory.ANALYTICS


# ---------------------------------------------------------------------------
# Event catalog definition (FR-ANL-003)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PropertySpec:
    """One allowed event property: its type + data classification (schema ``properties.*``)."""

    type: PropertyType
    classification: DataClassification
    required: bool = False

    @staticmethod
    def from_dict(raw: object) -> PropertySpec:
        if not isinstance(raw, dict):
            raise CatalogValidationError("each property definition must be an object")
        unknown = set(raw) - {"type", "classification", "required"}
        if unknown:
            raise CatalogValidationError(
                f"property definition has unsupported keys {sorted(unknown)}"
            )
        try:
            ptype = PropertyType(str(raw.get("type", "")))
            classification = DataClassification(str(raw.get("classification", "")))
        except ValueError as exc:
            raise CatalogValidationError(f"invalid property type/classification: {exc}") from exc
        return PropertySpec(
            type=ptype, classification=classification, required=bool(raw.get("required", False))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type.value,
            "classification": self.classification.value,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class AnalyticsEventDefinition:
    """A catalog event type: purpose-governed, schema-valid, with typed classified properties.

    Construction enforces the same invariants as
    ``spec/contracts/schemas/analytics-event-definition.schema.json``. A definition without a
    declared purpose (or with a too-short purpose) is rejected with :class:`PurposeRequired`
    (FR-ANL-003); any other schema violation raises :class:`CatalogValidationError`.
    ``prohibited_free_text`` is always ``True`` (raw free text is never an allowed property value).
    """

    event_name: str
    version: int
    owner: str
    purpose: str
    consent_category: ConsentCategory
    retention_days: int
    destinations: tuple[str, ...] = ()
    properties: dict[str, PropertySpec] = field(default_factory=dict)
    trigger: str | None = None
    sampling: float | None = None
    prohibited_free_text: bool = True

    def __post_init__(self) -> None:
        if not _EVENT_NAME.match(self.event_name):
            raise CatalogValidationError(
                f"event_name {self.event_name!r} must match ^[a-z][a-z0-9_]+$"
            )
        if self.version < 1:
            raise CatalogValidationError("event definition version must be >= 1")
        if len(self.owner.strip()) < 1:
            raise CatalogValidationError("event definition must declare a non-empty owner")
        if len(self.purpose.strip()) < _MIN_PURPOSE_LEN:
            raise PurposeRequired()
        if not isinstance(self.consent_category, ConsentCategory):
            raise CatalogValidationError("consent_category must be a valid ConsentCategory")
        if self.retention_days < 0:
            raise CatalogValidationError("retention_days must be >= 0")
        if len(set(self.destinations)) != len(self.destinations):
            raise CatalogValidationError("destinations must be unique")
        if self.sampling is not None and not (0.0 <= self.sampling <= 1.0):
            raise CatalogValidationError("sampling must be within [0, 1]")
        if self.prohibited_free_text is not True:
            raise CatalogValidationError("prohibited_free_text must be true")

    @property
    def required_properties(self) -> tuple[str, ...]:
        return tuple(name for name, spec in self.properties.items() if spec.required)

    def to_definition_dict(self) -> dict[str, object]:
        """Serialize to a dict shaped for ``analytics-event-definition.schema.json`` validation."""
        payload: dict[str, object] = {
            "event_name": self.event_name,
            "version": self.version,
            "owner": self.owner,
            "purpose": self.purpose,
            "consent_category": self.consent_category.value,
            "properties": {name: spec.to_dict() for name, spec in self.properties.items()},
            "retention_days": self.retention_days,
            "destinations": list(self.destinations),
            "prohibited_free_text": self.prohibited_free_text,
        }
        if self.trigger is not None:
            payload["trigger"] = self.trigger
        if self.sampling is not None:
            payload["sampling"] = self.sampling
        return payload

    @staticmethod
    def from_dict(raw: dict[str, object]) -> AnalyticsEventDefinition:
        """Build a definition from serialized fields, enforcing the catalog invariants."""
        if "purpose" not in raw or not str(raw.get("purpose", "")).strip():
            raise PurposeRequired()
        try:
            consent = ConsentCategory(str(raw.get("consent_category", "")))
        except ValueError as exc:
            raise CatalogValidationError(
                f"invalid consent_category {raw.get('consent_category')!r}"
            ) from exc
        raw_props = raw.get("properties") or {}
        if not isinstance(raw_props, dict):
            raise CatalogValidationError("properties must be an object")
        properties = {str(name): PropertySpec.from_dict(spec) for name, spec in raw_props.items()}
        destinations = raw.get("destinations") or []
        if not isinstance(destinations, (list, tuple)):
            raise CatalogValidationError("destinations must be an array")
        sampling = raw.get("sampling")
        return AnalyticsEventDefinition(
            event_name=str(raw.get("event_name", "")),
            version=int(raw.get("version", 0)),
            owner=str(raw.get("owner", "")),
            purpose=str(raw.get("purpose", "")),
            consent_category=consent,
            retention_days=int(raw.get("retention_days", 0)),
            destinations=tuple(str(d) for d in destinations),
            properties=properties,
            trigger=(str(raw["trigger"]) if raw.get("trigger") is not None else None),
            sampling=(float(sampling) if sampling is not None else None),
            prohibited_free_text=bool(raw.get("prohibited_free_text", True)),
        )


# ---------------------------------------------------------------------------
# Emitted events + validating pipeline (FR-ANL-007)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnalyticsEvent:
    """A single first-party observation validated against a catalog definition (docs/17 §4)."""

    event_name: str
    event_version: int
    occurred_at: datetime
    actor_type: str
    actor_id: str
    properties: dict[str, object] = field(default_factory=dict)
    anonymous_id: str | None = None
    event_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_name:
            raise PipelineValidationError("event_name is required")
        if not self.actor_id and not self.anonymous_id:
            raise PipelineValidationError("event requires an actor_id or an anonymous_id")

    @property
    def subject_key(self) -> str:
        """A stable per-subject key for distinct-reach counting (user id, else anonymous id)."""
        return self.actor_id or self.anonymous_id or ""


def _value_matches_type(value: object, ptype: PropertyType) -> bool:
    if ptype is PropertyType.BOOLEAN:
        return isinstance(value, bool)
    if ptype is PropertyType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if ptype is PropertyType.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if ptype in (PropertyType.STRING, PropertyType.ID):
        return isinstance(value, str)
    if ptype is PropertyType.TIMESTAMP:
        if isinstance(value, datetime):
            return True
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value)
            except ValueError:
                return False
            return True
        return False
    return False


class EventCatalog:
    """The registered set of event definitions + the authoritative validating pipeline.

    ``validate`` is deny-by-default: an event whose type is not registered, that omits a required
    property, that carries a property outside the definition's allowlist, or whose property value
    has the wrong type is REJECTED (never silently accepted) — FR-ANL-007, EVAL-ANL-001/003/007.
    """

    def __init__(self, definitions: tuple[AnalyticsEventDefinition, ...] = ()) -> None:
        self._by_name: dict[str, AnalyticsEventDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: AnalyticsEventDefinition) -> None:
        self._by_name[definition.event_name] = definition

    def get(self, event_name: str) -> AnalyticsEventDefinition | None:
        return self._by_name.get(event_name)

    def definitions(self) -> tuple[AnalyticsEventDefinition, ...]:
        return tuple(self._by_name.values())

    def validate(self, event: AnalyticsEvent) -> AnalyticsEventDefinition:
        """Validate ``event`` against its catalog definition; return it or raise (reject)."""
        definition = self.get(event.event_name)
        if definition is None:
            raise UnknownEventType(event.event_name)
        if event.event_version != definition.version:
            raise PipelineValidationError(
                f"event {event.event_name!r} version {event.event_version} does not match the "
                f"catalog version {definition.version}"
            )
        allowed = set(definition.properties)
        unknown = set(event.properties) - allowed
        if unknown:
            raise PipelineValidationError(
                f"event {event.event_name!r} carries properties outside the catalog allowlist: "
                f"{sorted(unknown)}"
            )
        for name in definition.required_properties:
            if name not in event.properties:
                raise PipelineValidationError(
                    f"event {event.event_name!r} is missing required property {name!r}"
                )
        for name, value in event.properties.items():
            spec = definition.properties[name]
            if not _value_matches_type(value, spec.type):
                raise PipelineValidationError(
                    f"event {event.event_name!r} property {name!r} must be of type "
                    f"{spec.type.value}"
                )
        return definition


# ---------------------------------------------------------------------------
# Consent-aware identity stitching (FR-ANL-004)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsentSnapshot:
    """The consent categories a subject has granted at stitch time (docs/17 §5)."""

    granted: frozenset[str] = frozenset()

    def allows(self, category: ConsentCategory) -> bool:
        return category.value in self.granted

    @staticmethod
    def of(categories: object) -> ConsentSnapshot:
        if categories is None:
            return ConsentSnapshot()
        if isinstance(categories, (list, tuple, set, frozenset)):
            return ConsentSnapshot(frozenset(str(c) for c in categories))
        raise StitchInvariantViolation("consent snapshot must be a list of category names")


@dataclass(frozen=True, slots=True)
class IdentityStitch:
    """An explicit, consent-backed link between an anonymous id and a user id (docs/17 §5).

    A stitch is only ever constructed through :func:`link_identities`, which refuses to link
    without the required consent. GA/user-provider identifiers are mappings, not internal keys.
    """

    anonymous_id: str
    user_id: str
    consent_category: str
    created_at: datetime


def link_identities(
    *,
    anonymous_id: str,
    user_id: str,
    consent: ConsentSnapshot,
    created_at: datetime,
    required: ConsentCategory = REQUIRED_STITCH_CONSENT,
) -> IdentityStitch:
    """Link an anonymous id to a user id — ONLY with the required consent (FR-ANL-004).

    Without ``required`` consent the link is refused (:class:`ConsentNotGranted`) and no
    :class:`IdentityStitch` is produced. Degenerate identifiers (empty, or anonymous == user) are
    rejected so unrelated identities are never silently merged.
    """
    if not anonymous_id or not user_id:
        raise StitchInvariantViolation("both anonymous_id and user_id are required to stitch")
    if anonymous_id == user_id:
        raise StitchInvariantViolation("anonymous_id and user_id must be distinct identifiers")
    if not consent.allows(required):
        raise ConsentNotGranted(required.value)
    return IdentityStitch(
        anonymous_id=anonymous_id,
        user_id=user_id,
        consent_category=required.value,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Content intelligence — reach (FR-ANL-005), first-party and authoritative
# ---------------------------------------------------------------------------

DEFAULT_CONTENT_PROPERTY = "content_id"


@dataclass(frozen=True, slots=True)
class ReachEntry:
    """Reach for one content unit: distinct subjects reached and total occurrences."""

    content_id: str
    reach: int
    occurrences: int


@dataclass(frozen=True, slots=True)
class ReachReport:
    """Authoritative first-party reach report (docs/17 §8).

    ``authoritative`` is always ``True`` and ``source`` is always ``"first_party"``: this report is
    computed entirely from persisted first-party events with NO external dependency
    (FR-ANL-001/002).
    """

    event_name: str
    entries: tuple[ReachEntry, ...]
    total_events: int
    source: str = "first_party"
    authoritative: bool = True

    def __post_init__(self) -> None:
        if self.source != "first_party" or self.authoritative is not True:
            raise CatalogValidationError(
                "a reach report is always the authoritative first-party source"
            )


def build_reach_report(
    events: tuple[AnalyticsEvent, ...],
    *,
    event_name: str,
    content_property: str = DEFAULT_CONTENT_PROPERTY,
) -> ReachReport:
    """Aggregate distinct-subject reach + occurrences per content unit from first-party events."""
    subjects_by_content: dict[str, set[str]] = {}
    occurrences_by_content: dict[str, int] = {}
    total = 0
    for event in events:
        if event.event_name != event_name:
            continue
        total += 1
        content_value = event.properties.get(content_property)
        if content_value is None:
            continue
        content_id = str(content_value)
        subjects_by_content.setdefault(content_id, set()).add(event.subject_key)
        occurrences_by_content[content_id] = occurrences_by_content.get(content_id, 0) + 1
    entries = tuple(
        ReachEntry(
            content_id=content_id,
            reach=len(subjects_by_content[content_id]),
            occurrences=occurrences_by_content[content_id],
        )
        for content_id in sorted(subjects_by_content)
    )
    return ReachReport(event_name=event_name, entries=entries, total_events=total)


# ---------------------------------------------------------------------------
# GA4 value objects (FR-ANL-006): optional, non-authoritative, freshness + mapping
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ga4Mapping:
    """The mapping (and version) from a first-party event to a GA4 event (docs/17 §9)."""

    northstar_event: str
    ga4_event: str
    mapping_version: str = "1.0.0"

    def to_dict(self) -> dict[str, str]:
        return {
            "northstar_event": self.northstar_event,
            "ga4_event": self.ga4_event,
            "mapping_version": self.mapping_version,
        }


@dataclass(frozen=True, slots=True)
class SourceFreshness:
    """When a GA4-derived figure was measured (``as_of``) and retrieved (``retrieved_at``)."""

    as_of: datetime
    retrieved_at: datetime

    def to_dict(self) -> dict[str, str]:
        return {"as_of": self.as_of.isoformat(), "retrieved_at": self.retrieved_at.isoformat()}


@dataclass(frozen=True, slots=True)
class Ga4Metric:
    """An imported GA4 aggregate figure — ALWAYS non-authoritative, with freshness + mapping.

    ``authoritative`` is forced to ``False`` at construction (FR-ANL-006, EVAL-ANL-006): a GA4
    value can never be presented as authoritative learner state, and it always carries its source
    freshness and the mapping that produced it.
    """

    metric_name: str
    value: float
    mapping: Ga4Mapping
    freshness: SourceFreshness
    source: str = "ga4"
    authoritative: bool = False

    def __post_init__(self) -> None:
        if self.authoritative is not False or self.source != "ga4":
            raise Ga4AuthorityViolation()


__all__ = [
    "DEFAULT_CONTENT_PROPERTY",
    "REQUIRED_STITCH_CONSENT",
    "RES_ANALYTICS",
    "AnalyticsEvent",
    "AnalyticsEventDefinition",
    "ConsentCategory",
    "ConsentSnapshot",
    "DataClassification",
    "EventCatalog",
    "Ga4Mapping",
    "Ga4Metric",
    "IdentityStitch",
    "PropertySpec",
    "PropertyType",
    "ReachEntry",
    "ReachReport",
    "SourceFreshness",
    "build_reach_report",
    "link_identities",
]
