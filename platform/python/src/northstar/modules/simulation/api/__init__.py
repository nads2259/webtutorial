"""Simulation HTTP inbound adapter (thin, over the kernel buses)."""

from .router import (
    SimulationApiDependencies,
    bind_simulation_dependencies,
    create_simulation_router,
)

__all__ = [
    "SimulationApiDependencies",
    "bind_simulation_dependencies",
    "create_simulation_router",
]
