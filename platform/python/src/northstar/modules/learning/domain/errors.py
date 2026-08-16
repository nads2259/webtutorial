"""Typed learning domain errors (rule 30/40): explainable, deterministic diagnostics.

The learning domain raises these typed errors rather than bare strings; adapters map them to
RFC 9457 problem details at the trust boundary (rule 40). The kernel error base carries the
structured diagnostics.
"""

from __future__ import annotations

from northstar.kernel.errors import Diagnostic, KernelError


class LearningError(KernelError):
    """Base class for learning & assessment domain errors."""


class LearningValidationError(LearningError):
    """A learning aggregate violates a structural invariant (deny-by-default)."""

    def __init__(self, message: str, code: str = "learning.invalid") -> None:
        super().__init__(message, (Diagnostic(code=code, message=message),))


class UnpublishedRevisionError(LearningError):
    """A course tried to compose a knowledge revision that is not PUBLISHED (FR-LRN-001).

    A course composes PUBLISHED knowledge revisions only; referencing a draft/unknown revision is
    rejected so a course can never expose unpublished content.
    """

    def __init__(self, revision_id: str) -> None:
        message = f"revision {revision_id!r} is not a published knowledge revision"
        super().__init__(
            message, (Diagnostic(code="learning.revision.unpublished", message=message),)
        )
        self.revision_id = revision_id


class UnknownBlockError(LearningError):
    """A section referenced a block id absent from its published revision (FR-LRN-001)."""

    def __init__(self, block_id: str, revision_id: str) -> None:
        message = f"block {block_id!r} is not a stable block of revision {revision_id!r}"
        super().__init__(message, (Diagnostic(code="learning.block.unknown", message=message),))
        self.block_id = block_id


class PositionNotInCourse(LearningError):  # noqa: N818 canonical error name
    """A resume/overlay position does not resolve to a section/block of the course (FR-LRN-002)."""

    def __init__(
        self, message: str = "position does not resolve to a course section/block"
    ) -> None:
        super().__init__(message, (Diagnostic(code="learning.position.invalid", message=message),))


class CourseNotFound(LearningError):  # noqa: N818 canonical error name
    """A referenced course does not exist in this tenant (deny-by-default)."""

    def __init__(self, course_id: str) -> None:
        message = f"course {course_id!r} was not found"
        super().__init__(message, (Diagnostic(code="learning.course.not_found", message=message),))
        self.course_id = course_id


class ItemImmutableError(LearningError):
    """An assessment item version used in a scored attempt is IMMUTABLE (FR-LRN-004).

    Once an item version has scored an attempt it is sealed: re-publishing that exact
    (item_id, version) with different content is rejected so a scored attempt's basis can never
    change retroactively.
    """

    def __init__(self, item_id: str, version: str) -> None:
        message = (
            f"assessment item {item_id!r} version {version!r} is sealed (used in a scored attempt) "
            "and cannot be re-published with different content"
        )
        super().__init__(message, (Diagnostic(code="learning.item.immutable", message=message),))
        self.item_id = item_id
        self.version = version


class ItemNotFound(LearningError):  # noqa: N818 canonical error name
    """A referenced assessment item/version does not exist in this tenant (deny-by-default)."""

    def __init__(self, item_id: str, version: str) -> None:
        message = f"assessment item {item_id!r} version {version!r} was not found"
        super().__init__(message, (Diagnostic(code="learning.item.not_found", message=message),))
        self.item_id = item_id
        self.version = version


class AttemptRejected(LearningError):  # noqa: N818 canonical error name
    """An attempt violates the item's integrity policy (max attempts / closed item)."""

    def __init__(self, reason: str) -> None:
        message = f"assessment attempt rejected: {reason}"
        super().__init__(message, (Diagnostic(code="learning.attempt.rejected", message=reason),))
        self.reason = reason


class ConsentRequired(LearningError):  # noqa: N818 canonical error name
    """A personalized recommendation was requested without the learner's consent (FR-LRN-007)."""

    def __init__(self) -> None:
        message = "personalized recommendations require the learner's consent"
        super().__init__(message, (Diagnostic(code="learning.consent.required", message=message),))


class ProfileFeatureNotFound(LearningError):  # noqa: N818 canonical error name
    """A correction targeted a profile feature that does not exist (EVAL-PRIV-004)."""

    def __init__(self, feature: str) -> None:
        message = f"inferred profile feature {feature!r} was not found"
        super().__init__(message, (Diagnostic(code="learning.profile.not_found", message=message),))
        self.feature = feature


class AnonymousProgressClaimed(LearningError):  # noqa: N818 canonical error name
    """An anonymous-progress source is already claimed by a DIFFERENT subject (UX-010, LAW-08).

    Anonymous progress may be merged into an authenticated account exactly once: once a subject has
    claimed an anonymous record, another subject can never merge it, so one learner can never absorb
    another's anonymous progress (cross-owner refusal, deny-by-default).
    """

    def __init__(self, anonymous_id: str) -> None:
        message = (
            f"anonymous progress {anonymous_id!r} is already claimed by another subject and "
            "cannot be merged again"
        )
        super().__init__(message, (Diagnostic(code="learning.merge.claimed", message=message),))
        self.anonymous_id = anonymous_id


class TenantScopeMissing(LearningError):  # noqa: N818 canonical error name
    """A tenant-scoped operation was invoked without an authenticated tenant scope (fail closed)."""

    def __init__(self) -> None:
        message = "a tenant scope is required for this operation"
        super().__init__(message, (Diagnostic(code="learning.tenant.missing", message=message),))
