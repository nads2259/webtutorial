"""SQLAlchemy Core table definitions for the identity data owner (schema ``northstar_identity``).

Infrastructure is allowed here (rule 10). The tables mirror migration ``000003_identity`` exactly
(columns, constraints, indexes) and live in the ``northstar_identity`` schema on PostgreSQL. The
builder is parameterised on ``schema`` so portable unit tests can materialise the same shape in
SQLite's default schema (``schema=None``) via ``metadata.create_all``; on PostgreSQL the tables are
created by the migration and the adapter binds to them here for typed, parameterised access.

Security invariants baked into the shape (docs/07 §4, rule 50): the session table stores only the
**SHA-256 of the opaque session token** (``token_sha256``), never the raw token; external
identities are keyed by ``(issuer, subject)`` — email is deliberately not an identity key.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

IDENTITY_SCHEMA = "northstar_identity"

# The pre-existing identity tables that carry a ``tenant_scope`` column and therefore get FORCED
# tenant Row-Level Security in migration 000022 (closing the verify_005 app-layer-only finding).
IDENTITY_EXISTING_RLS_TABLES: tuple[str, ...] = ("subject", "session")

# The new impersonation/break-glass tables (all tenant-scoped by ``tenant_scope``) — created with
# FORCED tenant RLS by migration 000022.
IDENTITY_IMPERSONATION_TABLES: tuple[str, ...] = (
    "impersonation_grant",
    "break_glass_access",
    "post_use_review",
)


def _jsonb() -> JSON:
    """A JSON column that renders as ``jsonb`` on PostgreSQL and portable ``JSON`` elsewhere."""
    return JSON().with_variant(JSONB, "postgresql")


@dataclass(frozen=True)
class IdentityTables:
    """The Core tables backing the identity directory, sessions and credentials."""

    subject: Table
    user_account: Table
    external_identity: Table
    session: Table
    credential: Table
    totp_credential: Table
    webauthn_credential: Table
    impersonation_grant: Table
    break_glass_access: Table
    post_use_review: Table
    password_credential: Table
    verification_token: Table
    account_event: Table


# Local-auth tables (migration 000030) are tenant-scoped by ``organization_id`` and get FORCED
# tenant RLS.
IDENTITY_LOCAL_AUTH_TABLES: tuple[str, ...] = (
    "password_credential",
    "verification_token",
    "account_event",
)


def build_identity_tables(
    metadata: MetaData, *, schema: str | None = IDENTITY_SCHEMA
) -> IdentityTables:
    """Define the identity tables on ``metadata`` in ``schema`` (mirrors migration 000003)."""
    subject = Table(
        "subject",
        metadata,
        Column("subject_id", String, primary_key=True),
        Column("subject_type", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("tenant_scope", String, nullable=True),
        schema=schema,
    )

    user_account = Table(
        "user_account",
        metadata,
        Column("user_id", String, primary_key=True),
        Column(
            "subject_id",
            String,
            ForeignKey(subject.c.subject_id),
            nullable=False,
        ),
        Column("primary_email", String, nullable=True),
        Column("display_name", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )

    external_identity = Table(
        "external_identity",
        metadata,
        Column("issuer", String, primary_key=True),
        Column("subject", String, primary_key=True),
        Column(
            "user_id",
            String,
            ForeignKey(user_account.c.user_id),
            nullable=False,
        ),
        Column("linked_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )

    session = Table(
        "session",
        metadata,
        Column("session_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        # Only the hash of the opaque session token is stored (docs/07 §4); never the raw token.
        Column("token_sha256", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("idle_expires_at", DateTime(timezone=True), nullable=False),
        Column("absolute_expires_at", DateTime(timezone=True), nullable=False),
        Column("assurance", String, nullable=False),
        Column("tenant_scope", String, nullable=True),
        Column("revoked_at", DateTime(timezone=True), nullable=True),
        Column("rotated_from", String, nullable=True),
        schema=schema,
    )
    Index("uq_session_token_sha256", session.c.token_sha256, unique=True)
    Index("session_subject_idx", session.c.subject_id)

    credential = Table(
        "credential",
        metadata,
        Column("credential_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("factor_type", String, nullable=False),
        Column("material", _jsonb(), nullable=False),
        Column("label", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("credential_subject_idx", credential.c.subject_id)

    # Real MFA credentials (FR-IDN-003). The TOTP secret is stored so codes can be verified; a
    # production deployment wraps it with the secret manager/KMS envelope (rule 50). The monotonic
    # ``last_used_step`` enforces TOTP replay protection; ``sign_count`` rejects cloned passkeys.
    totp_credential = Table(
        "totp_credential",
        metadata,
        Column("credential_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("secret", String, nullable=False),
        Column("digits", Integer, nullable=False),
        Column("period", Integer, nullable=False),
        Column("algorithm", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("confirmed_at", DateTime(timezone=True), nullable=True),
        Column("last_used_step", BigInteger, nullable=True),
        Column("label", String, nullable=True),
        schema=schema,
    )
    Index("uq_totp_subject", totp_credential.c.subject_id, unique=True)

    webauthn_credential = Table(
        "webauthn_credential",
        metadata,
        Column("credential_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("public_key", LargeBinary, nullable=False),
        Column("sign_count", BigInteger, nullable=False),
        Column("rp_id", String, nullable=False),
        Column("origin", String, nullable=False),
        Column("aaguid", String, nullable=True),
        Column("transports", _jsonb(), nullable=True),
        Column("label", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index("webauthn_subject_idx", webauthn_credential.c.subject_id)

    # Impersonation + break-glass (FR-IDN-007/008, migration 000022). Every row is tenant-scoped by
    # a NOT NULL ``tenant_scope`` — the RLS tenant column and the predicate every query includes.
    impersonation_grant = Table(
        "impersonation_grant",
        metadata,
        Column("grant_id", String, primary_key=True),
        Column("tenant_scope", String, nullable=False),
        Column("real_actor_id", String, nullable=False),
        Column("impersonated_subject_id", String, nullable=False),
        Column("reason", String, nullable=False),
        Column("started_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
        Column("approved_by", String, nullable=True),
        Column("ended_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("impersonation_grant_tenant_idx", impersonation_grant.c.tenant_scope)

    break_glass_access = Table(
        "break_glass_access",
        metadata,
        Column("access_id", String, primary_key=True),
        Column("tenant_scope", String, nullable=False),
        Column("operator_id", String, nullable=False),
        Column("justification", String, nullable=False),
        Column("severity", String, nullable=False),
        Column("invoked_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
        Column("authorized_by", String, nullable=True),
        schema=schema,
    )
    Index("break_glass_access_tenant_idx", break_glass_access.c.tenant_scope)

    post_use_review = Table(
        "post_use_review",
        metadata,
        Column("review_id", String, primary_key=True),
        Column("tenant_scope", String, nullable=False),
        Column("access_id", String, nullable=False),
        Column("status", String, nullable=False),
        Column("opened_at", DateTime(timezone=True), nullable=False),
        Column("resolved_at", DateTime(timezone=True), nullable=True),
        Column("resolved_by", String, nullable=True),
        Column("resolution", String, nullable=True),
        schema=schema,
    )
    Index("post_use_review_tenant_idx", post_use_review.c.tenant_scope)
    Index("post_use_review_access_idx", post_use_review.c.tenant_scope, post_use_review.c.access_id)

    # Local (email + password) auth (migration 000030). Tenant-scoped by ``organization_id``.
    # The password is stored only as a salted scrypt hash; email uniqueness is case-insensitive per
    # tenant. Verification tokens store only the SHA-256 of the opaque token (single-use, expiring).
    password_credential = Table(
        "password_credential",
        metadata,
        Column("user_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("organization_id", String, nullable=False),
        Column("email", String, nullable=False),
        Column("password_hash", String, nullable=False),
        Column("email_verified", Boolean, nullable=False),
        Column("is_admin", Boolean, nullable=False, server_default="false"),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index(
        "uq_password_credential_email",
        password_credential.c.organization_id,
        password_credential.c.email,
        unique=True,
    )
    Index("password_credential_subject_idx", password_credential.c.subject_id)

    verification_token = Table(
        "verification_token",
        metadata,
        Column("token_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("token_sha256", String, nullable=False),
        Column("purpose", String, nullable=False),
        Column("subject_id", String, nullable=False),
        Column("email", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
        Column("consumed_at", DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    Index("uq_verification_token_hash", verification_token.c.token_sha256, unique=True)

    account_event = Table(
        "account_event",
        metadata,
        Column("event_id", String, primary_key=True),
        Column("organization_id", String, nullable=False),
        Column("subject_id", String, nullable=False),
        Column("event_type", String, nullable=False),
        Column("detail", Text, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    Index(
        "account_event_subject_idx",
        account_event.c.organization_id,
        account_event.c.subject_id,
    )
    Index("account_event_tenant_idx", account_event.c.organization_id, account_event.c.created_at)

    return IdentityTables(
        subject=subject,
        user_account=user_account,
        external_identity=external_identity,
        session=session,
        credential=credential,
        totp_credential=totp_credential,
        webauthn_credential=webauthn_credential,
        impersonation_grant=impersonation_grant,
        break_glass_access=break_glass_access,
        post_use_review=post_use_review,
        password_credential=password_credential,
        verification_token=verification_token,
        account_event=account_event,
    )
