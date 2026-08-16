"""Organization module: organizations, workspaces, teams, memberships and roles (docs/07 §5, §9).

First-party module (hexagonal, `module.yaml`). The organization is the tenant root: every
tenant-scoped record carries an explicit ``organization_id`` scope, and cross-tenant assignment is
rejected in the domain. Capabilities run through the kernel command/query buses (LAW-04); the
module owns the ``northstar_organization`` schema (LAW-13).
"""
