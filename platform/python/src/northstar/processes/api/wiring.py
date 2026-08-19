"""Composition root for the API process (wires real adapters into the HTTP app).

This is the only place that knows concrete infrastructure: it resolves ``DATABASE_URL``, builds
the SQLAlchemy engine/session factory, constructs the capability registry, deny-by-default policy
evaluator, audit recorder and the command/query buses, and exposes health/version probes. The
result is a fully-wired :class:`~northstar.adapters.http_fastapi.AppDependencies` handed to
:func:`~northstar.adapters.http_fastapi.create_app`. No process runs migrations on startup
(docs/18 §5); readiness only checks that the database answers.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import Engine, MetaData, text
from sqlalchemy.orm import Session, sessionmaker

from northstar.adapters.ai_provider import DeterministicMockProvider
from northstar.adapters.crypto_aesgcm import AesGcmEncryptor, load_master_key
from northstar.adapters.egress import AllowlistEgressGuard
from northstar.adapters.http_fastapi import (
    AppDependencies,
    InMemoryRateLimiter,
    RateLimitGuard,
    create_app,
)
from northstar.adapters.object_storage import InMemoryObjectStorage
from northstar.adapters.persistence_sqlalchemy import (
    create_engine_from_url,
    create_session_factory,
    resolve_database_url,
)
from northstar.adapters.telemetry_otel import (
    build_tracer,
    build_tracer_provider,
    instrument_fastapi_app,
)
from northstar.adapters.persistence_sqlalchemy.audit_recorder import SqlAlchemyAuditRecorder
from northstar.processes.api.admin import (
    AdminApiDependencies,
    bind_admin_dependencies,
    create_admin_router,
)
from northstar.processes.api.seo import (
    SeoDependencies,
    bind_seo_dependencies,
    create_seo_router,
)
from northstar.kernel.audit import InMemoryAuditRecorder
from northstar.kernel.capabilities import (
    CapabilityDispatcher,
    CapabilityRegistry,
)
from northstar.kernel.context import Actor, ActorType, RequestContext, ResourceRef
from northstar.kernel.health.ports import HealthReport, HealthState, VersionInfo
from northstar.kernel.messaging import CommandBus, QueryBus
from northstar.kernel.observability.ports import TracerPort
from northstar.kernel.observability.reference import NoOpTracer
from northstar.kernel.policy import LayeredPolicyEvaluator, PolicyGrant
from northstar.kernel.security.egress import EgressPolicy
from northstar.modules.ai.adapters.http_fetch import GuardedFetchTool
from northstar.modules.ai.adapters.privacy_handlers import AiMemorySubjectStore
from northstar.modules.ai.adapters.reference import (
    REFERENCE_PRIMARY_PROFILE_ID,
    reference_actor_profiles,
    reference_grants,
    reference_model_catalog,
    reference_tools,
)
from northstar.modules.ai.adapters.repositories import (
    SqlAlchemyBudgetLedger,
    SqlAlchemyMemoryRepository,
    SqlAlchemyPromptRegistry,
    SqlAlchemyTraceRepository,
)
from northstar.modules.ai.adapters.retrieval_gateway import BusRetrievalGateway
from northstar.modules.ai.adapters.tables import build_ai_tables
from northstar.modules.ai.adapters.tool_executor import CallableToolExecutor
from northstar.modules.ai.api import (
    AiApiDependencies,
    bind_ai_dependencies,
    create_ai_router,
)
from northstar.modules.ai.application import capabilities as ai
from northstar.modules.ai.application.budget_guard import BudgetGuard
from northstar.modules.ai.application.tool_broker import ToolBroker
from northstar.modules.analytics.adapters.ga4 import InMemoryGa4Adapter
from northstar.modules.analytics.adapters.repositories import SqlAlchemyAnalyticsRepository
from northstar.modules.analytics.adapters.tables import build_analytics_tables
from northstar.modules.analytics.api import (
    AnalyticsApiDependencies,
    bind_analytics_dependencies,
    create_analytics_router,
)
from northstar.modules.analytics.application import capabilities as analytics
from northstar.modules.annotation.adapters.repositories import SqlAlchemyAnnotationRepository
from northstar.modules.annotation.adapters.snapshots import KnowledgeRevisionSnapshotProvider
from northstar.modules.annotation.adapters.tables import build_annotation_tables
from northstar.modules.annotation.api import (
    AnnotationApiDependencies,
    bind_annotation_dependencies,
    create_annotation_router,
)
from northstar.modules.annotation.application import capabilities as annotation
from northstar.modules.annotation.domain.remap import Remapper
from northstar.modules.commerce.adapters.entitlement_gateway import (
    EntitlementEngineGateway,
    SqlAlchemyCommerceEntitlementRepository,
)
from northstar.modules.commerce.adapters.repositories import SqlAlchemyCommerceRepository
from northstar.modules.commerce.adapters.tables import build_commerce_tables
from northstar.modules.commerce.adapters.webhook_verifier import HmacWebhookVerifier
from northstar.modules.commerce.api import (
    CommerceApiDependencies,
    bind_commerce_dependencies,
    create_commerce_router,
)
from northstar.modules.commerce.application import capabilities as commerce
from northstar.modules.enterprise.adapters.gateways import (
    IdentitySubjectGateway,
    InMemorySessionInvalidator,
)
from northstar.modules.enterprise.adapters.lrs import InMemoryExportConsent, InMemoryLrs
from northstar.modules.enterprise.adapters.repositories import SqlAlchemyEnterpriseRepository
from northstar.modules.enterprise.adapters.tables import build_enterprise_tables
from northstar.modules.enterprise.adapters.verifiers import (
    HmacFederationVerifier,
    HmacLtiVerifier,
)
from northstar.modules.enterprise.api import (
    EnterpriseApiDependencies,
    bind_enterprise_dependencies,
    create_enterprise_router,
)
from northstar.modules.enterprise.application import capabilities as enterprise
from northstar.modules.entitlement.adapters.repositories import SqlAlchemyEntitlementRepository
from northstar.modules.entitlement.adapters.tables import build_entitlement_tables
from northstar.modules.entitlement.application import capabilities as ent
from northstar.modules.extension.adapters.manifest_validation import (
    JsonSchemaManifestValidator,
    load_extension_schemas,
)
from northstar.modules.extension.adapters.repositories import SqlAlchemyExtensionRegistry
from northstar.modules.extension.adapters.signature_verifier import (
    HmacSignatureVerifier,
    PublisherKey,
)
from northstar.modules.extension.adapters.tables import build_extension_tables
from northstar.modules.extension.api import (
    ExtensionApiDependencies,
    bind_extension_dependencies,
    create_extension_router,
)
from northstar.modules.extension.application import capabilities as extension
from northstar.modules.extension.domain.model import TrustTier
from northstar.modules.governance.adapters.directories import InMemoryApproverDirectory
from northstar.modules.governance.adapters.repositories import SqlAlchemyGovernanceRepository
from northstar.modules.governance.adapters.tables import build_governance_tables
from northstar.modules.governance.api import (
    GovernanceApiDependencies,
    bind_governance_dependencies,
    create_governance_router,
)
from northstar.modules.governance.application import capabilities as governance
from northstar.modules.governance_studio import application as studio
from northstar.modules.governance_studio.adapters import RecorderAuditReader
from northstar.modules.governance_studio.api import (
    GovernanceStudioApiDependencies,
    bind_governance_studio_dependencies,
    create_governance_studio_router,
)
from northstar.modules.governance_studio.application.contributions import sample_contributions
from northstar.modules.identity.adapters.impersonation_repositories import (
    SqlAlchemyImpersonationRepository,
)
from northstar.modules.identity.adapters.in_memory import (
    InMemoryAuthTransactionStore,
    InMemoryMfaService,
    InMemoryWebAuthnChallengeStore,
    new_session_token,
)
from northstar.modules.identity.adapters.mfa_repositories import (
    SqlAlchemyTotpCredentialStore,
    SqlAlchemyWebAuthnCredentialStore,
)
from northstar.modules.identity.adapters.mock_oidc_provider import MockOidcProvider
from northstar.modules.identity.adapters.local_auth import (
    ScryptPasswordHasher,
    SqlAlchemyAccountEventStore,
    SqlAlchemyLocalAccountStore,
    SqlAlchemyVerificationTokenStore,
)
from northstar.modules.identity.adapters.sqlalchemy_repositories import (
    SqlAlchemyIdentityDirectory,
    SqlAlchemySessionStore,
)
from northstar.modules.identity.adapters.tables import build_identity_tables
from northstar.modules.identity.adapters.webauthn import PyWebAuthnVerifier
from northstar.modules.identity.api import (
    IdentityApiDependencies,
    IdentityCookieConfig,
    bind_identity_dependencies,
    create_identity_router,
)
from northstar.modules.identity.api.mock_idp import (
    MockIdpDependencies,
    bind_mock_idp_dependencies,
    create_mock_idp_router,
)
from northstar.modules.identity.application import capabilities as ident
from northstar.modules.identity.application import local_auth
from northstar.modules.identity.application import impersonation, mfa
from northstar.modules.knowledge.adapters.repositories import SqlAlchemyKnowledgeRepository
from northstar.modules.assistant.adapters.openai_compatible import OpenAICompatibleChatModel
from northstar.modules.assistant.adapters.retrieval_gateway import (
    BusRetrievalGateway as AssistantRetrievalGateway,
)
from northstar.modules.assistant.api.router import (
    AssistantApiDependencies,
    bind_assistant_dependencies,
    create_assistant_router,
)
from northstar.modules.assistant.adapters.settings_store import SqlAlchemyAssistantSettings
from northstar.modules.assistant.adapters.tables import build_assistant_tables
from northstar.modules.assistant.application import capabilities as assistant
from northstar.modules.assistant.application.config import default_store as assistant_default_store
from northstar.modules.codelab.adapters.repositories import SqlAlchemyCodeRunStore
from northstar.modules.codelab.adapters.sandbox import SubprocessSandbox
from northstar.modules.codelab.adapters.tables import build_codelab_tables
from northstar.modules.codelab.api.router import (
    CodelabApiDependencies,
    bind_codelab_dependencies,
    create_codelab_router,
)
from northstar.modules.codelab.application import capabilities as codelab
from northstar.modules.knowledge.adapters.tables import build_knowledge_tables
from northstar.modules.knowledge.api import (
    KnowledgeApiDependencies,
    bind_knowledge_dependencies,
    create_knowledge_router,
)
from northstar.modules.knowledge.application import capabilities as knowledge
from northstar.modules.learning.adapters.access_gateways import (
    InMemoryConsentStore,
    InMemoryEntitlementDirectory,
)
from northstar.modules.learning.adapters.ai_tutor_gateway import BusAiTutorGateway
from northstar.modules.learning.adapters.content_gateway import KnowledgePublishedContent
from northstar.modules.learning.adapters.repositories import SqlAlchemyLearningRepository
from northstar.modules.learning.adapters.tables import build_learning_tables
from northstar.modules.learning.api import (
    LearningApiDependencies,
    bind_learning_dependencies,
    create_learning_router,
)
from northstar.modules.learning.application import capabilities as learning
from northstar.modules.media.adapters.repositories import SqlAlchemyMediaRepository
from northstar.modules.media.adapters.storage import build_media_storage
from northstar.modules.media.adapters.tables import build_media_tables
from northstar.modules.media.api import (
    MediaApiDependencies,
    bind_media_dependencies,
    create_media_router,
)
from northstar.modules.media.application import capabilities as media
from northstar.modules.messaging.adapters.email_bridge import MessagingEmailSender
from northstar.modules.messaging.adapters.email_delivery import email_delivery_from_env
from northstar.modules.messaging.adapters.email_outbox import SqlAlchemyEmailOutbox
from northstar.modules.messaging.adapters.provider import InMemoryMessageProvider
from northstar.modules.messaging.adapters.repositories import SqlAlchemyMessagingRepository
from northstar.modules.messaging.adapters.tables import build_messaging_tables
from northstar.modules.messaging.adapters.webhook import GuardedWebhookDelivery
from northstar.modules.messaging.api import (
    MessagingApiDependencies,
    bind_messaging_dependencies,
    create_messaging_router,
)
from northstar.modules.messaging.application import capabilities as messaging
from northstar.modules.messaging.application import transactional as messaging_tx
from northstar.modules.messaging.application.transactional import TransactionalEmailService
from northstar.modules.moderation.adapters.enforcement import (
    AnnotationEnforcementGateway,
    AnnotationModerationGateway,
    InMemoryModeratorDirectory,
)
from northstar.modules.moderation.adapters.reportable_content import (
    AnnotationReportableContentProvider,
)
from northstar.modules.moderation.adapters.repositories import SqlAlchemyModerationRepository
from northstar.modules.moderation.adapters.tables import build_moderation_tables
from northstar.modules.moderation.api import (
    ModerationApiDependencies,
    bind_moderation_dependencies,
    create_moderation_router,
)
from northstar.modules.moderation.application import capabilities as moderation
from northstar.modules.organization.adapters.repositories import (
    OrgResourceAttributeResolver,
    OrgRoleDirectory,
    SqlAlchemyOrganizationRepository,
)
from northstar.modules.organization.adapters.tables import build_organization_tables
from northstar.modules.organization.api import (
    OrganizationApiDependencies,
    bind_organization_dependencies,
    create_organization_router,
)
from northstar.modules.organization.application import capabilities as org
from northstar.modules.privacy.adapters.handlers import (
    STORE_ANALYTICS_EVENTS,
    STORE_ANNOTATION,
    STORE_LEARNING_OVERLAY,
    STORE_LEARNING_PROGRESS,
    STORE_OBJECTSTORE_BLOBS,
    STORE_PROVIDER_EXPORT,
    STORE_SEARCH_PROJECTION,
    InMemorySubjectStore,
)
from northstar.modules.privacy.adapters.repositories import SqlAlchemyPrivacyRepository
from northstar.modules.privacy.adapters.tables import build_privacy_tables
from northstar.modules.privacy.api import (
    PrivacyApiDependencies,
    bind_privacy_dependencies,
    create_privacy_router,
)
from northstar.modules.privacy.application import capabilities as privacy
from northstar.modules.privacy.application.registry import DataSubjectRightsRegistry
from northstar.modules.research.adapters.ai_gateway import BusAiDraftGateway
from northstar.modules.research.adapters.repositories import SqlAlchemyResearchRepository
from northstar.modules.research.adapters.tables import build_research_tables
from northstar.modules.research.api import (
    ResearchApiDependencies,
    bind_research_dependencies,
    create_research_router,
)
from northstar.modules.research.application import capabilities as research
from northstar.modules.research.application.ports import SimulationRef
from northstar.modules.retrieval.adapters.embedding import LocalHashEmbedding
from northstar.modules.retrieval.adapters.repositories import SqlAlchemyRetrievalRepository
from northstar.modules.retrieval.adapters.tables import build_retrieval_tables
from northstar.modules.retrieval.api import (
    RetrievalApiDependencies,
    bind_retrieval_dependencies,
    create_retrieval_router,
)
from northstar.modules.retrieval.application import capabilities as retrieval
from northstar.modules.simulation.adapters.ai_coach import BusAiCoachGateway
from northstar.modules.simulation.adapters.lease_signer import HmacLeaseSigner
from northstar.modules.simulation.adapters.repositories import (
    SqlAlchemyEvidenceStore,
    SqlAlchemySimulationRepository,
)
from northstar.modules.simulation.adapters.sandbox import SandboxExecutor
from northstar.modules.simulation.adapters.scoring import DeterministicScoring
from northstar.modules.simulation.adapters.tables import build_simulation_tables
from northstar.modules.simulation.api import (
    SimulationApiDependencies,
    bind_simulation_dependencies,
    create_simulation_router,
)
from northstar.modules.simulation.application import capabilities as simulation
from northstar.modules.support.adapters.repositories import SqlAlchemySupportRepository
from northstar.modules.support.adapters.tables import build_support_tables
from northstar.modules.support.api import (
    SupportApiDependencies,
    bind_support_dependencies,
    create_support_router,
)
from northstar.modules.support.application import capabilities as support

from .tracing import TracingCommandBus

CONTRACT_API = "v1"


def _framework_version() -> str:
    try:
        return metadata.version("northstar")
    except metadata.PackageNotFoundError:
        return "0.0.0+local"


@dataclass(frozen=True, slots=True)
class StaticVersionProbe:
    """Reports the framework version and static schema-compatibility (FR-KRN-006)."""

    framework_version: str
    contract_api: str = CONTRACT_API
    schema_compatible: bool = True

    def version(self) -> VersionInfo:
        return VersionInfo(
            framework_version=self.framework_version,
            contract_api=self.contract_api,
            schema_compatible=self.schema_compatible,
        )


class DatabaseHealthProbe:
    """Liveness/readiness/startup probes backed by database connectivity (docs/18 §12).

    Liveness/startup are cheap process checks; readiness issues a lightweight ``SELECT 1`` so the
    process only advertises readiness when its required database dependency answers.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def liveness(self) -> HealthReport:
        return HealthReport(state=HealthState.HEALTHY, detail="process is running")

    def readiness(self) -> HealthReport:
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:  # readiness reports failure, it never raises
            return HealthReport(
                state=HealthState.UNHEALTHY, detail="database dependency is unavailable"
            )
        return HealthReport(state=HealthState.HEALTHY, detail="database dependency is ready")

    def startup(self) -> HealthReport:
        return HealthReport(state=HealthState.HEALTHY, detail="startup complete")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _egress_allowlist() -> frozenset[str]:
    """Resolve the outbound-HTTP host allowlist from config (empty => deny all, the safe default).

    ``NORTHSTAR_EGRESS_ALLOWLIST`` is a comma-separated host list; a deployment adds the exact
    provider/webhook hosts it trusts. Deny-by-default means an unset value blocks every outbound
    call rather than opening one (EVAL-SEC-005).
    """
    raw = os.environ.get("NORTHSTAR_EGRESS_ALLOWLIST", "")
    return frozenset(host.strip() for host in raw.split(",") if host.strip())


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class _Composition:
    """Everything the API process needs: kernel deps + module router deps."""

    dependencies: AppDependencies
    identity: IdentityApiDependencies
    mock_idp: MockIdpDependencies
    organization: OrganizationApiDependencies
    governance_studio: GovernanceStudioApiDependencies
    knowledge: KnowledgeApiDependencies
    codelab: CodelabApiDependencies
    annotation: AnnotationApiDependencies
    retrieval: RetrievalApiDependencies
    ai: AiApiDependencies
    assistant: AssistantApiDependencies
    admin: AdminApiDependencies
    research: ResearchApiDependencies
    simulation: SimulationApiDependencies
    extension: ExtensionApiDependencies
    messaging: MessagingApiDependencies
    analytics: AnalyticsApiDependencies
    commerce: CommerceApiDependencies
    support: SupportApiDependencies
    learning: LearningApiDependencies
    privacy: PrivacyApiDependencies
    media: MediaApiDependencies
    moderation: ModerationApiDependencies
    governance: GovernanceApiDependencies
    enterprise: EnterpriseApiDependencies
    messaging_webhook: GuardedWebhookDelivery


