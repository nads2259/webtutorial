"""The reference tutorial product profile — pure, declarative composition data (ARCH-024).

This module is intentionally infrastructure-free: it declares WHAT the tutorial product is
(theme tokens, typed configuration, a learning taxonomy, declared SLOs, the seed content and
the offer/campaign/provider selections) as plain frozen value objects. The assembly
(:mod:`northstar.products.reference.assembly`) and seeder
(:mod:`northstar.products.reference.seed`) turn this declaration into a running product by
COMPOSING released module capabilities — never by forking kernel/module source.

A theme changes PRESENTATION, never policy (docs/39 §4): the tokens below are colour/spacing/
typography hints only. Configuration values are typed product knobs. Nothing here authorises an
action or bypasses a capability; authorisation always flows through the kernel policy engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Presentation-only design tokens (never policy). Consumed by the web/Studio shells."""

    name: str
    color_primary: str
    color_surface: str
    color_on_surface: str
    font_body: str
    font_heading: str
    radius_px: int
    # WCAG 2.2 AA target contrast ratio the theme's colour pairs are chosen to meet (a11y is an
    # acceptance criterion, not a late add-on — LAW-08). Presentation, not authorization.
    min_contrast_ratio: float = 4.5


@dataclass(frozen=True, slots=True)
class SloTarget:
    """A declared service-level objective for the assembled product (NFR-OPS-006 input)."""

    name: str
    objective: str
    target: str
    window: str


@dataclass(frozen=True, slots=True)
class TaxonomyPath:
    """A learning path inside a domain (domain -> path -> course structure)."""

    path_id: str
    title: str
    slug: str


@dataclass(frozen=True, slots=True)
class TaxonomyDomain:
    """A top-level knowledge domain that groups paths (docs/39 required journey #3)."""

    domain_id: str
    title: str
    slug: str
    paths: tuple[TaxonomyPath, ...]


@dataclass(frozen=True, slots=True)
class SectionSeed:
    """One tutorial section, backed by a published knowledge revision + a stable block id."""

    section_id: str
    title: str
    block_id: str
    body: str


@dataclass(frozen=True, slots=True)
class AssessmentItemSeed:
    """A single-choice assessment item that gates course completion via an explicit rule."""

    item_id: str
    version: str
    kind: str
    prompt: str
    choices: tuple[str, ...]
    answer_key: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TutorialSeed:
    """A small tutorial: domain -> path -> course composed from published knowledge revisions."""

    domain_id: str
    domain_title: str
    path_id: str
    course_id: str
    course_title: str
    document_type: str
    locale: str
    sections: tuple[SectionSeed, ...]
    completion_rule_id: str
    assessment: AssessmentItemSeed


@dataclass(frozen=True, slots=True)
class OfferSeed:
    """A commerce offer (packaging) that grants a capability on purchase (>= 1 required)."""

    offer_id: str
    version: str
    product_name: str
    product_kind: str
    # Canonical offer document validated by the commerce module against its offer schema.
    document: dict[str, object]


@dataclass(frozen=True, slots=True)
class CampaignSeed:
    """A localized template + consent-filtered audience + periodic schedule (docs/39 #12)."""

    template_id: str
    template_version: int
    subject: str
    html_body: str
    text_body: str
    required_variables: tuple[str, ...]
    campaign_name: str
    message_class: str
    channel: str
    purpose: str
    segment_specs: tuple[dict[str, object], ...]
    schedule: dict[str, object]


@dataclass(frozen=True, slots=True)
class AnalyticsEventSeed:
    """A purpose-governed, consent-categorised first-party analytics event definition."""

    definition: dict[str, object]


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    """A declared provider/adapter selection realised by the released composition root.

    The reference product does not re-implement providers; it records the adapter each module
    port is bound to in ``northstar.processes.api.wiring`` (a real deployment swaps the adapter
    behind the SAME port with no product change — LAW-12).
    """

    port: str
    adapter: str
    note: str


