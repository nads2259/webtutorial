"""Research project structure: questions, hypotheses, methods and role-scoped membership.

Pure and infrastructure-free (rule 10, LAW-02). This models the EVAL-RSH-001 intent that a research
project is more than a title: it carries the questions it asks, the hypotheses that answer those
questions (a :class:`Hypothesis` MUST link to a :class:`ResearchQuestion`), the methods it uses, and
role-scoped contributor membership. Authorization on role-gated actions is DENY-BY-DEFAULT
(LAW-19/rule 50): :func:`authorize_role` refuses an action unless the acting subject holds a role
the action explicitly grants — a subject with no membership is always refused. Roles are opaque
capability grants, never plan names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .errors import HypothesisWithoutQuestion, ResearchInvariantViolation, RoleNotPermitted


def _require(condition: bool, message: str, code: str = "research.invariant") -> None:
    if not condition:
        raise ResearchInvariantViolation(message, code=code)


class ContributorRole(StrEnum):
    """A contributor's role within a research project (role-scoped membership, FR-RSH-001)."""

    LEAD = "lead"
    CONTRIBUTOR = "contributor"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


# Role-gated project/document actions -> the roles that may perform them (deny-by-default: an action
# absent from this map is not role-gated; an action present grants ONLY the listed roles). A VIEWER
# never mutates; assigning roles and updating the project are LEAD-only (LAW-19).
ROLE_GATED_ACTIONS: dict[str, frozenset[ContributorRole]] = {
    "research.project.update": frozenset({ContributorRole.LEAD}),
    "research.project.assign-role": frozenset({ContributorRole.LEAD}),
    "research.project.add-question": frozenset({ContributorRole.LEAD, ContributorRole.CONTRIBUTOR}),
    "research.project.add-hypothesis": frozenset(
        {ContributorRole.LEAD, ContributorRole.CONTRIBUTOR}
    ),
    "research.project.add-method": frozenset({ContributorRole.LEAD, ContributorRole.CONTRIBUTOR}),
    "research.document.add-block": frozenset({ContributorRole.LEAD, ContributorRole.CONTRIBUTOR}),
    "research.document.link-simulation": frozenset(
        {ContributorRole.LEAD, ContributorRole.CONTRIBUTOR}
    ),
    "research.document.open-review": frozenset({ContributorRole.LEAD, ContributorRole.CONTRIBUTOR}),
}


def authorize_role(action: str, role: ContributorRole | None) -> None:
    """Deny-by-default role check: raise :class:`RoleNotPermitted` unless ``role`` grants it.

    An action not in :data:`ROLE_GATED_ACTIONS` is not role-gated and passes. A role-gated action
    requires a membership whose role is in the action's allowed set; ``None`` (no membership) is
    always refused.
    """
    allowed = ROLE_GATED_ACTIONS.get(action)
    if allowed is None:
        return
    if role is None or role not in allowed:
        raise RoleNotPermitted(action, role)


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    """A role-scoped contributor membership in a research project (tenant-scoped, FR-RSH-001)."""

    organization_id: str
    project_id: str
    subject_id: str
    role: ContributorRole
    created_at: datetime

    def __post_init__(self) -> None:
        _require(
            bool(self.organization_id), "organization_id required", code="research.membership.scope"
        )
        _require(bool(self.project_id), "project_id required", code="research.membership.project")
        _require(bool(self.subject_id), "subject_id required", code="research.membership.subject")


@dataclass(frozen=True, slots=True)
class ResearchQuestion:
    """A research question posed inside a project (FR-RSH-001)."""

    question_id: str
    organization_id: str
    project_id: str
    prompt: str
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.question_id), "question_id required", code="research.question.id")
        _require(
            bool(self.organization_id), "organization_id required", code="research.question.scope"
        )
        _require(bool(self.project_id), "project_id required", code="research.question.project")
        _require(
            1 <= len(self.prompt.strip()) <= 2000,
            "question prompt must be 1..2000 chars",
            code="research.question.prompt",
        )


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """A hypothesis that MUST link to a research question (FR-RSH-001).

    The constructor is the invariant: an empty ``question_id`` raises
    :class:`HypothesisWithoutQuestion`, so an orphan hypothesis can never be built or persisted.
    """

    hypothesis_id: str
    organization_id: str
    project_id: str
    question_id: str
    statement: str
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.hypothesis_id), "hypothesis_id required", code="research.hypothesis.id")
        _require(
            bool(self.organization_id), "organization_id required", code="research.hypothesis.scope"
        )
        _require(bool(self.project_id), "project_id required", code="research.hypothesis.project")
        if not self.question_id:
            raise HypothesisWithoutQuestion()
        _require(
            1 <= len(self.statement.strip()) <= 2000,
            "hypothesis statement must be 1..2000 chars",
            code="research.hypothesis.statement",
        )


@dataclass(frozen=True, slots=True)
class ResearchMethod:
    """A method/procedure a project uses (FR-RSH-001)."""

    method_id: str
    organization_id: str
    project_id: str
    name: str
    description: str
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require(bool(self.method_id), "method_id required", code="research.method.id")
        _require(
            bool(self.organization_id), "organization_id required", code="research.method.scope"
        )
        _require(bool(self.project_id), "project_id required", code="research.method.project")
        _require(
            1 <= len(self.name.strip()) <= 300,
            "method name must be 1..300 chars",
            code="research.method.name",
        )
