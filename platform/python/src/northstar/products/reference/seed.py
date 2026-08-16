"""Seed the reference tutorial product ENTIRELY through released module capabilities.

The seeder never touches a module's tables or internals directly: every write is a
:class:`northstar.kernel.messaging.Command` dispatched on the composed command bus (deny-by-default
policy + tamper-evident audit + single authoritative capability per action). It seeds, for one
tenant:

* a small tutorial ``domain -> path -> course`` composed from PUBLISHED knowledge revisions
  (knowledge create/submit/publish, then ``learning.course.compose`` + ``learning.course.publish``
  + an assessment item);
* at least one commerce offer (``commerce.offer.publish``);
* at least one campaign — a localized template + consent-filtered audience + periodic schedule
  (``template.publish`` + ``campaign.create`` + ``campaign.schedule``);
* the first-party analytics event catalog entry (``analytics.catalog.register``) backing the
  product's consent-aware analytics — a declared provider selection realised by the composition
  root (GA4/messaging/payment providers are adapters behind their ports).

Each dispatch returns the authoritative audit record, which the seeder collects so callers can
prove audit completeness across the assembled product (LAW-14, EVAL-AUD-001).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from northstar.kernel.audit.ports import AuditRecord
from northstar.kernel.context import Actor, ActorType, RequestContext
from northstar.kernel.messaging import Command, CommandBus
from northstar.modules.analytics.application import capabilities as analytics
from northstar.modules.commerce.application import capabilities as commerce
from northstar.modules.knowledge.application import capabilities as knowledge
from northstar.modules.learning.application import capabilities as learning
from northstar.modules.messaging.application import capabilities as messaging

from .profile import REFERENCE_PRODUCT_PROFILE, ReferenceProductProfile

# The seed actor is an authenticated author/operator acting inside a tenant. Tenant scope is taken
# from the authenticated context, never a payload (rule 50); the released grants authorise these
# content/commerce/messaging/analytics actions for any authenticated actor.
SEED_ACTOR_ID = "reference-product-seeder"


@dataclass(frozen=True, slots=True)
class SeededSection:
    """A published section: the knowledge object/revision + the stable block id it exposes."""

    section_id: str
    title: str
    object_id: str
    revision_id: str
    block_id: str


@dataclass(frozen=True, slots=True)
class SeedReceipt:
    """What the seeder created, plus the audit records every write produced (evidence)."""

    tenant: str
    course_id: str
    domain_id: str
    path_id: str
    sections: tuple[SeededSection, ...]
    completion_rule_id: str
    assessment_item_id: str
    assessment_item_version: str
    offer_id: str
    offer_version: str
    template_id: str
    campaign_id: str
    analytics_event_name: str
    audit_records: tuple[AuditRecord, ...] = field(default_factory=tuple)


def _block(block_id: str, body: str) -> dict[str, object]:
    """A canonical paragraph block with a STABLE id (LAW-06 stable block identity)."""
    return {
        "id": block_id,
        "type": "paragraph",
        "version": 1,
        "data": {"attributes": {}, "content": body},
        "children": [],
    }


class ReferenceProductSeeder:
    """Seeds the reference product's content/offer/campaign/analytics via the command bus only."""

    def __init__(
        self,
        *,
        command_bus: CommandBus,
        profile: ReferenceProductProfile = REFERENCE_PRODUCT_PROFILE,
    ) -> None:
        self._bus = command_bus
        self._profile = profile
        self._audit: list[AuditRecord] = []

    def _ctx(self, tenant: str) -> RequestContext:
        return RequestContext(
            actor=Actor(type=ActorType.USER, id=SEED_ACTOR_ID),
            correlation_id=f"seed-{uuid.uuid4().hex}",
            tenant_scope=tenant,
        )

    def _run(self, tenant: str, capability: str, version: str, payload: object) -> object:
        """Dispatch one command through the authoritative bus and record its audit evidence."""
        result = self._bus.dispatch(
            Command(capability=capability, version=version, payload=payload), self._ctx(tenant)
        )
        self._audit.append(result.audit)
        return result.value

    def _seed_sections(self, tenant: str) -> tuple[SeededSection, ...]:
        seeded: list[SeededSection] = []
        for section in self._profile.tutorial.sections:
            created = self._run(
                tenant,
                knowledge.CAP_CREATE_DOCUMENT,
                knowledge.CAP_VERSION,
                knowledge.CreateDocumentCommand(
                    document_type=self._profile.tutorial.document_type,
                    locale=self._profile.tutorial.locale,
                    title=section.title,
                    blocks=(_block(section.block_id, section.body),),
                ),
            )
            object_id = created.object_id  # type: ignore[attr-defined]
            self._run(
                tenant,
                knowledge.CAP_SUBMIT_FOR_REVIEW,
                knowledge.CAP_VERSION,
                knowledge.SubmitForReviewCommand(object_id=object_id),
            )
            published = self._run(
                tenant,
                knowledge.CAP_PUBLISH_DOCUMENT,
                knowledge.CAP_VERSION,
                knowledge.PublishDocumentCommand(object_id=object_id, title=section.title),
            )
            seeded.append(
                SeededSection(
                    section_id=section.section_id,
                    title=section.title,
                    object_id=object_id,
                    revision_id=published.revision_id,  # type: ignore[attr-defined]
                    block_id=section.block_id,
                )
            )
        return tuple(seeded)

    def _compose_course(self, tenant: str, sections: tuple[SeededSection, ...]) -> None:
        tutorial = self._profile.tutorial
        specs = tuple(
            learning.SectionSpec(
                section_id=s.section_id,
                title=s.title,
                object_id=s.object_id,
                revision_id=s.revision_id,
                block_ids=(s.block_id,),
                ordinal=i,
            )
            for i, s in enumerate(sections)
        )
        self._run(
            tenant,
            learning.CAP_COURSE_COMPOSE,
            learning.CAP_VERSION,
            learning.ComposeCourseCommand(
                course_id=tutorial.course_id,
                domain_id=tutorial.domain_id,
                title=tutorial.course_title,
                sections=specs,
                path_id=tutorial.path_id,
                domain_title=tutorial.domain_title,
                completion_rule=learning.CompletionRuleSpec(
                    rule_id=tutorial.completion_rule_id,
                    required_section_ids=tuple(s.section_id for s in sections),
                    required_item_ids=(tutorial.assessment.item_id,),
                ),
            ),
        )
        self._run(
            tenant,
            learning.CAP_COURSE_PUBLISH,
            learning.CAP_VERSION,
            learning.PublishCourseCommand(course_id=tutorial.course_id),
        )
        item = tutorial.assessment
        self._run(
            tenant,
            learning.CAP_ITEM_PUBLISH,
            learning.CAP_VERSION,
            learning.PublishItemCommand(
                item_id=item.item_id,
                version=item.version,
                kind=item.kind,
                prompt=item.prompt,
                answer_key=item.answer_key,
                choices=item.choices,
            ),
        )

    def _seed_offer(self, tenant: str) -> None:
        offer = self._profile.offer
        self._run(
            tenant,
            commerce.CAP_OFFER_PUBLISH,
            commerce.CAP_VERSION,
            commerce.PublishOfferCommand(
                offer=dict(offer.document),
                product_name=offer.product_name,
                product_kind=offer.product_kind,
            ),
        )

    def _seed_campaign(self, tenant: str) -> None:
        campaign = self._profile.campaign
        self._run(
            tenant,
            messaging.CAP_TEMPLATE_PUBLISH,
            messaging.CAP_VERSION,
            messaging.PublishTemplateVersionCommand(
                template_id=campaign.template_id,
                version=campaign.template_version,
                subject=campaign.subject,
                html_body=campaign.html_body,
                text_body=campaign.text_body,
                required_variables=campaign.required_variables,
            ),
        )
        created = self._run(
            tenant,
            messaging.CAP_CAMPAIGN_CREATE,
            messaging.CAP_VERSION,
            messaging.CreateCampaignCommand(
                name=campaign.campaign_name,
                message_class=campaign.message_class,
                template_id=campaign.template_id,
                template_version=campaign.template_version,
                channel=campaign.channel,
                purpose=campaign.purpose,
                segment_specs=campaign.segment_specs,
            ),
        )
        campaign_id = created.campaign_id  # type: ignore[attr-defined]
        self._run(
            tenant,
            messaging.CAP_CAMPAIGN_SCHEDULE,
            messaging.CAP_VERSION,
            messaging.ScheduleCampaignCommand(
                campaign_id=campaign_id, schedule=dict(campaign.schedule)
            ),
        )
        self._campaign_id = campaign_id

    def _seed_analytics(self, tenant: str) -> None:
        self._run(
            tenant,
            analytics.CAP_CATALOG_REGISTER,
            analytics.CAP_VERSION,
            analytics.RegisterEventDefinitionCommand(
                definition=dict(self._profile.analytics_event.definition)
            ),
        )

    def seed(self, tenant: str) -> SeedReceipt:
        """Seed the whole reference product for ``tenant`` and return the evidence receipt."""
        self._audit = []
        self._campaign_id = ""
        sections = self._seed_sections(tenant)
        self._compose_course(tenant, sections)
        self._seed_offer(tenant)
        self._seed_campaign(tenant)
        self._seed_analytics(tenant)

        tutorial = self._profile.tutorial
        return SeedReceipt(
            tenant=tenant,
            course_id=tutorial.course_id,
            domain_id=tutorial.domain_id,
            path_id=tutorial.path_id,
            sections=sections,
            completion_rule_id=tutorial.completion_rule_id,
            assessment_item_id=tutorial.assessment.item_id,
            assessment_item_version=tutorial.assessment.version,
            offer_id=self._profile.offer.offer_id,
            offer_version=self._profile.offer.version,
            template_id=self._profile.campaign.template_id,
            campaign_id=self._campaign_id,
            analytics_event_name=str(self._profile.analytics_event.definition["event_name"]),
            audit_records=tuple(self._audit),
        )