@dataclass(frozen=True, slots=True)
class ReferenceProductProfile:
    """The whole tutorial product declaration, composed from released modules at assembly time."""

    product_id: str
    name: str
    description: str
    theme: ThemeTokens
    configuration: dict[str, object]
    taxonomy: tuple[TaxonomyDomain, ...]
    slos: tuple[SloTarget, ...]
    tutorial: TutorialSeed
    offer: OfferSeed
    campaign: CampaignSeed
    analytics_event: AnalyticsEventSeed
    provider_selections: tuple[ProviderSelection, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------------------------
# The canonical reference tutorial product instance (docs/39). Small, deterministic, honest.
# --------------------------------------------------------------------------------------------

_TUTORIAL = TutorialSeed(
    domain_id="dom-biology",
    domain_title="Biology",
    path_id="path-cell-biology",
    course_id="course-photosynthesis-101",
    course_title="Photosynthesis 101",
    document_type="knowledge_page",
    locale="en",
    sections=(
        SectionSeed(
            section_id="sec-intro",
            title="Introduction to Photosynthesis",
            block_id="blk-intro-0001",
            body="Photosynthesis converts light energy into chemical energy stored as glucose.",
        ),
        SectionSeed(
            section_id="sec-light",
            title="The Light-Dependent Reactions",
            block_id="blk-light-0001",
            body="In the thylakoid membranes, light excites electrons to build ATP and NADPH.",
        ),
    ),
    completion_rule_id="rule-photosynthesis-complete",
    assessment=AssessmentItemSeed(
        item_id="item-energy-store",
        version="1.0.0",
        kind="single_choice",
        prompt="In what molecule is the chemical energy of photosynthesis stored?",
        choices=("glucose", "water"),
        answer_key=("glucose",),
    ),
)

_OFFER = OfferSeed(
    offer_id="offer-photosynthesis-premium",
    version="1.0.0",
    product_name="Photosynthesis 101 — Premium",
    product_kind="course",
    document={
        "offer_id": "offer-photosynthesis-premium",
        "version": "1.0.0",
        "product_id": "course-photosynthesis-101",
        "status": "active",
        "price": {"amount_minor": 4999, "currency": "USD", "billing_type": "one_time"},
        "grants": [{"capability": "knowledge.object", "scope": {}}],
        "terms_version": "2026-01",
    },
)

_CAMPAIGN = CampaignSeed(
    template_id="tpl-weekly-digest",
    template_version=1,
    subject="Your weekly learning digest",
    html_body="<h1>Hello {{first_name}}</h1><p>Continue {{course_title}}.</p>",
    text_body="Hello {{first_name}} — continue {{course_title}}.",
    required_variables=("first_name", "course_title"),
    campaign_name="Weekly learning digest",
    message_class="marketing",
    channel="email",
    purpose="marketing",
    segment_specs=({"attribute": "locale", "operator": "eq", "values": ["en"]},),
    # A recipient-time-zone-aware local schedule (FR-MSG-004): every learner receives the digest at
    # the same local hour. (True cron/periodic recurrence is not a released capability; the
    # scheduling supported by the messaging module is immediate/absolute-UTC/recipient-local.)
    schedule={"kind": "recipient_local", "local_date": "2026-09-07", "local_time": "09:00"},
)

_ANALYTICS_EVENT = AnalyticsEventSeed(
    definition={
        "event_name": "content_block_reached",
        "version": 1,
        "owner": "learning-analytics",
        "purpose": "measure which content blocks learners reach for content intelligence",
        "consent_category": "analytics",
        "properties": {
            "content_id": {"type": "id", "classification": "internal", "required": True},
            "position": {"type": "integer", "classification": "internal"},
        },
        "retention_days": 400,
        "destinations": ["first_party"],
        "prohibited_free_text": True,
    },
)

REFERENCE_PRODUCT_PROFILE = ReferenceProductProfile(
    product_id="northstar-reference-tutorial",
    name="Northstar Reference Tutorial",
    description=(
        "The reference tutorial/learning product that proves Northstar can assemble a compelling "
        "experience purely by composing released modules (docs/39)."
    ),
    theme=ThemeTokens(
        name="aurora",
        color_primary="#1f6feb",
        color_surface="#ffffff",
        color_on_surface="#0b1721",
        font_body="Inter, system-ui, sans-serif",
        font_heading="Inter, system-ui, sans-serif",
        radius_px=12,
        min_contrast_ratio=4.5,
    ),
    configuration={
        "default_locale": "en",
        "supported_locales": ("en", "es"),
        "anonymous_analytics": True,
        "require_consent_for_marketing": True,
        "ai_tutor_enabled": True,
    },
    taxonomy=(
        TaxonomyDomain(
            domain_id="dom-biology",
            title="Biology",
            slug="biology",
            paths=(
                TaxonomyPath(
                    path_id="path-cell-biology", title="Cell Biology", slug="cell-biology"
                ),
            ),
        ),
    ),
    slos=(
        SloTarget(
            name="content_read_availability",
            objective="Public tutorial content is readable",
            target="99.9%",
            window="30d",
        ),
        SloTarget(
            name="command_latency_p95",
            objective="Authoritative write path stays responsive",
            target="<= 300ms",
            window="30d",
        ),
        SloTarget(
            name="ai_answer_citation_correctness",
            objective="AI tutor answers stay grounded",
            target=">= 0.95",
            window="30d",
        ),
    ),
    tutorial=_TUTORIAL,
    offer=_OFFER,
    campaign=_CAMPAIGN,
    analytics_event=_ANALYTICS_EVENT,
    provider_selections=(
        ProviderSelection(
            port="OidcProviderPort",
            adapter="MockOidcProvider",
            note="Deterministic reference IdP; a real OIDC IdP is a drop-in adapter swap.",
        ),
        ProviderSelection(
            port="WebhookVerifierPort",
            adapter="HmacWebhookVerifier(reference)",
            note="Reference payment provider 'reference'; Stripe/etc. swap behind the same port.",
        ),
        ProviderSelection(
            port="MessageProviderPort",
            adapter="InMemoryMessageProvider",
            note="Deterministic reference ESP; a real ESP/SMS/push provider is an adapter swap.",
        ),
        ProviderSelection(
            port="Ga4AdapterPort",
            adapter="InMemoryGa4Adapter",
            note="Non-authoritative GA4 import adapter; a real GA4 Data API adapter is a swap.",
        ),
    ),
)