def _dev_idp_enabled() -> bool:
    """Dev IdP is on by default so login is runnable locally; disable with NORTHSTAR_DEV_IDP=0."""
    return os.environ.get("NORTHSTAR_DEV_IDP", "1") != "0"


def _default_tenant() -> str:
    return os.environ.get("NORTHSTAR_DEFAULT_TENANT", "org-bestinfopages")


def _app_base_url() -> str:
    """Public base URL of the web app, used to build email confirm/reset links."""
    return os.environ.get("NORTHSTAR_APP_BASE_URL", "http://localhost:5173").rstrip("/")


def _register_identity(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
    email_sender: object,
    app_base_url: str,
    tenant: str,
) -> tuple[SqlAlchemySessionStore, MockOidcProvider]:
    """Register the identity capabilities and grants; return the session store + OIDC provider.

    Provider note: the reference wiring uses the deterministic :class:`MockOidcProvider`. A real
    deployment injects a configured OIDC IdP adapter (issuer/audience/JWKS from the secret manager)
    behind the same ``OidcProviderPort`` — no identity-core change is required (FR-IDN-006). When the
    dev IdP is enabled, the provider's authorization endpoint points at the mounted mock login page
    so the full Authorization-Code + PKCE flow runs locally; new subjects are provisioned into the
    default tenant so they can immediately read the seeded curriculum.
    """
    tables = build_identity_tables(MetaData())
    if _dev_idp_enabled():
        authorize_url = os.environ.get(
            "NORTHSTAR_MOCK_IDP_AUTHORIZE_URL", "/api/auth/mock-idp/authorize"
        )
        provider = MockOidcProvider(authorization_endpoint=authorize_url)
    else:
        provider = MockOidcProvider()
    transactions = InMemoryAuthTransactionStore()
    directory = SqlAlchemyIdentityDirectory(
        session_factory=session_factory, tables=tables, id_factory=_uuid, clock=_utc_now
    )
    session_store = SqlAlchemySessionStore(session_factory=session_factory, tables=tables)
    mfa_service = InMemoryMfaService()

    registry.register(
        ident.CAP_REGISTER_SUBJECT,
        ident.CAP_VERSION,
        ident.RegisterSubject(directory=directory, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        ident.CAP_BEGIN_AUTHENTICATION,
        ident.CAP_VERSION,
        ident.BeginAuthentication(provider=provider, transactions=transactions, clock=_utc_now),
    )
    registry.register(
        ident.CAP_COMPLETE_AUTHENTICATION,
        ident.CAP_VERSION,
        ident.CompleteAuthentication(
            provider=provider,
            transactions=transactions,
            directory=directory,
            sessions=session_store,
            clock=_utc_now,
            id_factory=_uuid,
            token_factory=new_session_token,
            tenant_scope=_default_tenant(),
        ),
    )
    registry.register(
        ident.CAP_ROTATE_SESSION,
        ident.CAP_VERSION,
        ident.RotateSession(
            sessions=session_store,
            clock=_utc_now,
            id_factory=_uuid,
            token_factory=new_session_token,
        ),
    )
    registry.register(
        ident.CAP_REVOKE_SESSION,
        ident.CAP_VERSION,
        ident.RevokeSession(sessions=session_store, clock=_utc_now),
    )
    registry.register(
        ident.CAP_DESCRIBE_SESSION,
        ident.CAP_VERSION,
        ident.DescribeSession(sessions=session_store, clock=_utc_now),
    )
    registry.register(
        ident.CAP_ENROLL_MFA,
        ident.CAP_VERSION,
        ident.EnrollMfa(enrollment=mfa_service),
    )
    registry.register(
        ident.CAP_VERIFY_MFA,
        ident.CAP_VERSION,
        ident.VerifyMfa(verification=mfa_service),
    )

    # Real MFA (FR-IDN-003): RFC 6238 TOTP + WebAuthn/passkeys, enforced as step-up. The WebAuthn
    # relying-party id/origin come from configuration; challenges are single-use and held
    # server-side. The one authoritative step-up guard is identity.mfa.step-up.enforce.
    # AES-256-GCM encryption at rest for the TOTP shared secret (NFR-SEC-001, rule 50). The KEK is
    # resolved from the secret manager/env via load_master_key (production MUST set
    # NORTHSTAR_MASTER_KEY; dev/test derive a deterministic insecure key). A KMS/HSM deployment
    # swaps only the encryptor behind the same kernel EncryptionPort — no capability change.
    encryptor = AesGcmEncryptor(load_master_key())
    totp_store = SqlAlchemyTotpCredentialStore(
        session_factory=session_factory, tables=tables, encryptor=encryptor
    )
    webauthn_store = SqlAlchemyWebAuthnCredentialStore(
        session_factory=session_factory, tables=tables
    )
    challenges = InMemoryWebAuthnChallengeStore()
    rp_id = os.environ.get("IDENTITY_WEBAUTHN_RP_ID", "localhost")
    origin = os.environ.get("IDENTITY_WEBAUTHN_ORIGIN", "http://localhost:8000")
    verifier = PyWebAuthnVerifier(rp_id=rp_id, rp_name="Northstar", origin=origin)

    registry.register(
        mfa.CAP_ENROLL_TOTP,
        ident.CAP_VERSION,
        mfa.EnrollTotp(totp_store=totp_store, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        mfa.CAP_VERIFY_TOTP,
        ident.CAP_VERSION,
        mfa.VerifyTotp(totp_store=totp_store, sessions=session_store, clock=_utc_now),
    )
    registry.register(
        mfa.CAP_BEGIN_WEBAUTHN_REGISTRATION,
        ident.CAP_VERSION,
        mfa.BeginWebAuthnRegistration(
            verifier=verifier, challenges=challenges, webauthn_store=webauthn_store
        ),
    )
    registry.register(
        mfa.CAP_COMPLETE_WEBAUTHN_REGISTRATION,
        ident.CAP_VERSION,
        mfa.CompleteWebAuthnRegistration(
            verifier=verifier,
            challenges=challenges,
            webauthn_store=webauthn_store,
            clock=_utc_now,
        ),
    )
    registry.register(
        mfa.CAP_BEGIN_WEBAUTHN_AUTHENTICATION,
        ident.CAP_VERSION,
        mfa.BeginWebAuthnAuthentication(
            verifier=verifier, challenges=challenges, webauthn_store=webauthn_store
        ),
    )
    registry.register(
        mfa.CAP_COMPLETE_WEBAUTHN_AUTHENTICATION,
        ident.CAP_VERSION,
        mfa.CompleteWebAuthnAuthentication(
            verifier=verifier,
            challenges=challenges,
            webauthn_store=webauthn_store,
            sessions=session_store,
            clock=_utc_now,
        ),
    )
    registry.register(
        mfa.CAP_RESET_MFA,
        ident.CAP_VERSION,
        mfa.ResetMfa(totp_store=totp_store, webauthn_store=webauthn_store),
    )
    registry.register(
        mfa.CAP_ENFORCE_STEP_UP,
        ident.CAP_VERSION,
        mfa.EnforceStepUp(sessions=session_store, clock=_utc_now),
    )

    # Impersonation + break-glass (FR-IDN-007/008): the auditable, time-bounded, visibly-indicated
    # support-impersonation session mode and the exceptional, justified, post-use-reviewed
    # break-glass access. Both own their records in the RLS-forced northstar_identity schema.
    impersonation_repo = SqlAlchemyImpersonationRepository(
        session_factory=session_factory, tables=tables
    )
    registry.register(
        impersonation.CAP_IMPERSONATION_START,
        ident.CAP_VERSION,
        impersonation.StartImpersonation(
            repository=impersonation_repo, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        impersonation.CAP_IMPERSONATION_END,
        ident.CAP_VERSION,
        impersonation.EndImpersonation(repository=impersonation_repo, clock=_utc_now),
    )
    registry.register(
        impersonation.CAP_BREAKGLASS_INVOKE,
        ident.CAP_VERSION,
        impersonation.InvokeBreakGlass(
            repository=impersonation_repo, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        impersonation.CAP_BREAKGLASS_REVIEW_RESOLVE,
        ident.CAP_VERSION,
        impersonation.ResolveBreakGlassReview(repository=impersonation_repo, clock=_utc_now),
    )

    # Local (email + password) auth: a parallel first-factor path that mints the SAME session as
    # OIDC (docs/07 §4). Confirmation + reset use single-use expiring tokens; every step writes a
    # durable account_event for the Activity feed. Email goes through the injected transactional
    # sender (identity depends only on a port; LAW-13).
    local_accounts = SqlAlchemyLocalAccountStore(
        session_factory=session_factory, tables=tables, id_factory=_uuid, clock=_utc_now
    )
    local_tokens = SqlAlchemyVerificationTokenStore(session_factory=session_factory, tables=tables)
    account_events = SqlAlchemyAccountEventStore(session_factory=session_factory, tables=tables)
    local_common = {
        "accounts": local_accounts,
        "hasher": ScryptPasswordHasher(),
        "tokens": local_tokens,
        "events": account_events,
        "email": email_sender,
        "clock": _utc_now,
        "id_factory": _uuid,
        "token_factory": new_session_token,
        "tenant_scope": tenant,
        "app_base_url": app_base_url,
    }
    registry.register(
        local_auth.CAP_LOCAL_REGISTER,
        ident.CAP_VERSION,
        local_auth.RegisterLocalUser(**local_common),
    )
    registry.register(
        local_auth.CAP_LOCAL_CONFIRM_EMAIL,
        ident.CAP_VERSION,
        local_auth.ConfirmEmail(**local_common),
    )
    registry.register(
        local_auth.CAP_LOCAL_LOGIN,
        ident.CAP_VERSION,
        local_auth.LoginLocalUser(sessions=session_store, **local_common),
    )
    registry.register(
        local_auth.CAP_LOCAL_REQUEST_RESET,
        ident.CAP_VERSION,
        local_auth.RequestPasswordReset(**local_common),
    )
    registry.register(
        local_auth.CAP_LOCAL_RESET_PASSWORD,
        ident.CAP_VERSION,
        local_auth.ResetPassword(**local_common),
    )
    registry.register(
        local_auth.CAP_LOCAL_RESEND_CONFIRMATION,
        ident.CAP_VERSION,
        local_auth.ResendConfirmation(**local_common),
    )
    registry.register(
        local_auth.CAP_ACTIVITY_LIST,
        ident.CAP_VERSION,
        local_auth.ListActivity(events=account_events),
    )
    registry.register(
        local_auth.CAP_ACTIVITY_ADMIN_LIST,
        ident.CAP_VERSION,
        local_auth.ListAdminActivity(events=account_events),
    )

    # Deny-by-default grants: the anonymous edge may only begin/complete authentication; the
    # session actions are authorized for any authenticated actor on the identity.session resource
    # (the router has already validated the session cookie before reaching the bus).
    anonymous = frozenset({"anonymous"})
    policy.grant(PolicyGrant(action=ident.CAP_BEGIN_AUTHENTICATION, actor_ids=anonymous))
    policy.grant(PolicyGrant(action=ident.CAP_COMPLETE_AUTHENTICATION, actor_ids=anonymous))
    # Local-auth entry points are anonymous; the activity queries are for any authenticated actor
    # (the router validates the session cookie first).
    for action in local_auth.LOCAL_AUTH_ANONYMOUS_CAPABILITIES:
        policy.grant(PolicyGrant(action=action, actor_ids=anonymous))
    policy.grant(PolicyGrant(action=local_auth.CAP_ACTIVITY_LIST))
    policy.grant(PolicyGrant(action=local_auth.CAP_ACTIVITY_ADMIN_LIST))
    for action in (
        ident.CAP_DESCRIBE_SESSION,
        ident.CAP_ROTATE_SESSION,
        ident.CAP_REVOKE_SESSION,
    ):
        policy.grant(PolicyGrant(action=action, resource_type=ident.SESSION_RESOURCE_TYPE))
    # MFA capabilities are authorized for any authenticated actor (the edge validates the session);
    # the second factor itself is what raises assurance, so no session resource type is required.
    for action in (
        ident.CAP_ENROLL_MFA,
        ident.CAP_VERIFY_MFA,
        mfa.CAP_ENROLL_TOTP,
        mfa.CAP_VERIFY_TOTP,
        mfa.CAP_BEGIN_WEBAUTHN_REGISTRATION,
        mfa.CAP_COMPLETE_WEBAUTHN_REGISTRATION,
        mfa.CAP_BEGIN_WEBAUTHN_AUTHENTICATION,
        mfa.CAP_COMPLETE_WEBAUTHN_AUTHENTICATION,
        mfa.CAP_RESET_MFA,
        mfa.CAP_ENFORCE_STEP_UP,
    ):
        policy.grant(PolicyGrant(action=action))
    # Impersonation/break-glass are operator/support actions authorized at the capability layer; the
    # finer rule (time-bound, approval-where-required, mandatory post-use review) is enforced inside
    # the capabilities + the kernel layered policy engine and dual-actor audited.
    for action in impersonation.IMPERSONATION_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))

    return session_store, provider


def _register_entitlement(
    *, registry: CapabilityRegistry, session_factory: sessionmaker[Session]
) -> ent.EntitlementService:
    """Register entitlement capabilities; return the authoritative entitlement service."""
    tables = build_entitlement_tables(MetaData())
    repository = SqlAlchemyEntitlementRepository(session_factory=session_factory, tables=tables)
    service = ent.EntitlementService(repository=repository, id_factory=_uuid)
    registry.register(
        ent.CAP_CREATE_GRANT,
        ent.CAP_VERSION,
        ent.CreateGrant(repository=repository, id_factory=_uuid),
    )
    registry.register(
        ent.CAP_EVALUATE_DECISION,
        ent.CAP_VERSION,
        ent.EvaluateEntitlement(service=service),
    )
    return service


def _register_organization(
    *, registry: CapabilityRegistry, session_factory: sessionmaker[Session]
) -> tuple[OrgRoleDirectory, OrgResourceAttributeResolver]:
    """Register organization capabilities; return the policy role/resource providers."""
    tables = build_organization_tables(MetaData())
    repository = SqlAlchemyOrganizationRepository(session_factory=session_factory, tables=tables)
    registry.register(
        org.CAP_CREATE_ORGANIZATION,
        org.CAP_VERSION,
        org.CreateOrganization(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        org.CAP_CREATE_WORKSPACE,
        org.CAP_VERSION,
        org.CreateWorkspace(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        org.CAP_CREATE_TEAM,
        org.CAP_VERSION,
        org.CreateTeam(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        org.CAP_ADD_MEMBERSHIP,
        org.CAP_VERSION,
        org.AddMembership(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        org.CAP_ASSIGN_ROLE,
        org.CAP_VERSION,
        org.AssignRole(repository=repository, clock=_utc_now),
    )
    registry.register(
        org.CAP_LIST_MEMBERSHIPS,
        org.CAP_VERSION,
        org.ListMemberships(repository=repository),
    )
    role_directory = OrgRoleDirectory(repository)
    resource_resolver = OrgResourceAttributeResolver(session_factory=session_factory, tables=tables)
    return role_directory, resource_resolver


def _register_knowledge(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
) -> None:
    """Register the knowledge capabilities + deny-by-default grants (LAW-04/LAW-07).

    Publishing writes an immutable revision and appends the ``document-published`` event to the
    transactional outbox in one unit of work (LAW-10). Tenant isolation is enforced inside every
    capability (scope taken from the authenticated context, never the payload — rule 50); the
    explicit grants authorize each knowledge action for any authenticated actor at the edge.
    """
    tables = build_knowledge_tables(MetaData())
    repository = SqlAlchemyKnowledgeRepository(session_factory=session_factory, tables=tables)
    registry.register(
        knowledge.CAP_CREATE_DOCUMENT,
        knowledge.CAP_VERSION,
        knowledge.CreateDocument(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        knowledge.CAP_EDIT_DRAFT,
        knowledge.CAP_VERSION,
        knowledge.EditDraft(repository=repository, id_factory=_uuid),
    )
    registry.register(
        knowledge.CAP_SUBMIT_FOR_REVIEW,
        knowledge.CAP_VERSION,
        knowledge.SubmitForReview(repository=repository),
    )
    registry.register(
        knowledge.CAP_PUBLISH_DOCUMENT,
        knowledge.CAP_VERSION,
        knowledge.PublishDocument(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        knowledge.CAP_GET_DOCUMENT,
        knowledge.CAP_VERSION,
        knowledge.GetDocument(repository=repository),
    )
    registry.register(
        knowledge.CAP_GET_REVISION,
        knowledge.CAP_VERSION,
        knowledge.GetRevision(repository=repository),
    )
    registry.register(
        knowledge.CAP_ASSIGN_TAXONOMY,
        knowledge.CAP_VERSION,
        knowledge.AssignTaxonomy(repository=repository, id_factory=_uuid),
    )
    registry.register(
        knowledge.CAP_BROWSE_DOCUMENTS,
        knowledge.CAP_VERSION,
        knowledge.BrowseDocuments(repository=repository),
    )
    registry.register(
        knowledge.CAP_TAXONOMY_TERMS,
        knowledge.CAP_VERSION,
        knowledge.TaxonomyTerms(repository=repository),
    )
    for action in knowledge.KNOWLEDGE_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_codelab(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
) -> None:
    """Register codelab capabilities + deny-by-default grants (LAW-04).

    ``codelab.run.execute`` runs untrusted code behind the :class:`CodeSandboxPort` (a locked-down
    subprocess in the reference adapter) and records an IMMUTABLE, tenant-scoped ``code_run`` through
    the store — so every learner execution is durably tracked AND audited by the command bus. Tenant
    isolation is enforced inside the capability (scope from the authenticated context, never the
    payload — rule 50); the explicit grants authorize these actions for any authenticated actor.
    """
    tables = build_codelab_tables(MetaData())
    store = SqlAlchemyCodeRunStore(session_factory=session_factory, tables=tables)
    sandbox = SubprocessSandbox()
    registry.register(
        codelab.CAP_RUN,
        codelab.CAP_VERSION,
        codelab.RunCode(sandbox=sandbox, store=store, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        codelab.CAP_LIST_RUNS,
        codelab.CAP_VERSION,
        codelab.ListRuns(store=store),
    )
    for action in codelab.CODELAB_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_annotation(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
) -> None:
    """Register the annotation capabilities + deny-by-default grants (LAW-04, FR-ANN-001..006).

    Remapping reads knowledge revisions through a read-only snapshot provider (no cross-module
    writes, LAW-13) and routes ambiguous/orphaned targets to review (FR-ANN-004). Tenant isolation
    is enforced inside every capability (scope from the authenticated context, never the payload —
    rule 50); visibility is projected server-side by the pure visibility policy (FR-ANN-005).
    """
    tables = build_annotation_tables(MetaData())
    repository = SqlAlchemyAnnotationRepository(session_factory=session_factory, tables=tables)
    knowledge_repo = SqlAlchemyKnowledgeRepository(
        session_factory=session_factory, tables=build_knowledge_tables(MetaData())
    )
    snapshots = KnowledgeRevisionSnapshotProvider(reader=knowledge_repo)
    remapper = Remapper()
    registry.register(
        annotation.CAP_CREATE_ANNOTATION,
        annotation.CAP_VERSION,
        annotation.CreateAnnotation(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        annotation.CAP_REPLY,
        annotation.CAP_VERSION,
        annotation.Reply(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        annotation.CAP_SET_VISIBILITY,
        annotation.CAP_VERSION,
        annotation.SetVisibility(repository=repository),
    )
    registry.register(
        annotation.CAP_MODERATE,
        annotation.CAP_VERSION,
        annotation.Moderate(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        annotation.CAP_REMAP,
        annotation.CAP_VERSION,
        annotation.RemapOnNewRevision(
            repository=repository, snapshots=snapshots, remapper=remapper
        ),
    )
    registry.register(
        annotation.CAP_LIST_FOR_TARGET,
        annotation.CAP_VERSION,
        annotation.ListForTarget(repository=repository),
    )
    for action in annotation.ANNOTATION_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_retrieval(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
) -> None:
    """Register the retrieval capabilities + deny-by-default grants (LAW-04, FR-RET-002/006/007).

    Search runs hybrid FTS + EXACT pgvector retrieval with the tenant/visibility ACL applied INSIDE
    the query and re-checked before disclosure (rule 50/60, zero leakage). Indexing builds the FTS
    + embedding projections from block passages the publisher supplies (no cross-module reads,
    LAW-13). Tenant scope + acting subject derive from the authenticated context, never the payload.
    """
    tables = build_retrieval_tables(MetaData())
    repository = SqlAlchemyRetrievalRepository(session_factory=session_factory, tables=tables)
    embedding = LocalHashEmbedding()
    registry.register(
        retrieval.CAP_INDEX_REVISION,
        retrieval.CAP_VERSION,
        retrieval.IndexRevision(repository=repository, embedding=embedding, id_factory=_uuid),
    )
    registry.register(
        retrieval.CAP_SEARCH,
        retrieval.CAP_VERSION,
        retrieval.Search(repository=repository, embedding=embedding),
    )
    for action in retrieval.RETRIEVAL_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_ai(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
    query_bus: QueryBus,
    egress_guard: AllowlistEgressGuard,
    audit: InMemoryAuditRecorder,
) -> AiMemorySubjectStore:
    """Register the governed AI capabilities + deny-by-default grants (LAW-04/09, ARCH-009).

    ``ai.answer`` runs the RAG pipeline: it resolves an IMMUTABLE prompt package, resolves the model
    gateway with a no-silent-downgrade rule (a DETERMINISTIC mock provider in the reference build),
    retrieves through the retrieval query bus (authorize-before-retrieval + ACL inside the query),
    routes every model tool call through the Tool Broker (the only path to a capability), verifies
    citations against actually-retrieved passages and guards output before disclosure. The AI actor
    reaches no table, secret or capability directly (ARCH-009). Memory is purpose-limited and
    deletable (FR-AI-006). The reference prompt package is seeded out-of-band (migration/tests), not
    at composition time, so this stays side-effect-free.
    """
    tables = build_ai_tables(MetaData())
    prompt_registry = SqlAlchemyPromptRegistry(session_factory=session_factory, tables=tables)
    memory_repository = SqlAlchemyMemoryRepository(session_factory=session_factory, tables=tables)
    trace_repository = SqlAlchemyTraceRepository(session_factory=session_factory, tables=tables)
    # Multi-scope AI cost/budget enforcement (per-actor/tenant/workflow): the guard denies a request
    # whose projected spend exceeds ANY applicable budget with a typed error + audit and fails safe,
    # and records the provider cost per interaction for reconciliation (FR-AI-008). No tenant
    # configures a budget by default, so an unconfigured tenant is simply unconstrained.
    budget_ledger = SqlAlchemyBudgetLedger(session_factory=session_factory, tables=tables)
    budget_guard = BudgetGuard(ledger=budget_ledger, audit=audit, id_factory=_uuid)
    tools = reference_tools()
    # Any AI tool that fetches an external URL goes through the deny-by-default egress guard, so the
    # AI actor holds no unguarded network authority (SSRF/metadata defense, EVAL-SEC-005, LAW-09).
    executor = CallableToolExecutor()
    executor.register("ai.web.fetch", GuardedFetchTool(guard=egress_guard))
    broker = ToolBroker(tools=tools, grants=reference_grants(), executor=executor)
    registry.register(
        ai.CAP_ANSWER,
        ai.CAP_VERSION,
        ai.Answer(
            gateway=DeterministicMockProvider(),
            broker=broker,
            registry=prompt_registry,
            retrieval=BusRetrievalGateway(query_bus=query_bus),
            traces=trace_repository,
            actors=reference_actor_profiles(),
            model_catalog=reference_model_catalog(),
            primary_profile_id=REFERENCE_PRIMARY_PROFILE_ID,
            tools_by_id=tools,
            id_factory=_uuid,
            budget_guard=budget_guard,
        ),
    )
    registry.register(
        ai.CAP_REMEMBER,
        ai.CAP_VERSION,
        ai.RememberMemory(repository=memory_repository, id_factory=_uuid),
    )
    registry.register(
        ai.CAP_FORGET,
        ai.CAP_VERSION,
        ai.ForgetMemory(repository=memory_repository),
    )
    registry.register(
        ai.CAP_LIST_MEMORY,
        ai.CAP_VERSION,
        ai.ListMemory(repository=memory_repository),
    )
    # Memory rights (FR-AI-006, EVAL-AI-006): audited correction (supersede), portable export and
    # reset/erase, all owner-scoped from the authenticated context (rule 50).
    registry.register(
        ai.CAP_CORRECT_MEMORY,
        ai.CAP_VERSION,
        ai.CorrectMemory(repository=memory_repository, audit=audit, id_factory=_uuid),
    )
    registry.register(
        ai.CAP_EXPORT_MEMORY,
        ai.CAP_VERSION,
        ai.ExportMemory(repository=memory_repository, clock=_utc_now),
    )
    registry.register(
        ai.CAP_RESET_MEMORY,
        ai.CAP_VERSION,
        ai.ResetMemory(repository=memory_repository, audit=audit),
    )
    for action in ai.AI_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))
    # The AI module's DSAR handler bridges its OWN memory store to the privacy rights registry so a
    # privacy erase also clears AI memory (H03 seam, EVAL-DATA-009).
    return AiMemorySubjectStore(repository=memory_repository)


class _SimulationRefGateway:
    """Composition-root :class:`SimulationRefPort`: resolves a simulation's IDENTITY only.

    Research links a simulation by identity through this port and never reaches the simulation
    module's tables from its own domain/adapters (LAW-13/rule 10). The composition root is the one
    place allowed to know both modules; it resolves the published definition via the simulation
    repository and hands research only the stable identity (id/version/content hash).
    """

    def __init__(self, *, repository: SqlAlchemySimulationRepository) -> None:
        self._repository = repository

    def resolve(
        self, *, organization_id: str, simulation_id: str, version: str
    ) -> SimulationRef | None:
        definition = self._repository.get_definition(
            organization_id=organization_id, simulation_id=simulation_id, version=version
        )
        if definition is None:
            return None
        return SimulationRef(
            simulation_id=definition.simulation_id,
            version=definition.version,
            content_hash=definition.content_hash(),
        )


def _register_research(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
    command_bus: CommandBus,
) -> None:
    """Register the research capabilities + deny-by-default grants (LAW-04, FR-RSH-001..006).

    Research owns the ``northstar_research`` schema (RLS-forced, tenant-scoped). The AI-assisted
    draft capability reuses the ONE governed ``ai.answer`` pipeline via :class:`BusAiDraftGateway`
    (dispatched on the authorized command bus, so citations are verified by the AI module before
    research maps them to evidence) — there is no second AI path (FR-RSH-005). A claim is only
    persisted over >=1 verified evidence record, so an uncited/fabricated draft is rejected
    (EVAL-RSH-005). Tenant scope derives from the authenticated context, never the payload.
    """
    tables = build_research_tables(MetaData())
    repository = SqlAlchemyResearchRepository(session_factory=session_factory, tables=tables)
    ai_gateway = BusAiDraftGateway(command_bus=command_bus)
    # Simulation is linked by IDENTITY through a port (docs/37 §3): the composition root resolves it
    # via the simulation repository so research never reaches the simulation module's tables.
    simulation_gateway = _SimulationRefGateway(
        repository=SqlAlchemySimulationRepository(
            session_factory=session_factory, tables=build_simulation_tables(MetaData())
        )
    )
    registry.register(
        research.CAP_CREATE_WORKSPACE,
        research.CAP_VERSION,
        research.CreateWorkspace(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_CREATE_PROJECT,
        research.CAP_VERSION,
        research.CreateProject(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_AUTHOR_DOCUMENT,
        research.CAP_VERSION,
        research.AuthorDocument(repository=repository, id_factory=_uuid),
    )
    registry.register(
        research.CAP_PUBLISH_DOCUMENT,
        research.CAP_VERSION,
        research.PublishDocument(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_REGISTER_EVIDENCE,
        research.CAP_VERSION,
        research.RegisterEvidence(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_ASSERT_CLAIM,
        research.CAP_VERSION,
        research.AssertClaim(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_REGISTER_DATASET,
        research.CAP_VERSION,
        research.RegisterDataset(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_REGISTER_EXPERIMENT,
        research.CAP_VERSION,
        research.RegisterExperiment(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_AI_DRAFT,
        research.CAP_VERSION,
        research.AiAssistedDraft(
            repository=repository, ai_draft=ai_gateway, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        research.CAP_EXPORT_DOCUMENT,
        research.CAP_VERSION,
        research.ExportDocument(repository=repository),
    )
    registry.register(
        research.CAP_PACKAGE_REPRODUCIBILITY,
        research.CAP_VERSION,
        research.PackageReproducibility(repository=repository, clock=_utc_now),
    )
    # Project structure (FR-RSH-001): questions/hypotheses/methods + role-scoped membership.
    registry.register(
        research.CAP_UPDATE_PROJECT,
        research.CAP_VERSION,
        research.UpdateProject(repository=repository),
    )
    registry.register(
        research.CAP_ADD_QUESTION,
        research.CAP_VERSION,
        research.AddQuestion(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_ADD_HYPOTHESIS,
        research.CAP_VERSION,
        research.AddHypothesis(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_ADD_METHOD,
        research.CAP_VERSION,
        research.AddMethod(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_ASSIGN_ROLE,
        research.CAP_VERSION,
        research.AssignRole(repository=repository, clock=_utc_now),
    )
    # Document journey (FR-RSH-002): figure/table/lit-review blocks, simulation link, peer review.
    registry.register(
        research.CAP_ADD_DOCUMENT_BLOCK,
        research.CAP_VERSION,
        research.AddDocumentBlock(repository=repository, clock=_utc_now),
    )
    registry.register(
        research.CAP_LINK_SIMULATION,
        research.CAP_VERSION,
        research.LinkSimulation(
            repository=repository, simulations=simulation_gateway, clock=_utc_now
        ),
    )
    registry.register(
        research.CAP_OPEN_REVIEW,
        research.CAP_VERSION,
        research.OpenReview(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        research.CAP_REVIEW_TRANSITION,
        research.CAP_VERSION,
        research.TransitionReview(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    for action in research.RESEARCH_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_simulation(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
    command_bus: CommandBus,
) -> None:
    """Register the simulation capabilities + deny-by-default grants (LAW-04, FR-SIM-001..008).

    Simulation owns the ``northstar_simulation`` schema (RLS-forced, tenant-scoped). The control
    plane signs short-lived leases with an HMAC key resolved from the secret manager (the crypto
    adapter's ``load_master_key`` — a KMS/HSM swaps only that resolver, rule 50); the reference
    in-process :class:`SandboxExecutor` validates a lease and runs WITHOUT app credentials or the
    scoring key, enforcing the egress allowlist + quotas and failing closed on escape attempts. Runs
    produce immutable hash-chained evidence; scoring is deterministic. AI coaching reuses the ONE
    governed ``ai.answer`` pipeline through the authorized command bus as a SCOPED actor that never
    receives the hidden scoring key (FR-SIM-007). Tenant scope derives from the authenticated
    context, never the payload.
    """
    tables = build_simulation_tables(MetaData())
    repository = SqlAlchemySimulationRepository(session_factory=session_factory, tables=tables)
    evidence_store = SqlAlchemyEvidenceStore(session_factory=session_factory, tables=tables)
    lease_signer = HmacLeaseSigner(load_master_key())
    registry.register(
        simulation.CAP_DEFINE,
        simulation.CAP_VERSION,
        simulation.DefineSimulation(repository=repository),
    )
    registry.register(
        simulation.CAP_PUBLISH,
        simulation.CAP_VERSION,
        simulation.PublishSimulation(repository=repository),
    )
    registry.register(
        simulation.CAP_SET_TIER,
        simulation.CAP_VERSION,
        simulation.SetTrustTier(repository=repository),
    )
    registry.register(
        simulation.CAP_ISSUE_LEASE,
        simulation.CAP_VERSION,
        simulation.IssueLease(
            repository=repository, issuer=lease_signer, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        simulation.CAP_RUN,
        simulation.CAP_VERSION,
        simulation.RunSimulation(
            repository=repository,
            validator=lease_signer,
            executor=SandboxExecutor(),
            evidence_store=evidence_store,
            scoring=DeterministicScoring(),
            clock=_utc_now,
            id_factory=_uuid,
        ),
    )
    registry.register(
        simulation.CAP_COACH,
        simulation.CAP_VERSION,
        simulation.CoachSimulation(
            repository=repository, coach=BusAiCoachGateway(command_bus=command_bus)
        ),
    )
    for action in simulation.SIMULATION_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_extension(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
) -> None:
    """Register the extension capabilities + deny-by-default grants (LAW-04, FR-EXT-001..008).

    Extension owns the ``northstar_extension`` schema (RLS-forced, tenant-scoped). Install AND
    upgrade verify a cryptographic signature + provenance against a TRUSTED-publisher key registry
    and reject an unsigned/forged/tampered/untrusted artifact before activation (FR-EXT-004,
    EVAL-SEC-009); the publisher's review-assigned trust tier then gates which requested
    capabilities the extension may hold (FR-EXT-003). Disable/uninstall stop execution and revoke
    grants (FR-EXT-005). Themes change only presentation (FR-EXT-006). The reference verifier is
    seeded with
    a single first-party publisher whose HMAC key is resolved from the secret manager
    (``load_master_key`` — a KMS/HSM or Sigstore verifier swaps only that adapter, rule 50). Tenant
    scope derives from the authenticated context, never the payload.
    """
    ext_tables = build_extension_tables(MetaData())
    ext_registry = SqlAlchemyExtensionRegistry(session_factory=session_factory, tables=ext_tables)
    verifier = HmacSignatureVerifier(
        {
            "northstar": PublisherKey(
                key=load_master_key(), granted_trust_tier=TrustTier.T0, verified=True
            )
        }
    )
    validator = JsonSchemaManifestValidator(load_extension_schemas())
    registry.register(
        extension.CAP_INSTALL,
        extension.CAP_VERSION,
        extension.InstallExtension(registry=ext_registry, verifier=verifier, validator=validator),
    )
    registry.register(
        extension.CAP_UPGRADE,
        extension.CAP_VERSION,
        extension.UpgradeExtension(registry=ext_registry, verifier=verifier, validator=validator),
    )
    registry.register(
        extension.CAP_DISABLE,
        extension.CAP_VERSION,
        extension.DisableExtension(registry=ext_registry),
    )
    registry.register(
        extension.CAP_UNINSTALL,
        extension.CAP_VERSION,
        extension.UninstallExtension(registry=ext_registry),
    )
    registry.register(
        extension.CAP_APPLY_THEME,
        extension.CAP_VERSION,
        extension.ApplyTheme(registry=ext_registry, validator=validator),
    )
    registry.register(
        extension.CAP_PUBLISH_CATALOG,
        extension.CAP_VERSION,
        extension.PublishCatalog(registry=ext_registry, verifier=verifier, validator=validator),
    )
    for action in extension.EXTENSION_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_messaging(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
    egress_guard: AllowlistEgressGuard,
) -> GuardedWebhookDelivery:
    """Register the messaging capabilities + deny-by-default grants (LAW-04, FR-MSG-001..007).

    Messaging owns the ``northstar_messaging`` schema (RLS-forced, tenant-scoped). ``campaign.send``
    is the single authoritative send path: it ALWAYS applies consent + suppression to a marketing
    campaign (a suppressed/unsubscribed/non-consented recipient is never submitted — FR-MSG-005),
    renders the bound IMMUTABLE template version deterministically (FR-MSG-002), resolves the
    send-at per recipient time zone (FR-MSG-004) and submits to the provider IDEMPOTENTLY so a
    re-submitted
    (campaign, recipient, key) never double-sends (FR-MSG-006). The reference provider is the
    deterministic in-memory :class:`InMemoryMessageProvider`; a real ESP/SMS/push provider is a
    drop-in adapter swap behind the same port. Tenant scope derives from the authenticated context,
    never the payload (rule 50).
    """
    tables = build_messaging_tables(MetaData())
    repository = SqlAlchemyMessagingRepository(session_factory=session_factory, tables=tables)
    provider = InMemoryMessageProvider()
    registry.register(
        messaging.CAP_TEMPLATE_PUBLISH,
        messaging.CAP_VERSION,
        messaging.PublishTemplateVersion(repository=repository),
    )
    registry.register(
        messaging.CAP_CAMPAIGN_CREATE,
        messaging.CAP_VERSION,
        messaging.CreateCampaign(repository=repository, id_factory=_uuid),
    )
    registry.register(
        messaging.CAP_CAMPAIGN_SCHEDULE,
        messaging.CAP_VERSION,
        messaging.ScheduleCampaign(repository=repository),
    )
    registry.register(
        messaging.CAP_CAMPAIGN_SEND,
        messaging.CAP_VERSION,
        messaging.SendCampaign(repository=repository, provider=provider, clock=_utc_now),
    )
    registry.register(
        messaging.CAP_CONSENT_UNSUBSCRIBE,
        messaging.CAP_VERSION,
        messaging.Unsubscribe(repository=repository),
    )
    for action in messaging.MESSAGING_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))
    # The single authoritative outbound-webhook path routes through the egress guard, so a
    # tenant-supplied callback URL cannot reach an internal/metadata target (SSRF, EVAL-SEC-005).
    return GuardedWebhookDelivery(guard=egress_guard)


def _register_analytics(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
) -> None:
    """Register the analytics capabilities + deny-by-default grants (LAW-04, FR-ANL-001..007).

    Analytics owns the ``northstar_analytics`` schema (RLS-forced, tenant-scoped). First-party
    events are the AUTHORITATIVE source: ``analytics.event.ingest`` validates each event against its
    catalog definition and rejects an unknown/malformed/purpose-less-type event before persisting
    it, and ``analytics.report.reach`` computes complete content intelligence with NO external
    dependency (GA independence, EVAL-ANL-002). ``analytics.catalog.register`` rejects a
    purpose-less event type (FR-ANL-003); ``analytics.identity.stitch`` links identities only with
    the required consent (FR-ANL-004). GA4 is an OPTIONAL adapter behind ``Ga4AdapterPort`` (the
    reference in-memory :class:`InMemoryGa4Adapter`); ``analytics.ga4.import`` returns figures
    LABELLED non-authoritative with source freshness + mapping, never authoritative learner state
    (FR-ANL-006). A real GA4 Data API adapter is a drop-in swap. Tenant scope derives from the
    authenticated context, never the payload (rule 50).
    """
    tables = build_analytics_tables(MetaData())
    repository = SqlAlchemyAnalyticsRepository(session_factory=session_factory, tables=tables)
    ga4_adapter = InMemoryGa4Adapter()
    registry.register(
        analytics.CAP_CATALOG_REGISTER,
        analytics.CAP_VERSION,
        analytics.RegisterEventDefinition(repository=repository),
    )
    registry.register(
        analytics.CAP_EVENT_INGEST,
        analytics.CAP_VERSION,
        analytics.IngestEvent(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        analytics.CAP_IDENTITY_STITCH,
        analytics.CAP_VERSION,
        analytics.StitchIdentity(repository=repository, clock=_utc_now),
    )
    registry.register(
        analytics.CAP_REPORT_REACH,
        analytics.CAP_VERSION,
        analytics.ReportReach(repository=repository),
    )
    registry.register(
        analytics.CAP_GA4_IMPORT,
        analytics.CAP_VERSION,
        analytics.ImportGa4(repository=repository, ga4_adapter=ga4_adapter, clock=_utc_now),
    )
    for action in analytics.ANALYTICS_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_commerce(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
) -> None:
    """Register the commerce capabilities + deny-by-default grants (LAW-04, FR-COM-001..005).

    Commerce owns the ``northstar_commerce`` schema (RLS-forced, tenant-scoped). Purchasing grants
    entitlements by REUSING the existing entitlement engine through the
    :class:`EntitlementEngineGateway` (it constructs the engine's grant model + reuses its
    authoritative decision function); the commerce domain never branches on plan/payment-provider
    names (ARCH-019). Payment provider callbacks are SIGNATURE-VERIFIED behind
    :class:`HmacWebhookVerifier`: a forged/unsigned/tampered/replayed callback is rejected
    fail-closed and never mutates entitlements, while a correctly-signed callback is processed
    idempotently via the ``payment_event`` ledger (FR-COM-003). Refunds revoke
    the granted entitlement idempotently + auditably (FR-COM-004). The reference webhook key is
    resolved from the secret manager (``load_master_key`` — a real provider verifier swaps only that
    adapter, rule 50). Tenant scope derives from the authenticated context, never the payload.
    """
    tables = build_commerce_tables(MetaData())
    repository = SqlAlchemyCommerceRepository(session_factory=session_factory, tables=tables)
    entitlement_repo = SqlAlchemyCommerceEntitlementRepository(
        session_factory=session_factory, tables=tables
    )
    gateway = EntitlementEngineGateway(repository=entitlement_repo, id_factory=_uuid)
    # Reference provider key resolved from the secret manager; a real provider (Stripe/etc.) is a
    # drop-in adapter swap behind the same WebhookVerifierPort.
    verifier = HmacWebhookVerifier({"reference": load_master_key()})
    registry.register(
        commerce.CAP_OFFER_PUBLISH,
        commerce.CAP_VERSION,
        commerce.PublishOffer(repository=repository),
    )
    registry.register(
        commerce.CAP_PURCHASE,
        commerce.CAP_VERSION,
        commerce.Purchase(
            repository=repository, entitlements=gateway, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        commerce.CAP_PAYMENT_CALLBACK,
        commerce.CAP_VERSION,
        commerce.ProcessPaymentCallback(
            repository=repository, verifier=verifier, entitlements=gateway, clock=_utc_now
        ),
    )
    registry.register(
        commerce.CAP_REFUND_ISSUE,
        commerce.CAP_VERSION,
        commerce.IssueRefund(
            repository=repository, entitlements=gateway, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        commerce.CAP_AD_DISCLOSE,
        commerce.CAP_VERSION,
        commerce.DiscloseAd(repository=repository),
    )
    for action in commerce.COMMERCE_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_support(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
) -> None:
    """Register the support capabilities + deny-by-default grants (LAW-04, FR-SUP-001..003).

    Support owns the ``northstar_support`` schema (RLS-forced, tenant-scoped). ``support.intake``
    validates input and rejects malformed/oversized/injection-shaped submissions (FR-SUP-001); cases
    carry ownership + a governed lifecycle (FR-SUP-002). ``support.case.view`` returns the MINIMIZED
    projection by default; an elevated/broad read requires an ACTIVE, time-bounded support-access
    grant — an unauthorized elevated read is refused and logged to the tamper-evident access log
    (deny-by-default, FR-SUP-003). Tenant scope derives from the authenticated context, never the
    payload (rule 50).
    """
    tables = build_support_tables(MetaData())
    repository = SqlAlchemySupportRepository(session_factory=session_factory, tables=tables)
    registry.register(
        support.CAP_INTAKE,
        support.CAP_VERSION,
        support.SubmitIntake(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        support.CAP_ASSIGN,
        support.CAP_VERSION,
        support.AssignCase(repository=repository, clock=_utc_now),
    )
    registry.register(
        support.CAP_TRANSITION,
        support.CAP_VERSION,
        support.TransitionCase(repository=repository, clock=_utc_now),
    )
    registry.register(
        support.CAP_REPLY,
        support.CAP_VERSION,
        support.ReplyToCase(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        support.CAP_VIEW,
        support.CAP_VERSION,
        support.ViewCase(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        support.CAP_ACCESS_GRANT,
        support.CAP_VERSION,
        support.GrantSupportAccess(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        support.CAP_ACCESS_REVOKE,
        support.CAP_VERSION,
        support.RevokeSupportAccess(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    for action in support.SUPPORT_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_learning(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
    command_bus: CommandBus,
) -> None:
    """Register the learning capabilities + deny-by-default grants (LAW-04, FR-LRN-001..007).

    Learning owns the ``northstar_learning`` schema (RLS-forced, tenant-scoped). Courses compose
    PUBLISHED knowledge revisions through a READ-ONLY :class:`KnowledgePublishedContent` seam (no
    cross-module writes, LAW-13); progress is stored in the module's OWN tables, never derived from
    analytics (FR-LRN-002). The AI tutor REUSES the ONE governed ``ai.answer`` pipeline through the
    authorized+audited command bus as a scoped actor (LAW-09) — multilingual and never handed an
    assessment answer key. Recommendations require consent and respect entitlements (FR-LRN-007).
    Tenant scope derives from the authenticated context, never the payload (rule 50).
    """
    tables = build_learning_tables(MetaData())
    repository = SqlAlchemyLearningRepository(session_factory=session_factory, tables=tables)
    knowledge_reader = SqlAlchemyKnowledgeRepository(
        session_factory=session_factory, tables=build_knowledge_tables(MetaData())
    )
    content = KnowledgePublishedContent(reader=knowledge_reader)
    tutor = BusAiTutorGateway(command_bus=command_bus)
    consent = InMemoryConsentStore()
    entitlements = InMemoryEntitlementDirectory()

    registry.register(
        learning.CAP_COURSE_COMPOSE,
        learning.CAP_VERSION,
        learning.ComposeCourse(repository=repository, content=content),
    )
    registry.register(
        learning.CAP_COURSE_PUBLISH,
        learning.CAP_VERSION,
        learning.PublishCourse(repository=repository),
    )
    registry.register(
        learning.CAP_PROGRESS_RECORD,
        learning.CAP_VERSION,
        learning.RecordProgress(repository=repository, clock=_utc_now),
    )
    registry.register(
        learning.CAP_PROGRESS_RECORD_ANON,
        learning.CAP_VERSION,
        learning.RecordAnonymousProgress(repository=repository, clock=_utc_now),
    )
    registry.register(
        learning.CAP_PROGRESS_MERGE,
        learning.CAP_VERSION,
        learning.MergeAnonymousProgress(repository=repository, clock=_utc_now),
    )
    registry.register(
        learning.CAP_PROGRESS_RESUME,
        learning.CAP_VERSION,
        learning.ResumeProgress(repository=repository),
    )
    registry.register(
        learning.CAP_OVERLAY_ADD,
        learning.CAP_VERSION,
        learning.AddOverlay(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        learning.CAP_ITEM_PUBLISH,
        learning.CAP_VERSION,
        learning.PublishAssessmentItem(repository=repository),
    )
    registry.register(
        learning.CAP_ATTEMPT_SUBMIT,
        learning.CAP_VERSION,
        learning.SubmitAttempt(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        learning.CAP_CREDENTIAL_EVALUATE,
        learning.CAP_VERSION,
        learning.EvaluateCredential(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        learning.CAP_RECOMMEND_NEXT,
        learning.CAP_VERSION,
        learning.RecommendNext(repository=repository, consent=consent, entitlements=entitlements),
    )
    registry.register(
        learning.CAP_PROFILE_INSPECT,
        learning.CAP_VERSION,
        learning.InspectProfile(repository=repository),
    )
    registry.register(
        learning.CAP_PROFILE_CORRECT,
        learning.CAP_VERSION,
        learning.CorrectProfile(repository=repository),
    )
    registry.register(
        learning.CAP_PROFILE_RESET,
        learning.CAP_VERSION,
        learning.ResetProfile(repository=repository),
    )
    registry.register(
        learning.CAP_TUTOR_ASK,
        learning.CAP_VERSION,
        learning.AskTutor(tutor=tutor),
    )
    for action in learning.LEARNING_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_media(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
    audit: InMemoryAuditRecorder,
) -> None:
    """Register the media capabilities + deny-by-default grants (LAW-04, FR-CNT-009/010).

    Media owns the ``northstar_media`` schema (RLS-forced, tenant-scoped). Ingestion writes ONLY
    through the shared H02 validated object-storage seam (:func:`build_media_storage` wrapping the
    ``ValidatingObjectStorage``), so a mismatched/malicious asset is refused before storage and each
    rejection is audited (EVAL-MED-001, LAW-14) — there is no unvalidated media write path. The
    reference build uses an in-memory object store (a real S3/GCS or filesystem adapter is a drop-in
    swap behind the same ``ObjectStoreLike`` port). Publishing enforces the HARD accessibility gate:
    a video/audio asset requires a transcript AND captions, an image requires alt text or the
    decorative flag, else a typed rejection (EVAL-MED-002, NFR-A11Y-003). Tenant scope derives from
    the authenticated context, never the payload (rule 50).
    """
    tables = build_media_tables(MetaData())
    repository = SqlAlchemyMediaRepository(session_factory=session_factory, tables=tables)
    storage = build_media_storage(inner=InMemoryObjectStorage(), audit=audit)
    registry.register(
        media.CAP_UPLOAD,
        media.CAP_VERSION,
        media.UploadMedia(repository=repository, storage=storage, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        media.CAP_ATTACH_TRANSCRIPT,
        media.CAP_VERSION,
        media.AttachTranscript(repository=repository),
    )
    registry.register(
        media.CAP_ATTACH_CAPTIONS,
        media.CAP_VERSION,
        media.AttachCaptions(repository=repository),
    )
    registry.register(
        media.CAP_ATTACH_ALT,
        media.CAP_VERSION,
        media.AttachAltText(repository=repository),
    )
    registry.register(
        media.CAP_PUBLISH,
        media.CAP_VERSION,
        media.PublishMedia(repository=repository),
    )
    registry.register(
        media.CAP_GET,
        media.CAP_VERSION,
        media.GetMediaAsset(repository=repository),
    )
    registry.register(
        media.CAP_RESOLVE_TIME,
        media.CAP_VERSION,
        media.ResolveTimeSelector(repository=repository),
    )
    for action in media.MEDIA_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_moderation(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
) -> None:
    """Register the moderation capabilities + deny-by-default grants (LAW-04, FR-ANN-007).

    Moderation owns the ``northstar_moderation`` schema (RLS-forced, tenant-scoped) and drives a
    deterministic case lifecycle (EVAL-MOD-001). It references the reportable content
    (annotations/comments) ONLY through ports: the reportable-content provider reads the annotation
    read model to resolve the affected author, and enforcement drives the annotation module's own
    authoritative ``moderate`` capability so an upheld removal/hide can be REVERSED on a granted
    appeal without any cross-module table write (LAW-13). The capability-layer grants authorize each
    action for any authenticated actor; the finer deny-by-default rule (only a moderator/assignee
    may decide, only the affected author may appeal) is enforced inside the capabilities + audited.
    Tenant scope derives from the authenticated context, never the payload (rule 50).
    """
    tables = build_moderation_tables(MetaData())
    repository = SqlAlchemyModerationRepository(session_factory=session_factory, tables=tables)
    annotation_repo = SqlAlchemyAnnotationRepository(
        session_factory=session_factory, tables=build_annotation_tables(MetaData())
    )
    reportable = AnnotationReportableContentProvider(reader=annotation_repo)
    enforcement = AnnotationEnforcementGateway(
        moderator=AnnotationModerationGateway(
            moderate_handler=annotation.Moderate(
                repository=annotation_repo, clock=_utc_now, id_factory=_uuid
            ),
            command_factory=lambda *, annotation_id, kind, reason: annotation.ModerateCommand(
                annotation_id=annotation_id, kind=kind, reason=reason
            ),
        )
    )
    moderators = InMemoryModeratorDirectory()
    registry.register(
        moderation.CAP_SUBMIT_REPORT,
        moderation.CAP_VERSION,
        moderation.SubmitReport(
            repository=repository, reportable=reportable, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        moderation.CAP_TRIAGE,
        moderation.CAP_VERSION,
        moderation.TriageCase(
            repository=repository, moderators=moderators, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        moderation.CAP_ASSIGN,
        moderation.CAP_VERSION,
        moderation.AssignCase(
            repository=repository, moderators=moderators, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        moderation.CAP_DECIDE,
        moderation.CAP_VERSION,
        moderation.DecideCase(
            repository=repository, moderators=moderators, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        moderation.CAP_APPLY_ACTION,
        moderation.CAP_VERSION,
        moderation.ApplyAction(
            repository=repository,
            enforcement=enforcement,
            moderators=moderators,
            clock=_utc_now,
            id_factory=_uuid,
        ),
    )
    registry.register(
        moderation.CAP_SUBMIT_APPEAL,
        moderation.CAP_VERSION,
        moderation.SubmitAppeal(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        moderation.CAP_RESOLVE_APPEAL,
        moderation.CAP_VERSION,
        moderation.ResolveAppeal(
            repository=repository,
            enforcement=enforcement,
            moderators=moderators,
            clock=_utc_now,
            id_factory=_uuid,
        ),
    )
    registry.register(
        moderation.CAP_GET_CASE,
        moderation.CAP_VERSION,
        moderation.GetCase(repository=repository),
    )
    for action in moderation.MODERATION_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_governance(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
) -> None:
    """Register the governance capabilities + deny-by-default grants (LAW-04, FR-GOV-001/002/003).

    Governance owns the ``northstar_governance`` schema (RLS-forced, tenant-scoped) and is DISTINCT
    from the ``governance_studio`` projection module: it records immutable, traceable DECISION
    RECORDS (a correction is a NEW superseding record, never a mutation — EVAL-GOV-001, LAW-07) and
    runs a time-bounded CONTROL-EXCEPTION engine (an exception requires an approver + explicit
    expiry and AUTO-EXPIRES under the evaluation clock; the pure ``no_expired_exception`` honors
    only a non-expired approved exception — EVAL-GOV-002). The capability-layer grants authorize
    each action for any authenticated actor; the finer deny-by-default rule (only an authorized
    approver may grant/revoke an exception) is enforced inside the capabilities via the approver
    directory + audited. The surface is reachable only through capabilities/queries (no direct path,
    FR-GOV-003). Tenant scope derives from the authenticated context, never the payload (rule 50).
    """
    tables = build_governance_tables(MetaData())
    repository = SqlAlchemyGovernanceRepository(session_factory=session_factory, tables=tables)
    approvers = InMemoryApproverDirectory()
    registry.register(
        governance.CAP_RECORD_DECISION,
        governance.CAP_VERSION,
        governance.RecordDecision(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        governance.CAP_SUPERSEDE_DECISION,
        governance.CAP_VERSION,
        governance.SupersedeDecision(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        governance.CAP_GRANT_EXCEPTION,
        governance.CAP_VERSION,
        governance.GrantException(
            repository=repository, approvers=approvers, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        governance.CAP_REVOKE_EXCEPTION,
        governance.CAP_VERSION,
        governance.RevokeException(repository=repository, approvers=approvers, clock=_utc_now),
    )
    registry.register(
        governance.CAP_EVALUATE_EXCEPTION,
        governance.CAP_VERSION,
        governance.EvaluateException(repository=repository, clock=_utc_now),
    )
    for action in governance.GOVERNANCE_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_enterprise(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
    session_store: SqlAlchemySessionStore,
) -> None:
    """Register the enterprise capabilities + deny-by-default grants (LAW-04, FR-IDN-006/LRN-008).

    Enterprise owns the ``northstar_enterprise`` schema (RLS-forced, tenant-scoped) and delivers
    federation/SCIM/LTI/xAPI strictly as ADAPTER capabilities — never an identity-core fork
    (EVAL-IDN-005). Federated login verifies an external IdP assertion through the
    signature-verified :class:`HmacFederationVerifier` (a real OIDC/SAML JWKS verifier is a drop-in
    swap) and maps it DETERMINISTICALLY to a Northstar subject by REUSING the identity directory via
    :class:`IdentitySubjectGateway`; a forged/unverified/expired assertion is rejected. SCIM
    provisioning creates/updates users+groups idempotently and deprovision disables the subject's
    access by REUSING identity session invalidation. LTI launches are signature-verified and mapped
    to an authorized learning context. xAPI emission maps a first-party progress event to an
    xAPI-shaped statement and emits it to the reference :class:`InMemoryLrs`, gated deny-by-default
    on the learner's export consent; it never writes learning state (independence, EVAL-INT-001).
    The reference HMAC issuer/platform keys are resolved from the secret manager
    (``load_master_key``). Tenant scope derives from the authenticated context, never the payload.
    """
    tables = build_enterprise_tables(MetaData())
    repository = SqlAlchemyEnterpriseRepository(session_factory=session_factory, tables=tables)
    identity_directory = SqlAlchemyIdentityDirectory(
        session_factory=session_factory,
        tables=build_identity_tables(MetaData()),
        id_factory=_uuid,
        clock=_utc_now,
    )
    invalidator = InMemorySessionInvalidator(sessions=session_store, clock=_utc_now)
    gateway = IdentitySubjectGateway(directory=identity_directory, invalidator=invalidator)

    # Reference symmetric keys resolved from the secret manager; a real OIDC/SAML/LTI verifier is a
    # drop-in adapter swap behind the same ports (no capability change).
    federation_issuer = os.environ.get(
        "ENTERPRISE_FEDERATION_ISSUER", "https://idp.enterprise.example"
    )
    federation_audience = os.environ.get("ENTERPRISE_FEDERATION_AUDIENCE", "northstar-enterprise")
    lti_issuer = os.environ.get("ENTERPRISE_LTI_ISSUER", "https://lms.enterprise.example")
    federation_verifier = HmacFederationVerifier(
        {federation_issuer: load_master_key()}, audience=federation_audience
    )
    lti_verifier = HmacLtiVerifier({lti_issuer: load_master_key()})
    lrs = InMemoryLrs(id_factory=_uuid)
    consent = InMemoryExportConsent()

    registry.register(
        enterprise.CAP_FEDERATION_LOGIN,
        enterprise.CAP_VERSION,
        enterprise.FederatedLogin(
            verifier=federation_verifier,
            gateway=gateway,
            repository=repository,
            clock=_utc_now,
            id_factory=_uuid,
        ),
    )
    registry.register(
        enterprise.CAP_SCIM_PROVISION,
        enterprise.CAP_VERSION,
        enterprise.ScimProvision(
            repository=repository, gateway=gateway, clock=_utc_now, id_factory=_uuid
        ),
    )
    registry.register(
        enterprise.CAP_SCIM_DEPROVISION,
        enterprise.CAP_VERSION,
        enterprise.ScimDeprovision(repository=repository, gateway=gateway, clock=_utc_now),
    )
    registry.register(
        enterprise.CAP_LTI_LAUNCH,
        enterprise.CAP_VERSION,
        enterprise.LaunchLti(verifier=lti_verifier, clock=_utc_now),
    )
    registry.register(
        enterprise.CAP_XAPI_EMIT,
        enterprise.CAP_VERSION,
        enterprise.EmitXapi(lrs=lrs, consent=consent, clock=_utc_now),
    )
    for action in enterprise.ENTERPRISE_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _register_privacy(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    session_factory: sessionmaker[Session],
    ai_memory_store: AiMemorySubjectStore,
) -> None:
    """Register the privacy capabilities + deny-by-default grants (LAW-04, NFR-PRV-001..005).

    Privacy owns the ``northstar_privacy`` schema (RLS-forced, tenant-scoped) for its data catalog,
    versioned/immutable consent history and data-subject-rights request lifecycle. Deletion
    propagation goes through a :class:`DataSubjectRightsRegistry`: a REPRESENTATIVE set of stores
    (learning progress/overlay, annotation, ai-memory, an object-store blob seam, a
    search/retrieval-projection seam, an analytics seam and a provider seam) register export +
    erasure handlers, so ``privacy.rights.erase`` fans out across every registered store until the
    deletion residue is zero (EVAL-DATA-009). Remaining modules register their own handlers later
    through the same seam. Tenant scope + acting subject derive from the authenticated context, not
    the payload (rule 50); a DSAR is authorized only for the subject or an authorized delegate.
    """
    tables = build_privacy_tables(MetaData())
    repository = SqlAlchemyPrivacyRepository(session_factory=session_factory, tables=tables)

    rights_registry = DataSubjectRightsRegistry()
    for store_id in (
        STORE_LEARNING_PROGRESS,
        STORE_LEARNING_OVERLAY,
        STORE_ANNOTATION,
        STORE_OBJECTSTORE_BLOBS,
        STORE_SEARCH_PROJECTION,
        STORE_ANALYTICS_EVENTS,
        STORE_PROVIDER_EXPORT,
    ):
        rights_registry.register_store(InMemorySubjectStore(store_id))
    # AI memory registers its OWN authoritative DSAR handler (H03 seam), so a privacy erase clears
    # the subject's AI memory through the AI module's real store (EVAL-DATA-009/EVAL-AI-006).
    rights_registry.register_store(ai_memory_store)

    registry.register(
        privacy.CAP_CATALOG_REGISTER,
        privacy.CAP_VERSION,
        privacy.RegisterDataField(repository=repository),
    )
    registry.register(
        privacy.CAP_CATALOG_INSPECT,
        privacy.CAP_VERSION,
        privacy.InspectCatalog(repository=repository),
    )
    registry.register(
        privacy.CAP_CONSENT_RECORD,
        privacy.CAP_VERSION,
        privacy.RecordConsent(repository=repository, clock=_utc_now, id_factory=_uuid),
    )
    registry.register(
        privacy.CAP_CONSENT_HISTORY,
        privacy.CAP_VERSION,
        privacy.ConsentHistory(repository=repository),
    )
    registry.register(
        privacy.CAP_RIGHTS_ACCESS,
        privacy.CAP_VERSION,
        privacy.AccessRights(
            repository=repository,
            registry=rights_registry,
            clock=_utc_now,
            id_factory=_uuid,
        ),
    )
    registry.register(
        privacy.CAP_RIGHTS_EXPORT,
        privacy.CAP_VERSION,
        privacy.ExportRights(
            repository=repository,
            registry=rights_registry,
            clock=_utc_now,
            id_factory=_uuid,
        ),
    )
    registry.register(
        privacy.CAP_RIGHTS_ERASE,
        privacy.CAP_VERSION,
        privacy.EraseRights(
            repository=repository,
            registry=rights_registry,
            clock=_utc_now,
            id_factory=_uuid,
        ),
    )
    registry.register(
        privacy.CAP_RETENTION_SWEEP,
        privacy.CAP_VERSION,
        privacy.SweepRetention(registry=rights_registry, clock=_utc_now),
    )
    for action in privacy.PRIVACY_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))


def _cms_contribution_schema() -> dict[str, object]:
    """Load the canonical ``cms-contribution`` JSON Schema the registry validates against.

    Resolved from the repository's read-only ``spec/`` tree (the single source of truth for
    contracts); the registry is otherwise decoupled from the spec layout.
    """
    repo_root = Path(__file__).resolve().parents[6]
    schema_path = repo_root / "spec" / "contracts" / "schemas" / "cms-contribution.schema.json"
    with schema_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _studio_resource(context: RequestContext, action: str) -> ResourceRef | None:
    """Map a projected permission to the policy resource the kernel engine needs (composition root).

    The Studio module stays decoupled from other modules' resource shapes; this composition-root
    resolver knows that organization actions are tenant-scoped and identity-session reads target the
    caller's own session resource.
    """
    if action.startswith("organization.") and context.tenant_scope:
        return ResourceRef(type=org.RES_ORGANIZATION, id=context.tenant_scope)
    if action.startswith("identity.session."):
        return ResourceRef(type=ident.SESSION_RESOURCE_TYPE, id=context.actor.id)
    return None


def _register_governance_studio(
    *,
    registry: CapabilityRegistry,
    policy: LayeredPolicyEvaluator,
    audit: InMemoryAuditRecorder,
) -> None:
    """Register the Studio read capabilities + sample contributions (LAW-05, ARCH-005/ARCH-022).

    The Studio owns no domain tables: ``studio.compose`` projects module contributions through the
    policy engine and ``studio.audit.explore`` reads the recorded audit trail. Both are simple reads
    authorized for any authenticated actor (the router enforces authentication); the actual
    sensitive data behind each projected surface is independently authorized at its own capability.
    """
    contributions = studio.ContributionRegistry(schema=_cms_contribution_schema())
    for document in sample_contributions():
        contributions.register(document)

    projection = studio.SurfaceProjection(policy=policy, resource_resolver=_studio_resource)
    registry.register(
        studio.CAP_COMPOSE_STUDIO,
        studio.CAP_VERSION,
        studio.ComposeStudio(registry=contributions, projection=projection),
    )
    registry.register(
        studio.CAP_EXPLORE_AUDIT,
        studio.CAP_VERSION,
        studio.ExploreAudit(reader=RecorderAuditReader(audit)),
    )
    policy.grant(PolicyGrant(action=studio.CAP_COMPOSE_STUDIO))
    policy.grant(PolicyGrant(action=studio.CAP_EXPLORE_AUDIT))


def _authenticator(session_store: SqlAlchemySessionStore) -> object:
    """Build an authenticator that derives the tenant scope from the server session (rule 50)."""

    def authenticate(request: object) -> RequestContext | None:
        cookies = getattr(request, "cookies", {}) or {}
        raw = cookies.get("ns_session")
        if not raw:
            return None
        session = session_store.authenticate(raw_token=raw, now=_utc_now())
        if session is None:
            return None
        return RequestContext(
            actor=Actor(type=ActorType.USER, id=session.subject_id),
            correlation_id=f"cor_{uuid.uuid4().hex}",
            tenant_scope=session.tenant_scope,
        )

    return authenticate


def _compose(*, database_url: str | None = None, tracer: TracerPort | None = None) -> _Composition:
    """Construct the fully-wired kernel dependencies and router dependencies (LAW-04)."""
    engine = create_engine_from_url(resolve_database_url(database_url))
    session_factory = create_session_factory(engine)
    registry = CapabilityRegistry()
    dispatcher = CapabilityDispatcher(registry)
    # Durable audit sink (LAW-14): persists every sealed record to northstar_audit.audit_record and
    # still exposes the in-memory trail the Studio explorer reads (subclass of InMemoryAuditRecorder).
    audit = SqlAlchemyAuditRecorder(session_factory=session_factory)
    # Shared deny-by-default egress guard: every outbound HTTP surface (AI fetch tools, webhook
    # delivery) routes through it, extending the simulation sandbox's isolation platform-wide
    # (EVAL-SEC-005). It resolves DNS and audits a refusal; the policy it consumes is pure.
    egress_guard = AllowlistEgressGuard(
        policy=EgressPolicy(allowlist=_egress_allowlist()), audit=audit
    )

    entitlement_service = _register_entitlement(registry=registry, session_factory=session_factory)
    role_directory, resource_resolver = _register_organization(
        registry=registry, session_factory=session_factory
    )

    # The single authoritative policy engine: deny-by-default RBAC + relationship + ABAC for
    # governed organization actions (with fail-closed tenancy), plus explicit grants for the
    # identity edge. Entitlement gating never learns plan/payment names (ARCH-019).
    policy = LayeredPolicyEvaluator(
        action_definitions=org.organization_action_definitions(),
        role_definitions=org.organization_role_definitions(),
        relation_grants=org.organization_relation_grants(),
        roles=role_directory,
        resources=resource_resolver,
        entitlements=entitlement_service,
    )
    # Transactional email (confirmation/reset) + durable outbox + admin-managed templates. Built
    # before identity so the local-auth capabilities can email through a port (LAW-13). The dev
    # mailbox records every email durably; a real SMTP provider is used when NORTHSTAR_SMTP_HOST is
    # configured (email_delivery_from_env).
    tx_messaging_tables = build_messaging_tables(MetaData())
    tx_messaging_repo = SqlAlchemyMessagingRepository(
        session_factory=session_factory, tables=tx_messaging_tables
    )
    email_outbox = SqlAlchemyEmailOutbox(session_factory=session_factory, tables=tx_messaging_tables)
    transactional_email = TransactionalEmailService(
        repository=tx_messaging_repo,
        outbox=email_outbox,
        delivery=email_delivery_from_env(),
        clock=_utc_now,
        id_factory=_uuid,
    )
    registry.register(
        messaging_tx.CAP_TRANSACTIONAL_SEND,
        messaging_tx.CAP_VERSION,
        messaging_tx.SendTransactionalEmail(service=transactional_email),
    )
    registry.register(
        messaging_tx.CAP_TEMPLATE_LIST,
        messaging_tx.CAP_VERSION,
        messaging_tx.ListTemplates(repository=tx_messaging_repo),
    )
    registry.register(
        messaging_tx.CAP_TEMPLATE_GET,
        messaging_tx.CAP_VERSION,
        messaging_tx.GetTemplate(repository=tx_messaging_repo),
    )
    registry.register(
        messaging_tx.CAP_OUTBOX_LIST,
        messaging_tx.CAP_VERSION,
        messaging_tx.ListOutbox(outbox=email_outbox),
    )
    for action in messaging_tx.TRANSACTIONAL_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))
    email_sender = MessagingEmailSender(service=transactional_email)

    session_store, oidc_provider = _register_identity(
        registry=registry,
        policy=policy,
        session_factory=session_factory,
        email_sender=email_sender,
        app_base_url=_app_base_url(),
        tenant=_default_tenant(),
    )

    # Backend/management admin accounts are a SEPARATE, seeded class from self-registered learners.
    # The is_admin flag gates the management console + admin-only endpoints. Seed from env
    # (NORTHSTAR_ADMIN_EMAIL / NORTHSTAR_ADMIN_PASSWORD) idempotently on startup.
    admin_accounts = SqlAlchemyLocalAccountStore(
        session_factory=session_factory,
        tables=build_identity_tables(MetaData()),
        id_factory=_uuid,
        clock=_utc_now,
    )
    _admin_email = os.environ.get("NORTHSTAR_ADMIN_EMAIL")
    _admin_password = os.environ.get("NORTHSTAR_ADMIN_PASSWORD")
    if _admin_email and _admin_password:
        try:
            admin_accounts.ensure_admin(
                organization_id=_default_tenant(),
                email=_admin_email.strip().lower(),
                password_hash=ScryptPasswordHasher().hash(_admin_password),
            )
        except Exception:  # noqa: BLE001 - seeding must never block startup
            pass

    def _admin_lookup(subject_id: str) -> bool:
        try:
            return admin_accounts.is_admin(
                organization_id=_default_tenant(), subject_id=subject_id
            )
        except Exception:  # noqa: BLE001 - fail closed (non-admin) on any lookup error
            return False
    _register_knowledge(registry=registry, policy=policy, session_factory=session_factory)
    _register_codelab(registry=registry, policy=policy, session_factory=session_factory)
    _register_annotation(registry=registry, policy=policy, session_factory=session_factory)
    _register_retrieval(registry=registry, policy=policy, session_factory=session_factory)
    _register_governance_studio(registry=registry, policy=policy, audit=audit)

    command_bus = TracingCommandBus(dispatcher, policy, audit, tracer=tracer or NoOpTracer())
    query_bus = QueryBus(dispatcher, policy)

    # AI reaches retrieval only through the (authorized) query bus, so it is registered after the
    # buses exist; it never touches another module's tables (ARCH-009, LAW-13).
    ai_memory_store = _register_ai(
        registry=registry,
        policy=policy,
        session_factory=session_factory,
        query_bus=query_bus,
        egress_guard=egress_guard,
        audit=audit,
    )
    # Assistant: retrieval-grounded Q&A over a configured external chat model. Registered after the
    # buses exist (it grounds via the authorized query bus); it never touches another module's tables.
    assistant_settings = SqlAlchemyAssistantSettings(
        session_factory=session_factory, tables=build_assistant_tables(MetaData())
    )
    assistant_store = assistant_default_store(
        settings=assistant_settings, tenant=_default_tenant()
    )
    registry.register(
        assistant.CAP_ASK,
        assistant.CAP_VERSION,
        assistant.Ask(
            chat=OpenAICompatibleChatModel(),
            retrieval=AssistantRetrievalGateway(query_bus=query_bus),
            store=assistant_store,
        ),
    )
    for action in assistant.ASSISTANT_CAPABILITIES:
        policy.grant(PolicyGrant(action=action))
    # The AI tutor is available to anonymous visitors too (grounded on public content).
    policy.grant(PolicyGrant(action=assistant.CAP_ASK, actor_ids=frozenset({"anonymous"})))
    # Research reuses ai.answer through the authorized command bus, so it is registered after the
    # buses exist; it never touches another module's tables (LAW-13).
    _register_research(
        registry=registry,
        policy=policy,
        session_factory=session_factory,
        command_bus=command_bus,
    )
    # Simulation reuses ai.answer for scoped coaching through the authorized command bus, so it is
    # registered after the buses exist; it never touches another module's tables (LAW-13).
    _register_simulation(
        registry=registry,
        policy=policy,
        session_factory=session_factory,
        command_bus=command_bus,
    )
    # Extension runtime owns its schema and reaches no other module's tables (LAW-13); its
    # capabilities are pure writes gated by signature/provenance verification + trust tiers.
    _register_extension(registry=registry, policy=policy, session_factory=session_factory)
    # Messaging owns its schema and reaches no other module's tables (LAW-13); its send path is the
    # single authoritative consent/suppression-honouring, idempotent provider submission (LAW-04).
    messaging_webhook = _register_messaging(
        registry=registry,
        policy=policy,
        session_factory=session_factory,
        egress_guard=egress_guard,
    )
    # Analytics owns its schema and reaches no other module's tables (LAW-13); first-party events
    # are the authoritative source and GA4 is an optional non-authoritative adapter behind a port.
    _register_analytics(registry=registry, policy=policy, session_factory=session_factory)
    # Commerce owns its schema and reaches no other module's tables (LAW-13); purchasing reuses the
    # existing entitlement engine and payment callbacks are signature-verified + idempotent.
    _register_commerce(registry=registry, policy=policy, session_factory=session_factory)
    # Support owns its schema and reaches no other module's tables (LAW-13); intake is validated and
    # elevated staff reads are gated by audited, time-bounded support-access grants.
    _register_support(registry=registry, policy=policy, session_factory=session_factory)
    # Privacy owns the northstar_privacy schema and reaches no other module's tables (LAW-13); its
    # DataSubjectRightsRegistry composes each store's export/erase handler so erasure propagates
    # until deletion_residue == 0 (EVAL-DATA-009). Registered before the learning block for clarity.
    _register_privacy(
        registry=registry,
        policy=policy,
        session_factory=session_factory,
        ai_memory_store=ai_memory_store,
    )
    # Media owns the northstar_media schema and reaches no other module's tables (LAW-13); ingestion
    # routes ALL bytes through the shared H02 validated object storage (no unvalidated write path,
    # EVAL-MED-001) and publication enforces the accessibility gate (EVAL-MED-002).
    _register_media(registry=registry, policy=policy, session_factory=session_factory, audit=audit)
    _register_moderation(registry=registry, policy=policy, session_factory=session_factory)
    # Governance owns the northstar_governance schema and reaches no other module's tables (LAW-13);
    # it records immutable decision records and runs the time-bounded control-exception engine that
    # closes GATE-GOVERNANCE (EVAL-GOV-001/002). Distinct from the governance_studio projection.
    _register_governance(registry=registry, policy=policy, session_factory=session_factory)
    # Enterprise owns the northstar_enterprise schema and reaches no other module's tables (LAW-13);
    # federation/SCIM REUSE the identity directory + session invalidation (never a fork), and LTI/
    # xAPI are signature-verified/consent-gated adapters that close GATE-ENTERPRISE (EVAL-IDN-005,
    # EVAL-INT-001). Registered after identity so it can reuse the shared session store.
    _register_enterprise(
        registry=registry,
        policy=policy,
        session_factory=session_factory,
        session_store=session_store,
    )
    # Learning composes published knowledge revisions and reuses ai.answer for the tutor through the
    # authorized command bus, so it is registered after the buses exist; it owns its own schema and
    # reaches no other module's tables (LAW-13). Progress is stored independently from analytics.
    _register_learning(
        registry=registry,
        policy=policy,
        session_factory=session_factory,
        command_bus=command_bus,
    )
    # Layered anti-automation throttling at the HTTP edge (EVAL-SEC-008): the reference in-memory
    # fixed-window limiter behind the RateLimiterPort; a distributed limiter is an adapter swap.
    # Auth fails safe (never fail-open) so a limiter outage cannot open a credential-stuffing gap.
    rate_limiter = RateLimitGuard(limiter=InMemoryRateLimiter(), audit=audit, clock=_utc_now)
    dependencies = AppDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        health=DatabaseHealthProbe(engine),
        version=StaticVersionProbe(framework_version=_framework_version()),
        rate_limiter=rate_limiter,
    )
    dev_idp = _dev_idp_enabled()
    if dev_idp:
        # Same-origin browser flow through the web dev proxy (/api -> API): relative URLs keep the
        # ns_session cookie on the app's origin; secure=False so it is sent over local http.
        callback_url = os.environ.get("IDENTITY_CALLBACK_URL", "/api/auth/callback")
        post_login_url = os.environ.get("NORTHSTAR_POST_LOGIN_URL", "/")
        identity_cookies = IdentityCookieConfig(secure=False, samesite="lax")
    else:
        callback_url = os.environ.get(
            "IDENTITY_CALLBACK_URL", "http://localhost:8000/auth/callback"
        )
        post_login_url = os.environ.get("NORTHSTAR_POST_LOGIN_URL") or None
        identity_cookies = IdentityCookieConfig()
    identity = IdentityApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        session_store=session_store,
        clock=_utc_now,
        callback_url=callback_url,
        cookies=identity_cookies,
        post_login_url=post_login_url,
        admin_lookup=_admin_lookup,
    )
    mock_idp = MockIdpDependencies(provider=oidc_provider)
    organization = OrganizationApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    governance_studio = GovernanceStudioApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    knowledge_deps = KnowledgeApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
        # Published content is publicly readable (anonymous viewers + crawlers) for SEO; writes still
        # require a session. Disable with NORTHSTAR_PUBLIC_CONTENT=0.
        public_tenant=(
            _default_tenant() if os.environ.get("NORTHSTAR_PUBLIC_CONTENT", "1") != "0" else None
        ),
    )
    codelab_deps = CodelabApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    annotation_deps = AnnotationApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    retrieval_deps = RetrievalApiDependencies(
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    ai_deps = AiApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    assistant_deps = AssistantApiDependencies(
        command_bus=command_bus,
        authenticate=_authenticator(session_store),
        store=assistant_store,
        admin_lookup=_admin_lookup,
        public_tenant=(
            _default_tenant() if os.environ.get("NORTHSTAR_PUBLIC_CONTENT", "1") != "0" else None
        ),
    )
    admin_deps = AdminApiDependencies(
        authenticate=_authenticator(session_store),
        admin_lookup=_admin_lookup,
        session_factory=session_factory,
        tenant=_default_tenant(),
    )
    research_deps = ResearchApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    simulation_deps = SimulationApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    extension_deps = ExtensionApiDependencies(
        command_bus=command_bus,
        authenticate=_authenticator(session_store),
    )
    messaging_deps = MessagingApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
        admin_lookup=_admin_lookup,
    )
    analytics_deps = AnalyticsApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    commerce_deps = CommerceApiDependencies(
        command_bus=command_bus,
        authenticate=_authenticator(session_store),
    )
    support_deps = SupportApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    learning_deps = LearningApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    privacy_deps = PrivacyApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    media_deps = MediaApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    moderation_deps = ModerationApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    governance_deps = GovernanceApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    enterprise_deps = EnterpriseApiDependencies(
        command_bus=command_bus,
        query_bus=query_bus,
        authenticate=_authenticator(session_store),
    )
    return _Composition(
        dependencies=dependencies,
        identity=identity,
        mock_idp=mock_idp,
        organization=organization,
        governance_studio=governance_studio,
        knowledge=knowledge_deps,
        codelab=codelab_deps,
        annotation=annotation_deps,
        retrieval=retrieval_deps,
        ai=ai_deps,
        assistant=assistant_deps,
        admin=admin_deps,
        research=research_deps,
        simulation=simulation_deps,
        extension=extension_deps,
        messaging=messaging_deps,
        analytics=analytics_deps,
        commerce=commerce_deps,
        support=support_deps,
        learning=learning_deps,
        privacy=privacy_deps,
        media=media_deps,
        moderation=moderation_deps,
        governance=governance_deps,
        enterprise=enterprise_deps,
        messaging_webhook=messaging_webhook,
    )


def build_dependencies(
    *, database_url: str | None = None, tracer: TracerPort | None = None
) -> AppDependencies:
    """Construct fully-wired :class:`AppDependencies` from the environment/config.

    The command bus is wrapped so every dispatched command emits a span through the injected
    :class:`TracerPort` (NFR-OPS-001); ``tracer`` defaults to a no-op tracer so the kernel path
    is traceable without forcing a telemetry backend. Identity capabilities are registered on the
    same authoritative registry (LAW-04).
    """
    return _compose(database_url=database_url, tracer=tracer).dependencies


def build_app(*, database_url: str | None = None) -> FastAPI:
    """Build the ready-to-serve ASGI app with real adapters + OpenTelemetry tracing wired in.

    A :class:`TracerProvider` is built locally (never set as the global provider, so repeated
    construction under test stays side-effect-free) and used both to trace command dispatch and to
    instrument the FastAPI app, so an inbound HTTP request span parents the command span. The
    identity ``/auth/*`` router is mounted and its dependencies bound to ``app.state``.
    """
    provider = build_tracer_provider()
    tracer = build_tracer(provider)
    composition = _compose(database_url=database_url, tracer=tracer)
    app = create_app(composition.dependencies)
    app.include_router(create_identity_router())
    bind_identity_dependencies(app.state, composition.identity)
    if _dev_idp_enabled():
        app.include_router(create_mock_idp_router())
        bind_mock_idp_dependencies(app.state, composition.mock_idp)
    app.include_router(create_organization_router())
    bind_organization_dependencies(app.state, composition.organization)
    app.include_router(create_governance_studio_router())
    bind_governance_studio_dependencies(app.state, composition.governance_studio)
    app.include_router(create_knowledge_router())
    bind_knowledge_dependencies(app.state, composition.knowledge)
    app.include_router(create_codelab_router())
    bind_codelab_dependencies(app.state, composition.codelab)
    # SEO: auto-generated sitemap.xml + robots.txt from published content.
    app.include_router(create_admin_router())
    bind_admin_dependencies(app.state, composition.admin)
    app.include_router(create_seo_router())
    bind_seo_dependencies(
        app.state,
        SeoDependencies(
            query_bus=composition.dependencies.query_bus,
            public_tenant=_default_tenant(),
            site_url=os.environ.get("NORTHSTAR_SITE_URL", "http://localhost:5173"),
        ),
    )
    app.include_router(create_annotation_router())
    bind_annotation_dependencies(app.state, composition.annotation)
    app.include_router(create_retrieval_router())
    bind_retrieval_dependencies(app.state, composition.retrieval)
    app.include_router(create_ai_router())
    bind_ai_dependencies(app.state, composition.ai)
    app.include_router(create_assistant_router())
    bind_assistant_dependencies(app.state, composition.assistant)
    app.include_router(create_research_router())
    bind_research_dependencies(app.state, composition.research)
    app.include_router(create_simulation_router())
    bind_simulation_dependencies(app.state, composition.simulation)
    app.include_router(create_extension_router())
    bind_extension_dependencies(app.state, composition.extension)
    app.include_router(create_messaging_router())
    bind_messaging_dependencies(app.state, composition.messaging)
    app.include_router(create_analytics_router())
    bind_analytics_dependencies(app.state, composition.analytics)
    app.include_router(create_commerce_router())
    bind_commerce_dependencies(app.state, composition.commerce)
    app.include_router(create_support_router())
    bind_support_dependencies(app.state, composition.support)
    app.include_router(create_learning_router())
    bind_learning_dependencies(app.state, composition.learning)
    app.include_router(create_privacy_router())
    bind_privacy_dependencies(app.state, composition.privacy)
    app.include_router(create_media_router())
    bind_media_dependencies(app.state, composition.media)
    app.include_router(create_moderation_router())
    bind_moderation_dependencies(app.state, composition.moderation)
    app.include_router(create_governance_router())
    bind_governance_dependencies(app.state, composition.governance)
    app.include_router(create_enterprise_router())
    bind_enterprise_dependencies(app.state, composition.enterprise)
    instrument_fastapi_app(app, tracer_provider=provider)
    return app
