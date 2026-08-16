"""Sample first-party Studio contributions composing identity + organization capabilities.

These ``cms-contribution`` 1.0 documents prove end-to-end composition: the Studio shell hosts
surfaces whose actions are **existing, registered** identity and organization capabilities — it adds
no new domain behaviour and writes no tables. In a fully modularised deployment each module ships
its own contribution document; they are co-located here so the reference wiring (and tests) can
register real, schema-valid contributions. Permission strings are the authoritative capability
action names those modules register; drift is caught by the composition/proxy tests.
"""

from __future__ import annotations

from collections.abc import Mapping

# Authoritative capability action names contributed as Studio surfaces (kept in sync by tests).
_ORG_MEMBERSHIP_LIST = "organization.membership.list"
_ORG_MEMBERSHIP_ADD = "organization.membership.add"
_ORG_ROLE_ASSIGN = "organization.role.assign"
_IDENTITY_SESSION_DESCRIBE = "identity.session.describe"
_STUDIO_AUDIT_EXPLORE = "studio.audit.explore"

_STUDIO_API = "1.0.0"


def organization_contribution() -> Mapping[str, object]:
    """Organization surfaces: read members, manage org (sensitive) and explore audit evidence."""
    return {
        "contribution_version": "1.0",
        "module_id": "northstar.organization",
        "compatibility": {"studio_api": _STUDIO_API},
        "permissions": [
            _ORG_MEMBERSHIP_LIST,
            _ORG_MEMBERSHIP_ADD,
            _ORG_ROLE_ASSIGN,
            _STUDIO_AUDIT_EXPLORE,
        ],
        "navigation": [
            {
                "id": "nav.org.members",
                "label_key": "studio.nav.org.members",
                "workbench_id": "organization.members",
                "order": 10,
            },
            {
                "id": "nav.org.administration",
                "label_key": "studio.nav.org.administration",
                "workbench_id": "organization.administration",
                "order": 20,
            },
            {
                "id": "nav.org.audit",
                "label_key": "studio.nav.org.audit",
                "workbench_id": "organization.audit",
                "order": 30,
            },
        ],
        "workbenches": [
            {
                "id": "organization.members",
                "route": "/organizations/members",
                "component": "OrganizationMembersWorkbench",
                "required_permissions": [_ORG_MEMBERSHIP_LIST],
                "danger_level": "normal",
            },
            {
                "id": "organization.administration",
                "route": "/organizations/administration",
                "component": "OrganizationAdministrationWorkbench",
                "required_permissions": [_ORG_MEMBERSHIP_ADD, _ORG_ROLE_ASSIGN],
                "danger_level": "sensitive",
            },
            {
                "id": "organization.audit",
                "route": "/organizations/audit",
                "component": "AuditExplorerWorkbench",
                "required_permissions": [_ORG_MEMBERSHIP_LIST, _STUDIO_AUDIT_EXPLORE],
                "danger_level": "normal",
            },
        ],
    }


def identity_contribution() -> Mapping[str, object]:
    """Identity surface: describe the actor's own sessions (read-only)."""
    return {
        "contribution_version": "1.0",
        "module_id": "northstar.identity",
        "compatibility": {"studio_api": _STUDIO_API},
        "permissions": [_IDENTITY_SESSION_DESCRIBE],
        "navigation": [
            {
                "id": "nav.identity.sessions",
                "label_key": "studio.nav.identity.sessions",
                "workbench_id": "identity.sessions",
                "order": 40,
            }
        ],
        "workbenches": [
            {
                "id": "identity.sessions",
                "route": "/identity/sessions",
                "component": "IdentitySessionsWorkbench",
                "required_permissions": [_IDENTITY_SESSION_DESCRIBE],
                "danger_level": "normal",
            }
        ],
    }


def sample_contributions() -> tuple[Mapping[str, object], ...]:
    """All first-party sample contributions registered by the reference wiring."""
    return (organization_contribution(), identity_contribution())
