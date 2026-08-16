"""Messaging aggregate: message class, templates, segments, schedule, consent and delivery.

Mirrors the messaging domain model in docs/16 (§2 message classes, §4 templates, §5 audience,
§6 scheduling, §7 preferences/suppression, §8 provider delivery, §12 tracking). Everything here is
pure and infrastructure-free (rule 10, LAW-02): time-zone math uses only the standard-library
:mod:`zoneinfo`, so scheduling is deterministic and testable without any adapter.

Key invariants enforced by construction (never weakened):

* a published :class:`TemplateVersion` is IMMUTABLE and renders deterministically (FR-MSG-002);
* a :class:`Segment` accepts ONLY approved attributes + allowlisted operators, so a raw-query /
  arbitrary-DB segment cannot be expressed (FR-MSG-003);
* a marketing :class:`MessageClass` is suppression-governed while transactional is not (FR-MSG-001);
* a :class:`Schedule` resolves a send-at per recipient time zone (FR-MSG-004);
* open/click :class:`TrackingConfig` defaults to OFF (privacy-safe) (FR-MSG-007).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import MessagingInvariantViolation, TemplateRenderError, UnsafeSegmentError

RES_CAMPAIGN = "messaging.campaign"


def _require(condition: bool, message: str, code: str = "messaging.invariant.violated") -> None:
    if not condition:
        raise MessagingInvariantViolation(message, code=code)


# ---------------------------------------------------------------------------
# Message classification (FR-MSG-001)
# ---------------------------------------------------------------------------


class MessageClass(StrEnum):
    """Whether suppression/consent applies to a send (docs/16 §2, FR-MSG-001).

    ``MARKETING`` messages are governed by consent + suppression and can NEVER be sent to a
    suppressed / unsubscribed / non-consented recipient. ``TRANSACTIONAL`` (legitimately-required
    service) messages are handled per policy and are not blocked by a marketing opt-out.
    """

    TRANSACTIONAL = "transactional"
    MARKETING = "marketing"

    @property
    def is_suppressible(self) -> bool:
        """``True`` when consent + suppression must be enforced before sending (FR-MSG-005)."""
        return self is MessageClass.MARKETING


class SuppressionReason(StrEnum):
    """Why a recipient is suppressed (docs/16 §7)."""

    UNSUBSCRIBE = "unsubscribe"
    HARD_BOUNCE = "hard_bounce"
    COMPLAINT = "complaint"
    LEGAL_BLOCK = "legal_block"
    ADMIN_BLOCK = "admin_block"


class DeliveryStatus(StrEnum):
    """Provider-neutral delivery status values (docs/16 §8)."""

    QUEUED = "queued"
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    DEFERRED = "deferred"
    BOUNCED = "bounced"
    COMPLAINED = "complained"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class CampaignStatus(StrEnum):
    """Lifecycle of a campaign (docs/16 §11)."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENT = "sent"


