"""Assemble the reference tutorial product by COMPOSING the released module composition root.

The product adds NO new capability and forks NO kernel/module source (ARCH-024, LAW-04). It reuses
:func:`northstar.processes.api.wiring.build_dependencies` — the single authoritative composition
root that wires all released modules onto one kernel command/query bus, one deny-by-default policy
engine and one tamper-evident audit recorder — and pairs it with the declarative
:data:`REFERENCE_PRODUCT_PROFILE`. Everything the product does flows through those buses (the same
public capabilities the API/Studio/CLI use), so there is exactly one authoritative implementation
per action and no persistence bypass.
"""

from __future__ import annotations

from dataclasses import dataclass

from northstar.kernel.messaging import CommandBus, QueryBus
from northstar.processes.api.wiring import build_dependencies

from .profile import REFERENCE_PRODUCT_PROFILE, ReferenceProductProfile


@dataclass(frozen=True, slots=True)
class AssembledReferenceProduct:
    """A running reference product: the declared profile + the composed authoritative buses.

    ``command_bus``/``query_bus`` are the SAME kernel buses the released composition root builds;
    the product neither wraps them with new logic nor registers its own capabilities.
    """

    profile: ReferenceProductProfile
    command_bus: CommandBus
    query_bus: QueryBus


def assemble_reference_product(
    *,
    database_url: str | None = None,
    profile: ReferenceProductProfile = REFERENCE_PRODUCT_PROFILE,
) -> AssembledReferenceProduct:
    """Compose the released modules and bind them to the reference product profile.

    ``database_url`` selects the target database (an ephemeral test database in journeys; the
    configured database in a real deployment). No schema/migration work happens here — assembly
    uses the existing migrated schemas and released capabilities only.
    """
    dependencies = build_dependencies(database_url=database_url)
    return AssembledReferenceProduct(
        profile=profile,
        command_bus=dependencies.command_bus,
        query_bus=dependencies.query_bus,
    )
