"""Typed, pure research-domain errors (LAW-02, rule 30/40).

Deny-by-default, explainable refusals with machine-comparable ``code`` values. Adapters map these
to RFC 9457 problem details at the API edge; the domain stays infrastructure-free. The
``ClaimWithoutEvidence`` refusal is the central research invariant (FR-RSH-003): a claim that links
to zero evidence records can never be constructed, so it can never be persisted.
"""

from __future__ import annotations


class ResearchError(Exception):
    """Base class for research-domain errors (deny-by-default, explainable)."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else self.__class__.__name__


class ResearchInvariantViolation(ResearchError):  # noqa: N818 canonical error name
    """A pure research value-object invariant was violated."""

    def __init__(self, message: str, *, code: str = "research.invariant") -> None:
        super().__init__(message, code=code)


class ClaimWithoutEvidence(ResearchError):  # noqa: N818 canonical error name
    """A claim was asserted with zero evidence records — rejected by the domain (FR-RSH-003).

    Every claim MUST link to one or more evidence records with provenance and version identity. An
    uncited/fabricated AI-produced claim yields no verified evidence, so this refusal is what makes
    it impossible to persist an uncited claim (FR-RSH-005, EVAL-RSH-005).
    """

    def __init__(self) -> None:
        super().__init__(
            "a claim must link to at least one evidence record; a claim with zero evidence is "
            "rejected",
            code="research.claim.no_evidence",
        )


class EvidenceNotFound(ResearchError):  # noqa: N818 canonical error name
    """A referenced evidence record does not exist in the caller's scope."""

    def __init__(self, evidence_id: str) -> None:
        self.evidence_id = evidence_id
        super().__init__(
            f"evidence record '{evidence_id}' is not available in this scope",
            code="research.evidence.not_found",
        )


class ResearchNotFound(ResearchError):  # noqa: N818 canonical error name
    """A workspace/project/document is absent or belongs to another tenant (fail closed)."""

    def __init__(self, kind: str, resource_id: str) -> None:
        self.kind = kind
        self.resource_id = resource_id
        super().__init__(
            f"{kind} '{resource_id}' is not available in this scope",
            code=f"research.{kind}.not_found",
        )


class ImmutableRevisionError(ResearchError):
    """A published research revision is immutable; a correction is a new revision (FR-RSH-002)."""

    def __init__(self, revision_id: str) -> None:
        self.revision_id = revision_id
        super().__init__(
            f"published research revision '{revision_id}' is immutable",
            code="research.revision.immutable",
        )


class TenantScopeMissing(ResearchError):  # noqa: N818 canonical error name
    """The authenticated request carried no tenant scope (rule 50, deny-by-default)."""

    def __init__(self) -> None:
        super().__init__(
            "tenant scope is required and must come from the authenticated context",
            code="research.tenant.missing",
        )


class HypothesisWithoutQuestion(ResearchError):  # noqa: N818 canonical error name
    """A hypothesis was built without linking to a research question (FR-RSH-001).

    A hypothesis is always an answer to a specific research question; the domain rejects a
    hypothesis whose ``question_id`` is empty so an orphan hypothesis can never be constructed or
    persisted.
    """

    def __init__(self) -> None:
        super().__init__(
            "a hypothesis must link to a research question (question_id is required)",
            code="research.hypothesis.no_question",
        )


class RoleNotPermitted(ResearchError):  # noqa: N818 canonical error name
    """A contributor's project role does not permit a role-gated action (deny-by-default, LAW-19).

    Membership is role-scoped: an action is refused unless the acting subject holds one of the
    roles the action requires. A subject with no membership (``role is None``) is always refused.
    """

    def __init__(self, action: str, role: object | None) -> None:
        self.action = action
        self.role = role
        held = getattr(role, "value", role)
        super().__init__(
            f"role {held!r} may not perform '{action}'",
            code="research.role.denied",
        )


class IllegalReviewTransition(ResearchError):  # noqa: N818 canonical error name
    """A peer-review state transition is not legal from the current state (deterministic)."""

    def __init__(self, current: object, action: object) -> None:
        self.current = getattr(current, "value", current)
        self.action = getattr(action, "value", action)
        super().__init__(
            f"review action '{self.action}' is not legal from state '{self.current}'",
            code="research.review.illegal_transition",
        )


class ReviewAuthorizationDenied(ResearchError):  # noqa: N818 canonical error name
    """A peer-review action was attempted by a subject not authorized for it (deny-by-default).

    Only an assigned reviewer may drive a review action (start/request-revisions/accept/reject);
    only an author may submit or resubmit. Any other subject is refused.
    """

    def __init__(self, action: object, reason: str) -> None:
        self.action = getattr(action, "value", action)
        super().__init__(
            f"review action '{self.action}' denied: {reason}",
            code="research.review.denied",
        )


class SimulationNotResolved(ResearchError):  # noqa: N818 canonical error name
    """A document tried to link a simulation the simulation module could not resolve (fail closed).

    Research links a simulation only by its identity through a port; if the port cannot resolve the
    (simulation_id, version) the link is refused rather than recording a dangling reference.
    """

    def __init__(self, simulation_id: str, version: str) -> None:
        self.simulation_id = simulation_id
        self.version = version
        super().__init__(
            f"simulation '{simulation_id}' (version '{version}') could not be resolved",
            code="research.simulation.unresolved",
        )