# ---------------------------------------------------------------------------
# Templates (FR-MSG-002): versioned, immutable, deterministic rendering
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _escape(value: str) -> str:
    """HTML-escape a substituted value (escaped by default, docs/16 §4)."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    """The deterministic result of rendering a template version with variables."""

    subject: str
    html_body: str
    text_body: str


@dataclass(frozen=True, slots=True)
class TemplateVersion:
    """An IMMUTABLE, versioned template with typed variable slots (FR-MSG-002, docs/16 §4).

    Rendering is deterministic: the same ``(version, variables)`` always yields the same
    :class:`RenderedMessage`. HTML substitutions are escaped by default; a variable that a template
    declares but the caller omits fails closed (:class:`TemplateRenderError`) rather than sending
    malformed content (docs/16 §13). ``content_hash`` records provenance for the exact version.
    """

    template_id: str
    version: int
    subject: str
    html_body: str
    text_body: str
    required_variables: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(
            bool(self.template_id), "template_id must be non-empty", code="messaging.template.id"
        )
        _require(self.version >= 1, "template version must be >= 1", code="messaging.template.ver")
        _require(
            1 <= len(self.subject) <= 300,
            "subject must be 1..300 characters",
            code="messaging.template.subject",
        )
        _require(
            bool(self.html_body), "html_body must be non-empty", code="messaging.template.body"
        )
        _require(
            bool(self.text_body),
            "text_body (plain-text alternative) must be non-empty",
            code="messaging.template.text",
        )
        declared = set(self.required_variables)
        referenced = (
            set(_PLACEHOLDER.findall(self.subject))
            | set(_PLACEHOLDER.findall(self.html_body))
            | set(_PLACEHOLDER.findall(self.text_body))
        )
        missing = referenced - declared
        _require(
            not missing,
            f"template references undeclared variables {sorted(missing)}",
            code="messaging.template.variables",
        )

    @property
    def content_hash(self) -> str:
        """A stable content hash of the exact version (provenance / determinism check)."""
        digest = hashlib.sha256()
        for part in (
            self.template_id,
            str(self.version),
            self.subject,
            self.html_body,
            self.text_body,
        ):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()

    def render(self, variables: dict[str, str]) -> RenderedMessage:
        """Deterministically render this exact version; missing declared variables fail closed."""
        for name in self.required_variables:
            if name not in variables:
                raise TemplateRenderError(
                    f"missing required template variable {name!r} for "
                    f"{self.template_id}:{self.version}"
                )

        def _sub(text_value: str, *, escape: bool) -> str:
            def _replace(match: re.Match[str]) -> str:
                raw = str(variables.get(match.group(1), ""))
                return _escape(raw) if escape else raw

            return _PLACEHOLDER.sub(_replace, text_value)

        return RenderedMessage(
            subject=_sub(self.subject, escape=False),
            html_body=_sub(self.html_body, escape=True),
            text_body=_sub(self.text_body, escape=False),
        )


# ---------------------------------------------------------------------------
# Audience segmentation (FR-MSG-003): approved attributes only, no query surface
# ---------------------------------------------------------------------------

# The closed allowlist of attributes a campaign user may segment on. Learning difficulty, private
# notes and AI conversation content are deliberately excluded (docs/16 §5).
APPROVED_SEGMENT_ATTRIBUTES: frozenset[str] = frozenset(
    {"locale", "region", "plan_tier", "lifecycle_stage", "signup_cohort"}
)

# The closed allowlist of comparison operators. There is intentionally no "raw"/"sql"/"expr"
# operator, so a segment can never carry an arbitrary query.
APPROVED_SEGMENT_OPERATORS: frozenset[str] = frozenset({"eq", "in", "neq"})


@dataclass(frozen=True, slots=True)
class SegmentCriterion:
    """A single ``attribute <operator> value`` predicate over an APPROVED attribute (FR-MSG-003)."""

    attribute: str
    operator: str
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.attribute not in APPROVED_SEGMENT_ATTRIBUTES:
            raise UnsafeSegmentError(
                f"segment attribute {self.attribute!r} is not approved; allowed: "
                f"{sorted(APPROVED_SEGMENT_ATTRIBUTES)}"
            )
        if self.operator not in APPROVED_SEGMENT_OPERATORS:
            raise UnsafeSegmentError(
                f"segment operator {self.operator!r} is not approved; allowed: "
                f"{sorted(APPROVED_SEGMENT_OPERATORS)}"
            )
        _require(
            bool(self.values),
            "segment criterion must have at least one value",
            code="messaging.segment.values",
        )

    def matches(self, attributes: dict[str, str]) -> bool:
        actual = attributes.get(self.attribute)
        if self.operator == "eq":
            return actual == self.values[0]
        if self.operator == "neq":
            return actual != self.values[0]
        # "in"
        return actual in self.values


@dataclass(frozen=True, slots=True)
class Segment:
    """A versioned audience specification over approved attributes (docs/16 §5, FR-MSG-003).

    A segment is a conjunction (AND) of :class:`SegmentCriterion`. An empty segment matches every
    candidate (the whole audience). Because every criterion is validated against the closed
    attribute + operator allowlists at construction, a raw-query / arbitrary-DB segment is rejected
    before it can ever reach persistence.
    """

    criteria: tuple[SegmentCriterion, ...] = ()

    def matches(self, attributes: dict[str, str]) -> bool:
        return all(criterion.matches(attributes) for criterion in self.criteria)

    @staticmethod
    def from_specs(specs: tuple[dict[str, object], ...]) -> Segment:
        """Build a segment from serialized criteria, rejecting any unsafe attribute/operator."""
        criteria: list[SegmentCriterion] = []
        for spec in specs:
            if not isinstance(spec, dict):
                raise UnsafeSegmentError(
                    "segment criterion must be an object, not a raw expression"
                )
            unknown = set(spec) - {"attribute", "operator", "values", "value"}
            if unknown:
                raise UnsafeSegmentError(
                    f"segment criterion has unsupported keys {sorted(unknown)} (no query surface)"
                )
            values = spec.get("values")
            if values is None and "value" in spec:
                values = [spec["value"]]
            if not isinstance(values, (list, tuple)):
                raise UnsafeSegmentError("segment criterion values must be a list of scalars")
            criteria.append(
                SegmentCriterion(
                    attribute=str(spec.get("attribute", "")),
                    operator=str(spec.get("operator", "")),
                    values=tuple(str(v) for v in values),
                )
            )
        return Segment(criteria=tuple(criteria))

    def to_specs(self) -> list[dict[str, object]]:
        return [
            {"attribute": c.attribute, "operator": c.operator, "values": list(c.values)}
            for c in self.criteria
        ]


# ---------------------------------------------------------------------------
# Scheduling (FR-MSG-004): recipient time-zone resolution
# ---------------------------------------------------------------------------


class ScheduleKind(StrEnum):
    """How a campaign's send-at is resolved (docs/16 §6)."""

    IMMEDIATE = "immediate"
    ABSOLUTE_UTC = "absolute_utc"
    RECIPIENT_LOCAL = "recipient_local"


