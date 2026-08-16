"""Row-Level-Security helpers: the tenant GUC and per-table RLS policies (FR-POL-004, rule 50).

RLS is **defense-in-depth**, not a replacement for application policy/capability checks: the
authoritative decision is made by the layered policy engine and repositories always filter by
``organization_id``. These helpers set the per-transaction tenant GUC
(``northstar.tenant_id``) and enable ``FORCE ROW LEVEL SECURITY`` with a tenant-isolation policy so
a non-superuser database role cannot read, insert or update another tenant's rows even if an
application predicate were ever omitted.

Identifiers passed here (schema/table/column) are trusted internal constants, never user input;
tenant values are always bound parameters.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

TENANT_GUC = "northstar.tenant_id"


def set_tenant_guc(session: Session, tenant_id: str) -> None:
    """Set the tenant GUC for the current transaction (``true`` ⇒ ``SET LOCAL`` semantics).

    RLS policies read this via ``current_setting('northstar.tenant_id', true)``; when it is unset
    the setting is ``NULL`` and every tenant-scoped policy denies (deny-by-default).
    """
    session.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": TENANT_GUC, "value": tenant_id},
    )


def clear_tenant_guc(session: Session) -> None:
    """Reset the tenant GUC to empty for the current transaction."""
    session.execute(
        text("SELECT set_config(:name, '', true)"),
        {"name": TENANT_GUC, "value": ""},
    )


def apply_tenant_rls(
    connection: Connection,
    *,
    schema: str,
    table: str,
    tenant_column: str = "organization_id",
) -> None:
    """Enable + FORCE RLS on ``schema.table`` with a tenant-isolation policy on ``tenant_column``.

    The policy's ``USING`` (reads/updates/deletes) and ``WITH CHECK`` (inserts/updates) predicates
    both require ``tenant_column = current_setting('northstar.tenant_id', true)``. ``FORCE`` makes
    even the table owner subject to RLS (superusers still bypass, by design).
    """
    policy = f"{table}_tenant_isolation"
    predicate = f"{tenant_column} = current_setting('{TENANT_GUC}', true)"
    connection.execute(text(f'ALTER TABLE "{schema}"."{table}" ENABLE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE "{schema}"."{table}" FORCE ROW LEVEL SECURITY'))
    # Idempotent (re-runnable to head in a fresh version schema): drop any prior policy.
    connection.execute(text(f'DROP POLICY IF EXISTS "{policy}" ON "{schema}"."{table}"'))
    connection.execute(
        text(
            f'CREATE POLICY "{policy}" ON "{schema}"."{table}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )


def drop_tenant_rls(connection: Connection, *, schema: str, table: str) -> None:
    """Drop the tenant-isolation policy and disable RLS on ``schema.table`` (downgrade)."""
    policy = f"{table}_tenant_isolation"
    connection.execute(text(f'DROP POLICY IF EXISTS "{policy}" ON "{schema}"."{table}"'))
    connection.execute(text(f'ALTER TABLE "{schema}"."{table}" NO FORCE ROW LEVEL SECURITY'))
    connection.execute(text(f'ALTER TABLE "{schema}"."{table}" DISABLE ROW LEVEL SECURITY'))
