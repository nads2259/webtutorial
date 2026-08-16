"""Command bus: the single authoritative write path (LAW-04, ARCH-004, FR-KRN-002).

A command is authorized deny-by-default (rule 50), then — and only then — routed to its
registered capability through the :class:`CapabilityDispatcher` (never an alternate route),
executed, and recorded as tamper-evident audit evidence (LAW-14). Replaying a command with
the same idempotency key returns the prior result without re-executing the handler, so an
effect is never duplicated. Denials and handler failures are distinguished from success and
each leave their own audit outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..audit.ports import AuditOutcome, AuditRecord, AuditRecorderPort
from ..capabilities.registry import CapabilityDispatcher
from ..context import RequestContext, ResourceRef
from ..errors import PolicyDenied
from ..policy.ports import PolicyDecision, PolicyDecisionPort

_COMMAND_EVENT_TYPE = "northstar.kernel.command.executed"


@dataclass(frozen=True, slots=True)
class Command:
    """A request to change state, routed to one registered capability (name + version).

    ``policy_action`` defaults to the capability name so authorization and dispatch stay
    aligned; override ``action`` when the policy action differs from the capability name.
    """

    capability: str
    version: str
    payload: object = None
    resource: ResourceRef | None = None
    action: str | None = None

    @property
    def policy_action(self) -> str:
        return self.action or self.capability


@dataclass(frozen=True, slots=True)
class CommandInvocation:
    """The typed request object a capability handler receives (context + payload)."""

    context: RequestContext
    command: Command

    @property
    def payload(self) -> object:
        return self.command.payload


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of a successful (or replayed) command execution.

    ``value`` is the handler's return; ``decision`` and ``audit`` are the authorization and
    evidence records; ``replayed`` is ``True`` when returned from the idempotency cache.
    """

    value: object
    decision: PolicyDecision
    audit: AuditRecord
    replayed: bool = False


class CommandBus:
    """Authorizes, dispatches, audits and de-duplicates commands (one authoritative path)."""

    def __init__(
        self,
        dispatcher: CapabilityDispatcher,
        policy: PolicyDecisionPort,
        audit: AuditRecorderPort,
    ) -> None:
        self._dispatcher = dispatcher
        self._policy = policy
        self._audit = audit
        self._idempotency: dict[str, CommandResult] = {}

    def dispatch(self, command: Command, context: RequestContext) -> CommandResult:
        """Run the command through the authoritative pipeline and return its result.

        Raises :class:`PolicyDenied` when policy denies the action, and re-raises any handler
        error after recording a ``failed`` audit outcome.
        """
        key = context.idempotency_key
        if key is not None and key in self._idempotency:
            return replace(self._idempotency[key], replayed=True)

        decision = self._policy.decide(context, command.policy_action, command.resource)
        if not decision.allowed:
            self._audit.record(
                event_type=_COMMAND_EVENT_TYPE,
                actor=context.actor,
                action=command.policy_action,
                outcome=AuditOutcome.DENIED,
                correlation_id=context.correlation_id,
                resource=command.resource,
                decision_ref=decision.decision_id,
                reason_codes=decision.reason_codes,
            )
            raise PolicyDenied(
                action=command.policy_action,
                decision_id=decision.decision_id,
                reasons=tuple((r.code, r.message) for r in decision.reasons),
            )

        try:
            value = self._dispatcher.dispatch(
                command.capability,
                command.version,
                CommandInvocation(context=context, command=command),
            )
        except Exception:
            self._audit.record(
                event_type=_COMMAND_EVENT_TYPE,
                actor=context.actor,
                action=command.policy_action,
                outcome=AuditOutcome.FAILED,
                correlation_id=context.correlation_id,
                resource=command.resource,
                decision_ref=decision.decision_id,
                reason_codes=decision.reason_codes,
            )
            raise

        audit = self._audit.record(
            event_type=_COMMAND_EVENT_TYPE,
            actor=context.actor,
            action=command.policy_action,
            outcome=AuditOutcome.SUCCESS,
            correlation_id=context.correlation_id,
            resource=command.resource,
            decision_ref=decision.decision_id,
            reason_codes=decision.reason_codes,
        )
        result = CommandResult(value=value, decision=decision, audit=audit)
        if key is not None:
            self._idempotency[key] = result
        return result