@dataclass(frozen=True, slots=True)
class Schedule:
    """When a campaign sends, resolved per recipient time zone where required (FR-MSG-004).

    * ``IMMEDIATE`` — resolves to the ``now`` supplied at send time.
    * ``ABSOLUTE_UTC`` — a fixed timezone-aware UTC instant, identical for every recipient.
    * ``RECIPIENT_LOCAL`` — a wall-clock ``local_date`` + ``local_time`` interpreted in EACH
      recipient's IANA time zone, so learners in different zones receive it at the same local hour.
    """

    kind: ScheduleKind
    absolute_utc: datetime | None = None
    local_date: str | None = None  # ISO date, e.g. "2026-09-01"
    local_time: str | None = None  # "HH:MM"

    def __post_init__(self) -> None:
        if self.kind is ScheduleKind.ABSOLUTE_UTC:
            _require(
                self.absolute_utc is not None and self.absolute_utc.tzinfo is not None,
                "absolute_utc schedule requires a timezone-aware UTC instant",
                code="messaging.schedule.absolute",
            )
        if self.kind is ScheduleKind.RECIPIENT_LOCAL:
            _require(
                bool(self.local_date) and bool(self.local_time),
                "recipient_local schedule requires local_date and local_time",
                code="messaging.schedule.local",
            )

    def resolve_for(self, *, recipient_timezone: str, now: datetime) -> datetime:
        """Resolve the timezone-aware UTC send-at instant for a recipient (deterministic)."""
        if self.kind is ScheduleKind.IMMEDIATE:
            return now.astimezone(UTC)
        if self.kind is ScheduleKind.ABSOLUTE_UTC and self.absolute_utc is not None:
            return self.absolute_utc.astimezone(UTC)
        # RECIPIENT_LOCAL (local_date/local_time guaranteed non-None by __post_init__)
        try:
            zone = ZoneInfo(recipient_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise MessagingInvariantViolation(
                f"unknown recipient time zone {recipient_timezone!r}",
                code="messaging.schedule.timezone",
            ) from exc
        local_date = self.local_date or ""
        local_time = self.local_time or ""
        try:
            year, month, day = (int(p) for p in local_date.split("-"))
            hour, minute = (int(p) for p in local_time.split(":"))
            local_dt = datetime(year, month, day, hour, minute, tzinfo=zone)
        except ValueError as exc:
            raise MessagingInvariantViolation(
                "malformed local_date/local_time in schedule",
                code="messaging.schedule.local_format",
            ) from exc
        return local_dt.astimezone(UTC)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "absolute_utc": self.absolute_utc.isoformat() if self.absolute_utc else None,
            "local_date": self.local_date,
            "local_time": self.local_time,
        }

    @staticmethod
    def from_dict(raw: dict[str, object]) -> Schedule:
        absolute = raw.get("absolute_utc")
        absolute_dt = datetime.fromisoformat(str(absolute)) if absolute else None
        return Schedule(
            kind=ScheduleKind(str(raw.get("kind", ScheduleKind.IMMEDIATE.value))),
            absolute_utc=absolute_dt,
            local_date=(str(raw["local_date"]) if raw.get("local_date") else None),
            local_time=(str(raw["local_time"]) if raw.get("local_time") else None),
        )


