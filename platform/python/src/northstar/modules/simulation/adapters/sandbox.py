"""Reference in-process sandbox executor implementing :class:`RuntimeExecutorPort` (EVAL-SEC-010).

This is ONE reference runtime tier (docs/15 §3 R0/R1-style deterministic in-process execution); a
real container/microVM runtime is an adapter swap behind the same port. It executes a simulation as
a deterministic interpretation of the run inputs' ``actions`` list and enforces, itself, every
runtime control so the defenses — not the caller — keep the security metrics at zero:

* **Deny-by-default egress / SSRF (FR-SIM-003, EVAL-SEC-005):** an ``egress`` action to a
  destination NOT on the policy allowlist is refused and the run fails closed.
* **Sandbox isolation (EVAL-SEC-010):** the executor is constructed with NO secret store, NO app
  credential and NO scoring key. Any ``read_secret`` / ``read_scoring_key`` / ``host_exec`` escape
  attempt fails closed (it has nothing to read); it can never reach a non-allowlisted destination.
* **Quotas (FR-SIM-003, EVAL-SIM-001):** CPU/memory/time/step/output limits are enforced; the first
  breach terminates the run and the cause is recorded.

The returned :class:`ExecutionOutcome` transcript contains only sanitized action records — never a
secret or the scoring key — which the control plane hash-chains into immutable evidence.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..application.ports import ExecutionOutcome
from ..domain.model import Lease, RuntimePolicy, SimulationDefinition

RUNTIME_VERSION = "reference-inproc/1.0.0"

# Action types that attempt to break out of the sandbox surface — all fail closed.
_ESCAPE_ACTIONS = frozenset(
    {"read_secret", "read_scoring_key", "host_exec", "read_env", "mount", "raw_socket"}
)


class SandboxExecutor:
    """Deterministic in-process sandbox tier (holds no credentials, no secrets, no scoring key)."""

    __slots__ = ()

    def execute(
        self,
        *,
        definition: SimulationDefinition,
        policy: RuntimePolicy,
        lease: Lease,
        inputs: Mapping[str, object],
    ) -> ExecutionOutcome:
        quota = lease.quota
        raw_actions = inputs.get("actions", ())
        actions: list[Mapping[str, object]] = []
        recorded: list[Mapping[str, object]] = []

        steps_used = 0
        cpu_used = 0
        output_bytes = 0
        peak_memory = 0
        wall_time = 0
        output_parts: list[str] = []
        status = "completed"
        termination_reason: str | None = None

        if isinstance(raw_actions, (list, tuple)):
            actions = [a for a in raw_actions if isinstance(a, Mapping)]

        for action in actions:
            # Step quota: enforced BEFORE the action runs so an over-long program is capped.
            if steps_used + 1 > quota.max_steps:
                status = "terminated_quota"
                termination_reason = "max_steps"
                recorded.append(
                    {"action": "quota.terminated", "quota": "max_steps", "limit": quota.max_steps}
                )
                break
            steps_used += 1

            action_type = str(action.get("type", "compute"))

            if action_type in _ESCAPE_ACTIONS:
                # Fail closed: the sandbox has nothing to disclose and cannot reach the host.
                status = "escape_blocked"
                termination_reason = action_type
                recorded.append({"action": "escape.blocked", "attempt": action_type})
                break

            if action_type == "egress":
                destination = str(action.get("destination", ""))
                if not policy.permits_egress(destination):
                    status = "denied_egress"
                    termination_reason = "egress_not_allowlisted"
                    recorded.append({"action": "egress.denied", "destination": destination})
                    break
                recorded.append({"action": "egress.allowed", "destination": destination})
                continue

            # A normal compute step: accumulate and enforce cpu/memory/time/output quotas.
            cpu_used += max(0, int(action.get("cpu", 1)))
            wall_time += max(0, int(action.get("duration", 1)))
            peak_memory = max(peak_memory, int(action.get("alloc_mb", 0)))
            produced = str(action.get("output", ""))
            output_bytes += len(produced.encode("utf-8"))

            breach = _first_quota_breach(
                cpu_used=cpu_used,
                wall_time=wall_time,
                peak_memory=peak_memory,
                output_bytes=output_bytes,
                policy=policy,
            )
            if breach is not None:
                quota_name, limit, used = breach
                status = "terminated_quota"
                termination_reason = quota_name
                recorded.append(
                    {
                        "action": "quota.terminated",
                        "quota": quota_name,
                        "limit": limit,
                        "used": used,
                    }
                )
                break

            output_parts.append(produced)
            recorded.append({"action": "compute", "bytes": len(produced.encode("utf-8"))})

        return ExecutionOutcome(
            status=status,
            runtime_version=RUNTIME_VERSION,
            actions=tuple(recorded),
            output="".join(output_parts),
            steps_used=steps_used,
            cpu_used=cpu_used,
            output_bytes=output_bytes,
            termination_reason=termination_reason,
        )


def _first_quota_breach(
    *,
    cpu_used: int,
    wall_time: int,
    peak_memory: int,
    output_bytes: int,
    policy: RuntimePolicy,
) -> tuple[str, int, int] | None:
    quota = policy.quota
    if cpu_used > quota.cpu_millis:
        return ("cpu_millis", quota.cpu_millis, cpu_used)
    if wall_time > quota.wall_time_seconds:
        return ("wall_time_seconds", quota.wall_time_seconds, wall_time)
    if peak_memory > quota.memory_mb:
        return ("memory_mb", quota.memory_mb, peak_memory)
    if output_bytes > quota.max_output_bytes:
        return ("max_output_bytes", quota.max_output_bytes, output_bytes)
    return None