IMMEDIATE_SCHEDULE = Schedule(kind=ScheduleKind.IMMEDIATE)


# ---------------------------------------------------------------------------
# Tracking (FR-MSG-007): configurable, privacy-safe (off) by default
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrackingConfig:
    """Per-campaign open/click tracking; both default OFF (privacy-safe, FR-MSG-007)."""

    open_tracking: bool = False
    click_tracking: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {"open_tracking": self.open_tracking, "click_tracking": self.click_tracking}

    @staticmethod
    def from_dict(raw: dict[str, object] | None) -> TrackingConfig:
        raw = raw or {}
        return TrackingConfig(
            open_tracking=bool(raw.get("open_tracking", False)),
            click_tracking=bool(raw.get("click_tracking", False)),
        )


# ---------------------------------------------------------------------------
# Preferences / suppression (FR-MSG-005)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    """A recipient's consent decision for a channel + purpose (docs/16 §7, FR-MSG-005)."""

    organization_id: str
    recipient_id: str
    channel: str
    purpose: str
    consented: bool

    def __post_init__(self) -> None:
        _require(bool(self.recipient_id), "recipient_id required", code="messaging.consent.rid")
        _require(bool(self.channel), "channel required", code="messaging.consent.channel")
        _require(bool(self.purpose), "purpose required", code="messaging.consent.purpose")


@dataclass(frozen=True, slots=True)
class SuppressionEntry:
    """A recipient suppressed from marketing sends (docs/16 §7, FR-MSG-005)."""

    organization_id: str
    recipient_id: str
    reason: SuppressionReason

    def __post_init__(self) -> None:
        _require(bool(self.recipient_id), "recipient_id required", code="messaging.suppress.rid")


# ---------------------------------------------------------------------------
# Recipients and delivery (FR-MSG-006)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Recipient:
    """A send candidate: address + IANA time zone + segmentation attributes (docs/16 §5/§6)."""

    recipient_id: str
    address: str
    timezone: str = "UTC"
    attributes: dict[str, str] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(bool(self.recipient_id), "recipient_id required", code="messaging.recipient.id")
        _require(bool(self.address), "recipient address required", code="messaging.recipient.addr")


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """Immutable evidence of one (campaign, recipient, idempotency-key) submission (FR-MSG-006)."""

    organization_id: str
    campaign_id: str
    recipient_id: str
    idempotency_key: str
    provider_message_id: str
    status: DeliveryStatus
    send_at: datetime

    def __post_init__(self) -> None:
        _require(
            bool(self.idempotency_key),
            "idempotency_key required",
            code="messaging.delivery.key",
        )


# ---------------------------------------------------------------------------
# Campaign aggregate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Campaign:
    """A campaign binding an EXACT template version, segment, schedule and tracking (docs/16 §3).

    ``template_version`` pins the immutable version the campaign will render; changing a template
    later never changes what a bound campaign sends (FR-MSG-002).
    """

    organization_id: str
    campaign_id: str
    name: str
    message_class: MessageClass
    template_id: str
    template_version: int
    channel: str = "email"
    purpose: str = "marketing"
    segment: Segment = field(default_factory=Segment)
    schedule: Schedule = IMMEDIATE_SCHEDULE
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    status: CampaignStatus = CampaignStatus.DRAFT

    def __post_init__(self) -> None:
        _require(bool(self.campaign_id), "campaign_id required", code="messaging.campaign.id")
        _require(
            1 <= len(self.name) <= 200,
            "campaign name must be 1..200 characters",
            code="messaging.campaign.name",
        )
        _require(
            self.template_version >= 1,
            "campaign must bind a template version >= 1",
            code="messaging.campaign.version",
        )


def default_local_time() -> time:
    """A conventional default local send time (09:00) for recipient-local schedules."""
    return time(hour=9, minute=0)
